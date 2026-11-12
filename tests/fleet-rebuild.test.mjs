import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { GitHub, Registry, BASE_LABEL, repositories, validateLedger, attributable, reconcile, fleet } from '../scripts/fleet-ci-image-rebuild.mjs';

const REPO = 'Rubio-Enterprises/example';
const SOURCE = 'Rubio-Enterprises/.github';
const DIGEST = `sha256:${'a'.repeat(64)}`;
const OLD = `sha256:${'b'.repeat(64)}`;
const ACTOR = 'rubio-renovate[bot]';
const ledger = (digest = OLD, runId = 101) => ({ version: 1, baseDigest: digest, dispatches: [{ repository: REPO, runId }] });
const run = (overrides = {}) => ({ id: 101, status: 'queued', event: 'workflow_dispatch', head_branch: 'master', path: '.github/workflows/standards.yml',
  repository: { full_name: REPO }, actor: { login: ACTOR }, triggering_actor: { login: ACTOR }, ...overrides });

function harness({ current = false, active = run(), supportsInput = false, cancelError, dispatchError, registryError = false, repo = {}, repoError } = {}) {
  const calls = [], logs = [];
  const github = { async api(endpoint, body) {
    calls.push({ endpoint, body });
    if (endpoint === `repos/${REPO}`) {
      if (repoError) throw new Error(`GitHub ${repoError}: ${endpoint}`, { cause: repoError });
      return { private: true, default_branch: 'master', ...repo };
    }
    if (endpoint.endsWith('/cancel')) {
      if (cancelError) throw new Error('cancel failed', { cause: cancelError });
      active = { ...active, status: 'completed' }; return {};
    }
    if (endpoint.endsWith('/runs/101')) return active;
    if (endpoint.includes('/contents/')) return { content: Buffer.from(supportsInput
      ? 'on:\n  workflow_dispatch:\n    inputs:\n      base-digest:\n        type: string\n'
      : 'on:\n  workflow_dispatch:\n').toString('base64') };
    if (endpoint.endsWith('/dispatches')) {
      if (dispatchError) throw new Error('dispatch failed');
      return { workflow_run_id: 202 };
    }
    throw new Error(`Unexpected API call: ${endpoint}`);
  }, async list() { return [active]; } };
  const registry = { async current() { if (registryError) throw new Error('403'); return current; } };
  const options = { github, registry, repository: REPO, digest: DIGEST, ledgers: [ledger()], actor: ACTOR,
    sleep: async () => {}, log: text => logs.push(text) };
  return { options, calls, logs };
}

test('inventory is validated before any mutation and deduplicates size admissions', () => {
  assert.deepEqual(repositories({ entries: [{ repository: REPO }, { repository: REPO }] }), [REPO]);
  for (const value of [{}, { entries: [] }, { entries: [{ repository: 'other/repo' }] }, { entries: [{ repository: `${REPO}\n::error::injected` }] }]) {
    assert.throws(() => repositories(value));
  }
});

test('ledger rejects malformed digests, paths and run IDs', () => {
  assert.deepEqual(validateLedger(ledger()), ledger());
  for (const value of [{ ...ledger(), version: 2 }, { ...ledger(), baseDigest: '$(touch injected)' },
    { ...ledger(), dispatches: [{ repository: '../escape', runId: 101 }] },
    { ...ledger(), dispatches: [{ repository: REPO, runId: '101' }] }]) assert.throws(() => validateLedger(value));
});

test('only exact workflow, actor, event, default branch and repository are attributable', () => {
  assert.equal(attributable(run(), REPO, 'master', ACTOR), true);
  for (const overrides of [{ event: 'push' }, { event: 'schedule' }, { head_branch: 'feature/manual' }, { path: '.github/workflows/other.yml' },
    { actor: { login: 'owner' } }, { triggering_actor: { login: 'owner' } }, { repository: { full_name: 'other/repo' } }]) {
    assert.equal(attributable(run(overrides), REPO, 'master', ACTOR), false);
  }
});

for (const status of ['queued', 'in_progress', 'waiting', 'pending', 'requested']) test(`cancels ${status} old-base run before dispatching on master`, async () => {
  const h = harness({ active: run({ status }) });
  assert.deepEqual(await reconcile(h.options), { repository: REPO, runId: 202 });
  const cancel = h.calls.findIndex(row => row.endpoint.endsWith('/cancel'));
  const dispatch = h.calls.findIndex(row => row.endpoint.endsWith('/dispatches'));
  assert.ok(cancel >= 0 && cancel < dispatch);
  assert.deepEqual(h.calls[dispatch].body, { ref: 'master', return_run_details: true });
});

