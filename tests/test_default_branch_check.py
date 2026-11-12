import copy
from itertools import product
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'
READ = {'contents': 'read', 'packages': 'read'}
GUARD = ("github.repository != 'Rubio-Enterprises/.github' && "
         "contains(fromJSON('[\"push\",\"workflow_dispatch\"]'), github.event_name) && "
         "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)")


def workflow(name):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(WORKFLOWS / f'{name}.yml')]))


def test_delivery_order_visibility_and_scratch_guard():
    private = workflow('repository-image')['jobs']['verify']
    assert private['needs'] == 'build'
    assert private['if'] == GUARD + " && needs.build.result == 'success' && inputs.image-name == ''"
    assert private['uses'] == 'Rubio-Enterprises/.github/.github/workflows/default-branch-check.yml@main'
    assert private['permissions'] == READ
    public = workflow('standards-cache')['jobs']['verify']
    assert public['if'] == GUARD + ' && github.event.repository.private == false'
    assert public['uses'] == private['uses']
    assert public['permissions'] == READ
    assert 'needs' not in public
    for job in (private, public):
        assert 'secrets' not in job
        assert 'with' not in job


@pytest.mark.parametrize(('event', 'branch', 'default', 'repository'), list(product(
    ['push', 'workflow_dispatch', 'schedule', 'pull_request', 'merge_group'],
    ['main', 'master', 'throwaway'], ['main', 'master'],
    ['Rubio-Enterprises/org-ops', 'Rubio-Enterprises/.github'])))
def test_guard(event, branch, default, repository):
    private = workflow('repository-image')['jobs']['verify']['if']
    public = workflow('standards-cache')['jobs']['verify']['if']
    assert private.startswith(GUARD + ' && ')
    assert public.startswith(GUARD + ' && ')
    expression = private.replace('inputs.image-name', "inputs['image-name']")
    code = '''const github = JSON.parse(process.argv[1]);
const needs = {build: {result: 'success'}}, inputs = {'image-name': ''};
const contains = (a, b) => a.includes(b), fromJSON = JSON.parse;
const format = (s, v) => s.replace('{0}', v);
console.log(Boolean(''' + expression + '));'
    data = {'repository': repository, 'event_name': event, 'ref': f'refs/heads/{branch}',
            'event': {'repository': {'default_branch': default}}}
    result = subprocess.check_output(['node', '-e', code, json.dumps(data)], text=True)
    expected = repository != 'Rubio-Enterprises/.github' and event in ('push', 'workflow_dispatch') and branch == default
    assert result.strip() == str(expected).lower()


@pytest.mark.parametrize('caller', ['repository-image', 'standards-cache'])
def test_caller_specific_guards(caller):
    expression = workflow(caller)['jobs']['verify']['if'].replace('inputs.image-name', "inputs['image-name']")
    cases = []
    for private, build, image in product([True, False], ['success', 'failure', 'skipped', 'cancelled'], ['', 'scratch']):
        data = {'github': {'repository': 'Rubio-Enterprises/example', 'event_name': 'push',
                           'ref': 'refs/heads/master', 'event': {'repository': {'default_branch': 'master', 'private': private}}},
                'needs': {'build': {'result': build}}, 'inputs': {'image-name': image}}
        expected = build == 'success' and not image if caller == 'repository-image' else not private
        cases.append([data, expected])
    code = '''const cases = JSON.parse(process.argv[1]);
const contains = (a, b) => a.includes(b), fromJSON = JSON.parse;
const format = (s, v) => s.replace('{0}', v);
for (const [{github, needs, inputs}, expected] of cases) {
  if (Boolean(''' + expression + ''') !== expected) process.exit(1);
}'''
    subprocess.run(['node', '-e', code, json.dumps(cases)], check=True)


def test_production_test_gate_no_inputs_no_shortcut_no_retry():
    wf = workflow('default-branch-check')
    assert wf['on'] == {'workflow_call': None}
    assert wf['permissions'] == READ
    assert wf['jobs']['test-gate-main'] == {
        'permissions': READ, 'uses': 'Rubio-Enterprises/.github/.github/workflows/test-gate.yml@main',
    }
    text = json.dumps(wf)
    for absent in ('check-pr', 'BASE_SHA', 'HEAD_SHA', 'PR_TITLE', 'qualification-',
                   'continue-on-error', 'retry', 'issues:', 'pull-requests:', 'evidence'):
        assert absent not in text
    assert set(wf['jobs']) == {'route', 'standards-linux', 'standards-macos', 'standards-main', 'test-gate-main'}
    assert wf['jobs']['route']['with'] == {'route': 'linux'}
    assert 'if' not in wf['jobs']['route']
    assert 'if' not in wf['jobs']['standards-linux']
    assert wf['jobs']['standards-macos']['if'] == "needs.route.outputs.platform == 'macos'"
    assert wf['jobs']['standards-main']['if'] == '${{ always() }}'


