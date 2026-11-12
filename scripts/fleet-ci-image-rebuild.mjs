import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const OWNER = 'Rubio-Enterprises';
const SOURCE = `${OWNER}/.github`;
const WORKFLOW = 'fleet-ci-image-rebuild.yml';
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const REPOSITORY = /^Rubio-Enterprises\/[A-Za-z0-9_.-]+$/;
const BASE = 'rubio-enterprises/base';
export const BASE_LABEL = 'org.opencontainers.image.base.digest';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export class GitHub {
  constructor(token, request = fetch) { this.token = token; this.request = request; }
  async api(endpoint, body) {
    const response = await this.request(`https://api.github.com/${endpoint}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { authorization: `Bearer ${this.token}`, accept: 'application/vnd.github+json', 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new Error(`GitHub ${response.status}: ${endpoint}`, { cause: response.status });
    return response.status === 204 ? null : response.json();
  }
  async list(endpoint, key) {
    const rows = [];
    for (let page = 1; ; page++) {
      const result = await this.api(`${endpoint}${endpoint.includes('?') ? '&' : '?'}per_page=100&page=${page}`);
      rows.push(...result[key]);
      if (result[key].length < 100) return rows;
    }
  }
  async ledger(run, directory) {
    return this.record(SOURCE, run.id, 'fleet-dispatches', 'fleet-dispatches.json', directory);
  }
  async admittedCurrent(repository, branch, digest, directory) {
    const result = await this.api(`repos/${repository}/actions/workflows/standards.yml/runs?branch=${encodeURIComponent(branch)}&per_page=1`);
    const latest = result.workflow_runs[0];
    if (!latest || !['push', 'schedule', 'workflow_dispatch'].includes(latest.event) || latest.head_branch !== branch
        || latest.head_repository?.full_name !== repository) return false;
    const run = await this.api(`repos/${repository}/actions/runs/${latest.id}`);
    if (!run.referenced_workflows?.some(row => row.path === `${SOURCE}/.github/workflows/repository-image.yml@main`)) return false;
    const record = await this.record(repository, latest.id, 'repository-image-admission', 'repository-image-admission.json', directory);
    return record?.repository === repository && record.image === `ghcr.io/${repository.toLowerCase()}/devcontainer-ci:main`
      && record.revision === run.head_sha && DIGEST.test(record.imageDigest) && record.baseDigest === digest;
  }
  async record(repository, runId, name, filename, directory) {
    const artifacts = await this.list(`repos/${repository}/actions/runs/${runId}/artifacts`, 'artifacts');
    const artifact = artifacts.find(row => row.name === name && !row.expired);
    if (!artifact) return null;
    if (artifact.size_in_bytes > 1024 * 1024) throw new Error('Publication record archive exceeds size limit');
    const download = fs.mkdtempSync(path.join(directory, 'fleet-record-'));
    try {
      execFileSync('gh', ['run', 'download', String(runId), '--repo', repository, '--name', name, '--dir', download], {
        env: { ...process.env, GH_TOKEN: this.token }, stdio: 'pipe', maxBuffer: 1024 * 1024,
      });
      const file = path.join(download, filename);
      if (fs.statSync(file).size > 1024 * 1024) throw new Error('Publication record exceeds size limit');
      return JSON.parse(fs.readFileSync(file, 'utf8'));
    } finally { fs.rmSync(download, { recursive: true, force: true }); }
  }
}

export class Registry {
  constructor(token, request = fetch) { this.token = token; this.request = request; }
  async get(repository, resource) {
    const auth = await this.request(`https://ghcr.io/token?service=ghcr.io&scope=repository:${repository}:pull`, {
      headers: { authorization: `Basic ${Buffer.from(`token:${this.token}`).toString('base64')}` },
    });
    if (!auth.ok) throw new Error(`Registry authorization failed: ${auth.status}`);
    const { token } = await auth.json();
    const response = await this.request(`https://ghcr.io/v2/${repository}/${resource}`, {
      headers: { authorization: `Bearer ${token}`, accept: 'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json' },
    });
    if (!response.ok) throw new Error(`Registry read failed: ${response.status}`);
    return { data: await response.json(), digest: response.headers.get('docker-content-digest') };
  }
  async amd64(repository, reference) {
    const result = await this.get(repository, `manifests/${reference}`);
    const digest = result.data.manifests
      ? result.data.manifests.find(row => row.platform?.os === 'linux' && row.platform?.architecture === 'amd64')?.digest
      : result.digest;
    if (!DIGEST.test(digest)) throw new Error('Registry did not return a linux/amd64 digest');
    return digest;
  }
  async current(repository, digest) {
    const image = `${repository.toLowerCase()}/devcontainer-ci`;
    const child = await this.amd64(image, 'main');
    const manifest = await this.get(image, `manifests/${child}`);
    const configDigest = manifest.data.config?.digest;
    if (!DIGEST.test(configDigest)) throw new Error('Invalid CI image config digest');
    const config = await this.get(image, `blobs/${configDigest}`);
    return config.data.os === 'linux' && config.data.architecture === 'amd64' && config.data.config?.Labels?.[BASE_LABEL] === digest;
  }
}