const ineligible = [
  { reason: 'public', repo: { private: false } },
  { reason: 'archived', repo: { private: true, archived: true } },
  { reason: 'disabled', repo: { private: true, disabled: true } },
];

for (const { reason, repo } of ineligible) test(`skips ${reason} repository before cancelling or dispatching`, async () => {
  const h = harness({ repo });
  assert.equal(await reconcile(h.options), null);
  assert.deepEqual(h.calls, [{ endpoint: `repos/${REPO}`, body: undefined }]);
  assert.equal(h.logs.length, 1);
  assert.ok(h.logs[0].startsWith(`::notice::Skip ${REPO}: `));
  assert.ok(h.logs[0].includes(reason));
});

test('sends base-digest only to a caller that declares the input', async () => {
  const h = harness({ supportsInput: true });
  await reconcile(h.options);
  assert.equal(h.calls.find(row => row.endpoint.endsWith('/dispatches')).body.inputs['base-digest'], DIGEST);
});

test('a base-digest declared under workflow_call does not authorize a dispatch input', async () => {
  const h = harness(); const api = h.options.github.api;
  h.options.github.api = async (endpoint, body) => endpoint.includes('/contents/')
    ? { content: Buffer.from('on:\n  workflow_dispatch:\n  workflow_call:\n    inputs:\n      base-digest:\n        type: string\n').toString('base64') }
    : api(endpoint, body);
  await reconcile(h.options);
  assert.equal(h.calls.find(row => row.endpoint.endsWith('/dispatches')).body.inputs, undefined);
});

test('same-base active fleet run is idempotent without new work', async () => {
  const h = harness(); h.options.ledgers = [ledger(DIGEST)];
  assert.equal(await reconcile(h.options), null);
  assert.ok(!h.calls.some(row => row.body));
  assert.match(h.logs.join('\n'), /same-base fleet run already active/);
});

test('already-current images still cancel obsolete work, then skip dispatch', async () => {
  const h = harness({ current: true });
  assert.equal(await reconcile(h.options), null);
  assert.ok(h.calls.some(row => row.endpoint.endsWith('/cancel')));
  assert.ok(!h.calls.some(row => row.endpoint.endsWith('/dispatches')));
  assert.match(h.logs.join('\n'), /admitted image already current/);
});

test('unlabelled or inaccessible images rebuild rather than false-positive current', async () => {
  const h = harness({ registryError: true });
  assert.deepEqual(await reconcile(h.options), { repository: REPO, runId: 202 });
  assert.match(h.logs.join('\n'), /Cannot read image or publication record/);
});

test('private package denial can skip via a verified publication record instead', async () => {
  const h = harness({ registryError: true });
  h.options.github.admittedCurrent = async () => true;
  assert.equal(await reconcile(h.options), null);
  assert.ok(!h.calls.some(row => row.endpoint.endsWith('/dispatches')));
});

test('admission fallback binds latest default-branch run, central reusable, revision and image', async () => {
  const github = new GitHub('secret');
  const latest = { id: 200, event: 'push', head_branch: 'main', head_repository: { full_name: REPO }, head_sha: 'commit',
    referenced_workflows: [{ path: `${SOURCE}/.github/workflows/repository-image.yml@main` }] };
  const record = { repository: REPO, image: `ghcr.io/${REPO.toLowerCase()}/devcontainer-ci:main`, revision: 'commit', baseDigest: DIGEST, imageDigest: OLD };
  github.api = async endpoint => endpoint.includes('/workflows/standards.yml/runs?') ? { workflow_runs: [latest] } : latest;
  github.record = async () => record;
  assert.equal(await github.admittedCurrent(REPO, 'main', DIGEST, '/unused'), true);
  for (const field of ['repository', 'image', 'revision', 'imageDigest', 'baseDigest']) {
    const old = record[field]; record[field] = 'invalid';
    assert.equal(await github.admittedCurrent(REPO, 'main', DIGEST, '/unused'), false);
    record[field] = old;
  }
  latest.event = 'pull_request';
  assert.equal(await github.admittedCurrent(REPO, 'main', DIGEST, '/unused'), false);
  latest.event = 'push'; latest.referenced_workflows = [];
  assert.equal(await github.admittedCurrent(REPO, 'main', DIGEST, '/unused'), false);
});