def test_nested_permissions_fit_both_starter_callers_without_inherited_secrets():
    # Existing starter grants: cache has contents/packages read; publisher additionally
    # has packages write and id-token write. Verification may use only their intersection.
    for caller in (READ, {'contents': 'read', 'packages': 'write', 'id-token': 'write'}):
        def subset(permissions, grants):
            level = {'none': 0, 'read': 1, 'write': 2}
            for scope, value in permissions.items():
                assert level[value] <= level[grants.get(scope, 'none')], (scope, permissions, grants)

        def visit(name, grants):
            wf = workflow(name)
            subset(wf.get('permissions', grants), grants)
            grants = wf.get('permissions', grants)
            for job in wf['jobs'].values():
                permissions = job.get('permissions', grants)
                subset(permissions, grants)
                assert 'secrets' not in job
                if 'uses' in job:
                    nested = job['uses'].split('/')[-1].split('@')[0].removesuffix('.yml')
                    visit(nested, permissions)
        visit('default-branch-check', caller)
    gate = workflow('test-gate')
    assert all(not secret.get('required', False) for secret in gate['on']['workflow_call']['secrets'].values())
    for job in gate['jobs'].values():
        for step in job.get('steps', []):
            if 'secrets.' in json.dumps(step):
                assert step['if'] == "inputs.qualification-repository != ''"
                assert step['uses'].startswith('actions/create-github-app-token@')


def whole_tree_steps(job):
    job = copy.deepcopy(job)
    job['steps'] = [step for step in job['steps'] if step.get('id') not in ('check-pr-ci', 'check-pr-hosted')]
    job.pop('if', None)
    if job['name'] == 'standards-macos':
        job['if'] = "needs.route.outputs.platform == 'macos'"
    for key in ('BASE_SHA', 'HEAD_SHA'):
        job.get('env', {}).pop(key, None)
    for step in job['steps']:
        if step.get('id') == 'context-hosted':
            step.pop('env', None)
            step['run'] = re.sub(r'^  --arg (BASE_SHA|HEAD_SHA|PR_TITLE) .*\n', '', step['run'], flags=re.M)
    return job


@pytest.mark.parametrize('name', ['standards-linux', 'standards-macos'])
def test_standards_steps_cannot_drift_from_pr_execution(name):
    original = whole_tree_steps(workflow('standards')['jobs'][name])
    actual = workflow('default-branch-check')['jobs'][name]
    assert actual == original
    # Includes hosted disk preparation, full history, writable checkout, trivy restore,
    # context writer, native routing and exactly the PR macOS download-cache key/path.
    assert all('secrets.' not in json.dumps(s) or 'secrets.GITHUB_TOKEN' in json.dumps(s)
               for s in actual['steps'])


def test_standards_aggregate_preserves_fail_closed_behavior():
    original = workflow('standards')['jobs']['standards']
    actual = workflow('default-branch-check')['jobs']['standards-main']
    original['name'] = 'standards-main'
    original['if'] = '${{ always() }}'
    assert actual == original


def test_hosted_context_runs_without_any_pr_metadata(tmp_path):
    steps = workflow('default-branch-check')['jobs']['standards-linux']['steps']
    writer = next(step for step in steps if step.get('id') == 'context-hosted')
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    event = tmp_path / 'event.json'
    event.write_text('{"ref":"refs/heads/main"}')
    env = {**os.environ, 'GITHUB_WORKSPACE': str(tmp_path), 'GITHUB_EVENT_PATH': str(event),
           'GITHUB_WORKFLOW': 'standards', 'GITHUB_EVENT_NAME': 'push',
           'GITHUB_REPOSITORY': 'Rubio-Enterprises/example', 'GITHUB_RUN_ID': '123',
           'GITHUB_SERVER_URL': 'https://github.com', 'GITHUB_STEP_SUMMARY': '/not/mounted',
           'CI': 'true', 'TRIVY_CACHE_DIR': str(tmp_path / '.cache/trivy'),
           'XDG_CACHE_HOME': str(tmp_path / '.cache')}
    for key in ('BASE_SHA', 'HEAD_SHA', 'PR_TITLE', 'TRIVY_SKIP_DB_UPDATE'):
        env.pop(key, None)
    result = subprocess.run(['bash', '-euc', writer['run']], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    context = json.loads((tmp_path / '.cache/ci/context.json').read_text())
    assert context['GITHUB_EVENT_NAME'] == 'push'
    assert context['GITHUB_EVENT_PATH'] == str(tmp_path / '.cache/ci/event.json')
    assert context['TRIVY_CACHE_DIR'] == env['TRIVY_CACHE_DIR']
    assert not {'BASE_SHA', 'HEAD_SHA', 'PR_TITLE'} & context.keys()