export function repositories(inventory) {
  const names = inventory.entries?.map(row => row.repository);
  if (!names?.length || names.some(name => !REPOSITORY.test(name))) throw new Error('Invalid admission inventory');
  return [...new Set(names)].sort();
}

export function validateLedger(ledger) {
  if (ledger.version !== 1 || !DIGEST.test(ledger.baseDigest) || !Array.isArray(ledger.dispatches)
      || ledger.dispatches.some(row => !REPOSITORY.test(row.repository) || !Number.isSafeInteger(row.runId) || row.runId <= 0)) {
    throw new Error('Invalid fleet dispatch ledger');
  }
  return ledger;
}

export function attributable(run, repository, branch, actor) {
  return run.repository?.full_name === repository && run.event === 'workflow_dispatch'
    && run.head_branch === branch && run.path === '.github/workflows/standards.yml'
    && run.actor?.login === actor && run.triggering_actor?.login === actor;
}

export async function waitCompleted(github, repository, id, sleep = delay) {
  for (let attempt = 0; attempt < 60; attempt++) {
    const run = await github.api(`repos/${repository}/actions/runs/${id}`);
    if (run.status === 'completed') return run;
    await sleep(2000);
  }
  throw new Error(`Run ${repository}/${id} did not finish cancelling`);
}

export async function reconcile({ github, registry, repository, digest, ledgers, actor, directory, sleep = delay, log = console.log }) {
  const repo = await github.api(`repos/${repository}`);
  const reason = repo.archived ? 'archived' : repo.disabled ? 'disabled' : !repo.private ? 'public' : null;
  if (reason) {
    log(`::notice::Skip ${repository}: ${reason} repository has no CI image to rebuild`);
    return null;
  }
  let sameDigestActive = false;
  const recorded = ledgers.some(ledger => ledger.dispatches.some(row => row.repository === repository));
  const active = new Set(recorded ? (await github.list(`repos/${repository}/actions/workflows/standards.yml/runs?branch=${encodeURIComponent(repo.default_branch)}&event=workflow_dispatch&actor=${encodeURIComponent(actor)}`, 'workflow_runs'))
    .filter(row => row.status !== 'completed').map(row => row.id) : []);
  const seen = new Set();
  for (const ledger of ledgers) {
    for (const entry of ledger.dispatches.filter(row => row.repository === repository)) {
      if (seen.has(entry.runId) || !active.has(entry.runId)) continue;
      seen.add(entry.runId);
      const endpoint = `repos/${repository}/actions/runs/${entry.runId}`;
      let run;
      try { run = await github.api(endpoint); }
      catch (error) { if (error.cause === 404) continue; throw error; }
      if (run.status === 'completed') continue;
      if (!attributable(run, repository, repo.default_branch, actor)) throw new Error(`Ledger run ${entry.runId} failed attribution`);
      if (ledger.baseDigest === digest) { sameDigestActive = true; continue; }
      log(`Cancel superseded ${repository} run ${entry.runId} (${ledger.baseDigest})`);
      try { await github.api(`${endpoint}/cancel`, {}); }
      catch (error) { if (error.cause !== 409) throw error; }
      await waitCompleted(github, repository, entry.runId, sleep);
    }
  }
  let current = false;
  try { current = await registry.current(repository, digest); }
  catch {
    // GITHUB_TOKEN cannot read another private repository's package without a package grant
    try { current = await github.admittedCurrent(repository, repo.default_branch, digest, directory); }
    catch { log(`::notice::Cannot read image or publication record for ${repository}; rebuild rather than assume it is current`); }
  }
  if (current || sameDigestActive) {
    log(`::notice::Skip ${repository}: ${current ? 'admitted image already current' : 'same-base fleet run already active'}`);
    return null;
  }
  const workflow = await github.api(`repos/${repository}/contents/.github/workflows/standards.yml?ref=${encodeURIComponent(repo.default_branch)}`);
  const source = Buffer.from(workflow.content, 'base64').toString('utf8');
  // GitHub rejects undeclared dispatch inputs. Legacy callers use exact run IDs in the central ledger
  const dispatchBlock = source.match(/^([ \t]*)workflow_dispatch:[ \t]*\n((?:\1[ \t]+.*\n|[ \t]*\n)*)/m)?.[2] || '';
  const acceptsDigest = /^[ \t]+base-digest:[ \t]*$/m.test(dispatchBlock);
  const body = { ref: repo.default_branch, return_run_details: true, ...(acceptsDigest ? { inputs: { 'base-digest': digest } } : {}) };
  const result = await github.api(`repos/${repository}/actions/workflows/standards.yml/dispatches`, body);
  if (!Number.isSafeInteger(result?.workflow_run_id) || result.workflow_run_id <= 0) throw new Error('Dispatch returned no run ID; outcome unknown, do not retry blindly');
  log(`Dispatched ${repository} run ${result.workflow_run_id} base-digest=${digest}${acceptsDigest ? '' : ' (legacy caller, ledger attribution)'}`);
  return { repository, runId: result.workflow_run_id };
}

