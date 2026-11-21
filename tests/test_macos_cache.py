from hashlib import sha256
import json
import subprocess

import pytest

from test_default_branch_check import READ, workflow


LOCK = 'lockfile_version = 4\n\n[tools]\n'
REF = 'refs/heads/trunk'
KEY = f'macos-tools-downloads-macOS-ARM64-{sha256(sha256(LOCK.encode()).digest()).hexdigest()}'


@pytest.mark.parametrize(('platform', 'fallback', 'cache', 'expected'), [
    ('linux', 'macos', 'miss', None),
    ('macos', 'linux', 'hit', False),
    ('macos', 'linux', 'miss', True),
    ('macos', 'linux', 'wrong-key', True),
    ('macos', 'linux', 'pull-ref', True),
    ('macos', 'linux', 'error', True),
    ('macos', 'linux', 'lock-error', None),
    (None, 'macos', 'miss', True),
    (None, 'linux', 'miss', None),
])
def test_default_ref_macos_cache_probe(platform, fallback, cache, expected):
    job = workflow('default-branch-check')['jobs']['evidence']
    step, = [s for s in job['steps'] if s.get('id') == 'macos-cache']
    code = '''
const assert = require('node:assert/strict');
const [script, platform, fallback, cache, expected, lock, ref, key] = JSON.parse(process.argv[1]);
const repo = {owner: 'Rubio-Enterprises', repo: 'example'};
const sha = 'a'.repeat(40);
const context = {repo, sha, ref, runAttempt: 1, payload: {repository: {
  custom_properties: platform === null ? {} : {'ci-platform': platform}
}}};
const requests = [];
const result = {};
const core = {setOutput: (name, value) => { result[name] = value; }, warning: () => {}};
const github = {request: async (route, args) => {
  requests.push(route);
  if (route.includes('/properties/')) {
    assert.deepEqual(args, repo);
    return {data: [{property_name: 'ci-platform', value: fallback}]};
  }
  if (route.includes('/contents/')) {
    assert.deepEqual(args, {...repo, ref: sha});
    if (cache === 'lock-error') throw Object.assign(new Error('missing lock'), {status: 404});
    return {data: {type: 'file', encoding: 'base64', content: Buffer.from(lock).toString('base64')}};
  }
  assert.equal(route, 'GET /repos/{owner}/{repo}/actions/caches');
  assert.deepEqual(args, {...repo, key, ref});
  if (cache === 'error') throw new Error('lookup unavailable');
  const entries = {hit: [{key, ref}], 'wrong-key': [{key: key + '-other', ref}],
    'pull-ref': [{key, ref: 'refs/pull/23/merge'}]};
  return {data: {actions_caches: entries[cache] || []}};
}};
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
(async () => {
  await new AsyncFunction('github', 'context', 'core', 'require', script)(github, context, core, require);
  assert.deepEqual(result, expected === null ? {} : {needed: expected});
  const count = (platform === null ? 1 : 0) + ((platform || fallback) === 'macos' ? 1 : 0)
    + (expected === null ? 0 : 1);
  assert.equal(requests.length, count);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    subprocess.run(['node', '-e', code, json.dumps([
        step['with']['script'], platform, fallback, cache, expected, LOCK, REF, KEY,
    ])], check=True)


def test_warmer_is_separate_from_verification_and_uses_the_same_cache():
    jobs = workflow('default-branch-check')['jobs']
    evidence = jobs['evidence']
    probe, = [s for s in evidence['steps'] if s.get('id') == 'macos-cache']
    assert 'steps.lookup.outputs.runs' in probe['if']
    assert 'github.event.repository.default_branch' in probe['if']
    assert evidence['outputs']['warm-macos'] == '${{ steps.macos-cache.outputs.needed }}'
    warm = jobs['warm-macos-downloads']
    assert warm['if'] == "needs.evidence.outputs.warm-macos == 'true'"
    assert warm['needs'] == 'evidence'
    assert warm['permissions'] == {'contents': READ['contents']}
    assert 'warm-macos-downloads' not in str(jobs['standards-main']['needs'])
    assert 'warm-macos-downloads' not in str(jobs['test-gate-main']['needs'])
    native = jobs['standards-macos']
    assert warm['runs-on'] == native['runs-on']
    restore, = [s for s in warm['steps'] if s.get('id') == 'macos-downloads']
    native_restore, = [s for s in native['steps'] if s.get('id') == 'macos-downloads']
    assert restore['with'] == {**native_restore['with'], 'lookup-only': True}
    install, = [s for s in warm['steps'] if '/actions/macos-tools@' in s.get('uses', '')]
    assert install['with'] == {'runners': ''}
    save, = [s for s in warm['steps'] if s.get('uses', '').startswith('actions/cache/save@')]
    assert install['if'] == save['if'] == "steps.macos-downloads.outputs.cache-hit != 'true'"
    assert save['with']['path'] == restore['with']['path']
    assert save['with']['key'] == '${{ steps.macos-downloads.outputs.cache-primary-key }}'
    assert warm['steps'].index(restore) < warm['steps'].index(install) < warm['steps'].index(save)