test('unrecorded App manual runs are never discovered or cancelled by actor/time guesses', async () => {
  const h = harness(); h.options.ledgers = [];
  await reconcile(h.options);
  assert.ok(!h.calls.some(row => row.endpoint.includes('/runs/')));
});

test('completed failed same-base run can be retried; completed old work is not cancelled', async () => {
  const h = harness({ active: run({ status: 'completed', conclusion: 'failure' }) });
  h.options.ledgers = [ledger(DIGEST)];
  assert.deepEqual(await reconcile(h.options), { repository: REPO, runId: 202 });
  assert.ok(!h.calls.some(row => row.endpoint.endsWith('/cancel')));
});

test('cancellation failure or attribution mismatch stops that repository before dispatch', async () => {
  for (const config of [{ cancelError: 403 }, { cancelError: 409 }, { active: run({ actor: { login: 'owner' } }) }]) {
    const h = harness(config);
    await assert.rejects(reconcile(h.options));
    assert.ok(!h.calls.some(row => row.endpoint.endsWith('/dispatches')));
  }
});

test('run records returned by dispatch are required, not inferred', async () => {
  const h = harness(); const api = h.options.github.api;
  h.options.github.api = async (endpoint, body) => endpoint.endsWith('/dispatches') ? null : api(endpoint, body);
  await assert.rejects(reconcile(h.options), /outcome unknown/);
});

test('GitHub list paginates; dispatch return_run_details stays boolean', async () => {
  const calls = [];
  const github = new GitHub('secret', async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ workflow_runs: new URL(url).searchParams.get('page') === '1' ? Array(100).fill({}) : [{}] }) };
  });
  assert.equal((await github.list('repos/example/actions/runs?branch=main', 'workflow_runs')).length, 101);
  await github.api('repos/example/dispatches', { return_run_details: true });
  assert.equal(JSON.parse(calls.at(-1).options.body).return_run_details, true);
  assert.match(calls[1].url, /&page=2$/);
});

test('registry resolves amd64 child, not index or arm64; checks label and config architecture', async () => {
  const registry = new Registry('secret'); const calls = [];
  registry.get = async (repository, resource) => {
    calls.push(resource);
    if (resource === 'manifests/main') return { data: { manifests: [
      { platform: { os: 'linux', architecture: 'arm64' }, digest: OLD },
      { platform: { os: 'linux', architecture: 'amd64' }, digest: DIGEST },
    ] }, digest: OLD };
    if (resource === `manifests/${DIGEST}`) return { data: { config: { digest: OLD } } };
    return { data: { os: 'linux', architecture: 'amd64', config: { Labels: { [BASE_LABEL]: DIGEST } } } };
  };
  assert.equal(await registry.amd64('rubio-enterprises/base', 'main'), DIGEST);
  assert.equal(await registry.current(REPO, DIGEST), true);
  assert.equal(await registry.current(REPO, OLD), false);
  assert.ok(calls.includes(`blobs/${OLD}`));
});

async function fleetHarness(t, { requested = '', move = false, failFirst = false, entries = [REPO, REPO], ...repoOptions } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fleet-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const h = harness(repoOptions); const api = h.options.github.api; let registryReads = 0;
  const previous = { id: 50, created_at: '2026-01-01', status: 'in_progress', event: 'repository_dispatch' };
  const order = [];
  h.options.github.api = async (endpoint, body) => {
    order.push(endpoint);
    if (endpoint === `repos/${SOURCE}/actions/runs/100`) return { created_at: '2026-01-02' };
    if (endpoint === `repos/${SOURCE}/actions/runs/50`) return { ...previous, status: 'completed' };
    if (endpoint.includes('/mac-dev-playbook/contents/')) return { content: Buffer.from(JSON.stringify({ entries: entries.map(repository => ({ repository })) })).toString('base64') };
    if (failFirst && endpoint === `repos/${REPO}`) throw new Error('isolated repo failure');
    return api(endpoint, body);
  };
  const list = h.options.github.list;
  h.options.github.list = async endpoint => endpoint.includes(`repos/${SOURCE}/`) ? [previous, { id: 100, created_at: '2026-01-02' }] : list(endpoint);
  h.options.github.ledger = async row => { order.push(`ledger:${row.id}`); return ledger(); };
  h.options.registry.amd64 = async () => { registryReads++; return move && registryReads > 1 ? OLD : DIGEST; };
  const sleeps = [];
  const options = { ...h.options, env: { RUNNER_TEMP: directory, GITHUB_RUN_ID: '100', FLEET_ACTOR: ACTOR, BASE_DIGEST: requested }, sleep: async ms => sleeps.push(ms) };
  return { ...h, options, directory, order, sleeps };
}