export async function fleet({ github, registry, env = process.env, sleep = delay, log = console.log }) {
  if (env.GITHUB_RUN_ATTEMPT && env.GITHUB_RUN_ATTEMPT !== '1') throw new Error('Replay with a fresh workflow_dispatch, not a rerun, to preserve dispatch attribution');
  const own = await github.api(`repos/${SOURCE}/actions/runs/${env.GITHUB_RUN_ID}`);
  const digest = await registry.amd64(BASE, 'main');
  const requested = env.BASE_DIGEST || '';
  if (requested && !DIGEST.test(requested)) throw new Error('base-digest must be a sha256 digest');
  if (requested && requested !== digest) { log('::notice::Requested base has been superseded; no fleet changes'); return; }
  const inventory = await github.api(`repos/${OWNER}/mac-dev-playbook/contents/cue/incus/ci-image-inventory.json`);
  const admitted = repositories(JSON.parse(Buffer.from(inventory.content, 'base64').toString('utf8')));
  const prior = await github.list(`repos/${SOURCE}/actions/workflows/${WORKFLOW}/runs?branch=main`, 'workflow_runs');
  const ledgers = [];
  for (let run of prior.filter(row => Date.parse(row.created_at) >= Date.parse(own.created_at) - 30 * 86400000
    && (row.created_at < own.created_at || (row.created_at === own.created_at && row.id < Number(env.GITHUB_RUN_ID)))
    && ['workflow_dispatch', 'repository_dispatch'].includes(row.event))) {
    if (run.status !== 'completed') run = await waitCompleted(github, SOURCE, run.id, sleep);
    const ledger = await github.ledger(run, env.RUNNER_TEMP);
    if (ledger) ledgers.push(validateLedger(ledger));
    else log(`::notice::Receiver ${run.id} has no ledger; unrecorded legacy dispatches are not safe to cancel`);
  }
  const ledger = { version: 1, baseDigest: digest, dispatches: [] };
  const destination = path.join(env.RUNNER_TEMP, 'fleet-dispatches.json');
  const save = () => {
    fs.writeFileSync(`${destination}.new`, JSON.stringify(ledger));
    fs.renameSync(`${destination}.new`, destination);
  };
  save();
  let failed = 0;
  for (const repository of admitted) {
    // A new publication may precede its dispatch event or arrive while this loop is paced
    if (await registry.amd64(BASE, 'main') !== digest) { log('::notice::Base moved during dispatch; stop the obsolete loop'); break; }
    try {
      const entry = await reconcile({ github, registry, repository, digest, ledgers, actor: env.FLEET_ACTOR, directory: env.RUNNER_TEMP, sleep, log });
      if (entry) { ledger.dispatches.push(entry); save(); }
    } catch (error) { failed++; log(`::error::${repository}: ${error.message}`); }
    await sleep(15000);
  }
  if (failed) throw new Error(`Failed repositories: ${failed}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { await fleet({ github: new GitHub(process.env.GH_TOKEN), registry: new Registry(process.env.REGISTRY_TOKEN) }); }
  catch (error) { console.error(`::error::${error.message}`); process.exitCode = 1; }
}
