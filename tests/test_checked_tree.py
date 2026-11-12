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
            for name in ('commit', 'runs', 'head', 'compare')}


def execute(tmp_path, data, event='push', status=200, next_page=False, next_at='head', status_at='runs'):
    fixture = tmp_path / 'responses.json'
    fixture.write_text(json.dumps({'data': data, 'status': status, 'next': next_page,
                                   'next_at': next_at, 'status_at': status_at}))
    gh = tmp_path / 'gh'
    gh.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
request = sys.argv[-1]
with open(os.environ['CALLS'], 'a') as calls:
    calls.write(request + '\\n')
fixture = json.loads(Path(os.environ['RESPONSES']).read_text())
name = ('commit' if '/git/commits/' in request else 'compare' if '/compare/' in request
        else 'head' if 'head_sha=' in request else 'runs')
if fixture['status'] != 200 and name == fixture['status_at']:
    print('HTTP/2.0 ' + str(fixture['status']) + '\\n\\n{}')
    sys.exit(1)
print('HTTP/2.0 200 OK')
if fixture['next'] and name == fixture['next_at']:
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
    assert len(requests) <= 4
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
    current = data['head']['workflow_runs']
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
        newer.update(id=newer['id'] - 100, run_attempt=2, conclusion='failure',
                     run_started_at='2026-12-01T00:00:00Z')
        assert newer['id'] not in {run['id'] for run in data['runs']['workflow_runs']}
        data['head']['workflow_runs'].append(newer)
        data['head']['total_count'] += 1
    elif case == 'fork':
        for run in current:
            run['head_repository']['full_name'] = 'Rubio-Enterprises/a-fork'
    elif case == 'repo-owned-lookalike':
        for run in current:
            run['workflow_url'] = run['workflow_url'].replace('/required_workflows/', '/workflows/')
    elif case == 'two-parents':
        data['commit']['parents'].append({'sha': HEAD})
    elif case == 'more-than-one-page':
        data['head']['total_count'] = 101
    elif case == 'null-head':
        current[0]['head_repository'] = None
    elif case == 'malformed':
        data['head'] = {'unknown': []}
    elif case == 'wrong-workflow-path':
        current[0]['path'] = '.github/workflows/fake.yml'
    answer, _ = execute(tmp_path, data)
    assert answer == {}


@pytest.mark.parametrize('status', [403, 404, 429, 500])
@pytest.mark.parametrize('endpoint', ['runs', 'head'])
def test_non_200_runs(tmp_path, status, endpoint):
    assert execute(tmp_path, recorded(), status=status, status_at=endpoint)[0] == {}


def test_next_link_on_head_runs(tmp_path):
    assert execute(tmp_path, recorded(), next_page=True)[0] == {}


def test_newer_in_progress_run_does_not_replace_latest_completed(tmp_path):
    data = recorded()
    pending = copy.deepcopy(data['head']['workflow_runs'][0])
    pending.update(id=pending['id'] + 100, status='in_progress', conclusion=None,
                   run_started_at='2026-12-01T00:00:00Z')
    data['head']['workflow_runs'].append(pending)
    data['head']['total_count'] += 1
    assert set(execute(tmp_path, data)[0]) == {'standards', 'test-gate'}


def test_candidate_window_of_100_with_next_link_is_evaluated(tmp_path):
    data = recorded()
    filler = copy.deepcopy(data['head']['workflow_runs'][0])
    filler['path'] = '.github/workflows/unrelated.yml'
    data['runs']['workflow_runs'] += [copy.deepcopy(filler) for _ in
                                    range(100 - len(data['runs']['workflow_runs']))]
    data['runs']['total_count'] = 2500
    answer, calls = execute(tmp_path, data, next_page=True, next_at='runs')
    assert len(data['runs']['workflow_runs']) == 100
    assert set(answer) == {'standards', 'test-gate'}
    assert len(calls) == 4
    assert f'head_sha={HEAD}' in calls[2]


def test_candidate_does_not_need_both_workflows_in_window(tmp_path):
    data = recorded()
    data['runs'] = {'total_count': 2500, 'workflow_runs': [data['head']['workflow_runs'][0]]}
    assert set(execute(tmp_path, data, next_page=True, next_at='runs')[0]) == {'standards', 'test-gate'}


@pytest.mark.parametrize('event', ['workflow_dispatch', 'pull_request', 'merge_group', 'schedule'])
def test_non_push_does_not_look_up(tmp_path, event):
    assert execute(tmp_path, recorded(), event=event) == ({}, [])
