#!/usr/bin/env python3
"""Return both passing run links, or {}. Reuse is all-or-nothing.

For a single-parent push P with parent B, require B to be an ancestor of PR head H.
Together with tree(P) == tree(H), H already contains the whole base and the squash
adds nothing beyond H relative to B. GitHub's checked merge tree is therefore the
same tree as H and P; behind branches and two-parent merges get no shortcut.
The newest page only nominates H; a complete per-head page must confirm both
latest completed results, including re-runs of older runs outside that window.
At most four API calls are made, without pagination or retries.
"""

import json
import os
import re
import subprocess
import sys

PATHS = ('.github/workflows/standards.yml', '.github/workflows/test-gate.yml')
SHA = re.compile(r'[0-9a-f]{40}')


def api(endpoint, *, complete=True):
    result = subprocess.run(['gh', 'api', '--include', endpoint], check=True,
                            capture_output=True, text=True, timeout=20)
    headers, body = result.stdout.replace('\r\n', '\n').split('\n\n', 1)
    if (not re.match(r'HTTP/\S+ 200\b', headers)
            or complete and re.search(r'^link:.*rel="next"', headers, re.I | re.M)):
        raise ValueError('incomplete API response')
    return json.loads(body)


def injected(runs, repo, api_root):
    # Injected workflow IDs are repository-specific. Their workflow_url namespace
    # distinguishes them from a repository-owned workflow at the very same path.
    for run in runs:
        if (run['path'] not in PATHS or run['event'] != 'pull_request'
                or run['status'] != 'completed' or run['repository']['full_name'] != repo
                or run['head_repository']['full_name'] != repo):
            continue
        if (type(run['workflow_id']) is not int or type(run['id']) is not int
                or run['workflow_url'] != f"{api_root}/repos/{repo}/actions/required_workflows/{run['workflow_id']}"):
            continue
        if not SHA.fullmatch(run['head_sha']) or run['head_commit']['id'] != run['head_sha']:
            raise ValueError('invalid PR head')
        yield run


def order(run):
    return run['run_started_at'], run['id'], run['run_attempt']


def evidence():
    if os.environ.get('GITHUB_EVENT_NAME') != 'push':
        return {}
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_SHA']
    if not SHA.fullmatch(sha):
        return {}
    root = f'repos/{repo}'
    commit = api(f'{root}/git/commits/{sha}')
    if commit['sha'] != sha or len(commit['parents']) != 1:
        return {}
    parent, tree = commit['parents'][0]['sha'], commit['tree']['sha']
    if not SHA.fullmatch(parent) or not SHA.fullmatch(tree):
        return {}
    api_root = os.environ.get('GITHUB_API_URL', 'https://api.github.com')
    server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
    window = api(f'{root}/actions/runs?event=pull_request&status=completed&per_page=100', complete=False)
    if not isinstance(window['workflow_runs'], list) or len(window['workflow_runs']) > 100:
        return {}
    candidates = [run for run in injected(window['workflow_runs'], repo, api_root)
                  if run['head_commit']['tree_id'] == tree]
    if not candidates:
        return {}
    head = max(candidates, key=order)['head_sha']
    page = api(f'{root}/actions/runs?event=pull_request&head_sha={head}&per_page=100')
    runs = page['workflow_runs']
    if (type(page['total_count']) is not int or not isinstance(runs, list)
            or page['total_count'] != len(runs) or len(runs) > 100
            or any(run['head_sha'] != head for run in runs)):
        return {}
    latest, identities = {}, {}
    for run in injected(runs, repo, api_root):
        path = run['path']
        identities.setdefault(path, set()).add(run['workflow_id'])
        if path not in latest or order(run) > order(latest[path]):
            latest[path] = run
    if any(len(identities.get(path, set())) != 1 for path in PATHS):
        return {}
    pair = tuple(latest[path] for path in PATHS)
    if not all(run['conclusion'] == 'success' and run['head_commit']['tree_id'] == tree for run in pair):
        return {}
    comparison = api(f'{root}/compare/{parent}...{head}')
    if comparison['status'] not in ('ahead', 'identical') or comparison['behind_by'] != 0:
        return {}
    return dict(zip(('standards', 'test-gate'),
                    (f"{server}/{repo}/actions/runs/{run['id']}" for run in pair)))


def main():
    try:
        answer = evidence()
    except (KeyError, ValueError, TypeError, AttributeError, OSError, subprocess.SubprocessError):
        answer = {}
        print('No conclusive PR evidence; execute verification.', file=sys.stderr)
    print(json.dumps(answer, separators=(',', ':')))


if __name__ == '__main__':
    main()