test('fleet waits for cancelled receiver artifact, persists exact IDs atomically and paces unique repos', async t => {
  const h = await fleetHarness(t);
  await fleet(h.options);
  assert.ok(h.order.indexOf(`repos/${SOURCE}/actions/runs/50`) < h.order.indexOf('ledger:50'));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(h.directory, 'fleet-dispatches.json'))), ledger(DIGEST, 202));
  assert.deepEqual(h.sleeps, [15000]);
});

test('mixed inventory succeeds and records only actionable private dispatches', async t => {
  const skipped = new Map(ineligible.map(({ reason, repo }) => [`${REPO}-${reason}`, { reason, repo }]));
  const h = await fleetHarness(t, { entries: [REPO, ...skipped.keys()] });
  const api = h.options.github.api;
  h.options.github.api = async (endpoint, body) => {
    const entry = skipped.get(endpoint.replace(/^repos\//, ''));
    if (entry) return entry.repo;
    return api(endpoint, body);
  };
  await fleet(h.options);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(h.directory, 'fleet-dispatches.json'))), ledger(DIGEST, 202));
  for (const [repository, { reason }] of skipped) {
    assert.ok(h.logs.some(text => text.startsWith(`::notice::Skip ${repository}: `) && text.includes(reason)));
    assert.ok(!h.calls.some(row => row.endpoint.startsWith(`repos/${repository}/`)));
  }
  assert.equal(h.calls.filter(row => row.endpoint.endsWith('/dispatches')).length, 1);
  assert.ok(!h.logs.some(text => text.startsWith('::error::')));
});

for (const status of [403, 404]) test(`unreadable repository metadata (${status}) still fails the fleet`, async t => {
  const h = await fleetHarness(t, { repoError: status });
  await assert.rejects(fleet(h.options), /Failed repositories: 1/);
  assert.ok(h.logs.some(text => text.startsWith(`::error::${REPO}: GitHub ${status}`)));
  assert.ok(!h.logs.some(text => text.startsWith('::notice::Skip')));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(h.directory, 'fleet-dispatches.json'))).dispatches, []);
  assert.ok(!h.calls.some(row => row.body));
});

test('failed private dispatch still fails the fleet without recording a run', async t => {
  const h = await fleetHarness(t, { dispatchError: true });
  await assert.rejects(fleet(h.options), /Failed repositories: 1/);
  assert.ok(h.calls.some(row => row.endpoint.endsWith('/dispatches')));
  assert.ok(h.logs.some(text => text === `::error::${REPO}: dispatch failed`));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(h.directory, 'fleet-dispatches.json'))).dispatches, []);
});

test('failed attribution still fails the fleet before cancelling or dispatching', async t => {
  const h = await fleetHarness(t, { active: run({ actor: { login: 'owner' } }) });
  await assert.rejects(fleet(h.options), /Failed repositories: 1/);
  assert.ok(h.logs.some(text => text.includes('failed attribution')));
  assert.ok(!h.calls.some(row => row.body));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(h.directory, 'fleet-dispatches.json'))).dispatches, []);
});

test('superseded payload does nothing and payload injection is rejected', async t => {
  for (const requested of [OLD, '$(touch injected)']) {
    const h = await fleetHarness(t, { requested });
    if (requested === OLD) await fleet(h.options);
    else await assert.rejects(fleet(h.options), /sha256/);
    assert.ok(!h.calls.some(row => row.body));
  }
});

test('reruns cannot overwrite an earlier attempt ledger; replay must be a fresh dispatch', async t => {
  const h = await fleetHarness(t); h.options.env.GITHUB_RUN_ATTEMPT = '2';
  await assert.rejects(fleet(h.options), /fresh workflow_dispatch/);
  assert.equal(h.order.length, 0);
});

test('registry movement stops a paced loop even before the newer event is received', async t => {
  const h = await fleetHarness(t, { move: true });
  await fleet(h.options);
  assert.ok(!h.calls.some(row => row.body));
  assert.match(h.logs.join('\n'), /Base moved/);
});

test('per-repository failures fail the fleet but leave a readable attribution artifact', async t => {
  const h = await fleetHarness(t, { failFirst: true });
  await assert.rejects(fleet(h.options), /Failed repositories: 1/);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(h.directory, 'fleet-dispatches.json'))).dispatches, []);
});
