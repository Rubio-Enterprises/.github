import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/checked-tree'
REPO = 'Rubio-Enterprises/scratch-955-verify'
PUSH = '10cbe8a28f4fb500bca797b1b330b9c4edd1d691'
HEAD = '977007c8f3719911f04424337b4d0f72e995b781'


def recorded():
    return {name: json.loads((FIXTURES / f'{name}.json').read_text())
            for name in ('commit', 'runs', 'compare')}


def execute(tmp_path, data, event='push', status=200, next_page=False):
    fixture = tmp_path / 'responses.json'
    fixture.write_text(json.dumps({'data': data, 'status': status, 'next': next_page}))
    gh = tmp_path / 'gh'
    gh.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
request = sys.argv[-1]
with open(os.environ['CALLS'], 'a') as calls:
    calls.write(request + '\\n')
fixture = json.loads(Path(os.environ['RESPONSES']).read_text())
name = 'commit' if '/git/commits/' in request else 'compare' if '/compare/' in request else 'runs'
if fixture['status'] != 200 and name == 'runs':
    print('HTTP/2.0 ' + str(fixture['status']) + '\\n\\n{}')
    sys.exit(1)
print('HTTP/2.0 200 OK')
if fixture['next'] and name == 'runs':
    print('Link: <https://api.github.com/next>; rel="next"')
print()
body = fixture['data'][name]
if name == 'compare' and not request.endswith('977007c8f3719911f04424337b4d0f72e995b781'):
    body = {'status': 'diverged', 'behind_by': 1}
print(json.dumps(body))
''')
    gh.chmod(0o755)
    calls = tmp_path / 'calls'
    result = subprocess.run(['python3', str(ROOT / 'scripts/checked-tree.py')],
                            env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}',
                                 'RESPONSES': str(fixture), 'CALLS': str(calls),
                                 'GITHUB_EVENT_NAME': event, 'GITHUB_REPOSITORY': REPO,
                                 'GITHUB_SHA': PUSH}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    requests = calls.read_text().splitlines() if calls.exists() else []
    assert len(requests) <= 3
    return json.loads(result.stdout), requests


def test_recorded_success(tmp_path):
    answer, calls = execute(tmp_path, recorded())
    assert answer == {
        'standards': f'https://github.com/{REPO}/actions/runs/43209031402',
        'test-gate': f'https://github.com/{REPO}/actions/runs/43209031545',
    }
    assert calls[-1].endswith('7b57346cbf1e63458f3aef460477d67146add12d...' + HEAD)


@pytest.mark.parametrize('case', [
    'behind-tree', 'equal-tree-diverged', 'only-standards', 'different-heads',
    'newer-failure', 'newer-standards-failure', 'fork', 'repo-owned-lookalike', 'two-parents',
    'more-than-one-page', 'null-head', 'malformed', 'wrong-workflow-path',
])
def test_counter_cases_run(tmp_path, case):
    data = recorded()
    current = [r for r in data['runs']['workflow_runs'] if r['head_sha'] == HEAD]
    if case == 'behind-tree':
        data['commit']['tree']['sha'] = 'a' * 40
    elif case == 'equal-tree-diverged':
        data['compare'] = {'status': 'diverged', 'behind_by': 1}
    elif case == 'only-standards':
        current[0]['conclusion'] = 'failure'
    elif case == 'different-heads':
        current[0]['head_sha'] = current[0]['head_commit']['id'] = 'b' * 40
    elif case in ('newer-failure', 'newer-standards-failure'):
        newer = copy.deepcopy(next(r for r in current if r['path'].endswith(
            'standards.yml' if case == 'newer-standards-failure' else 'test-gate.yml')))
        newer.update(id=newer['id'] + 100, conclusion='failure', run_started_at='2026-12-01T00:00:00Z')
        data['runs']['workflow_runs'].append(newer)
        data['runs']['total_count'] += 1
    elif case == 'fork':
        for run in current:
            run['head_repository']['full_name'] = 'Rubio-Enterprises/a-fork'
    elif case == 'repo-owned-lookalike':
        for run in current:
            run['workflow_url'] = run['workflow_url'].replace('/required_workflows/', '/workflows/')
    elif case == 'two-parents':
        data['commit']['parents'].append({'sha': HEAD})
    elif case == 'more-than-one-page':
        data['runs']['total_count'] = 101
    elif case == 'null-head':
        current[0]['head_repository'] = None
    elif case == 'malformed':
        data['runs'] = {'unknown': []}
    elif case == 'wrong-workflow-path':
        current[0]['path'] = '.github/workflows/fake.yml'
    answer, _ = execute(tmp_path, data)
    assert answer == {}


@pytest.mark.parametrize('status', [403, 404, 429, 500])
def test_non_200_runs(tmp_path, status):
    assert execute(tmp_path, recorded(), status=status)[0] == {}


def test_next_link_runs(tmp_path):
    assert execute(tmp_path, recorded(), next_page=True)[0] == {}


@pytest.mark.parametrize('event', ['workflow_dispatch', 'pull_request', 'merge_group', 'schedule'])
def test_non_push_does_not_look_up(tmp_path, event):
    assert execute(tmp_path, recorded(), event=event) == ({}, [])
