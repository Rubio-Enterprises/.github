#!/usr/bin/env python3
"""Return both passing run links, or {}. Reuse is all-or-nothing.

For a single-parent push P with parent B, require B to be an ancestor of PR head H.
Together with tree(P) == tree(H), H already contains the whole base and the squash
adds nothing beyond H relative to B. GitHub's checked merge tree is therefore the
same tree as H and P; behind branches and two-parent merges get no shortcut.
"""

import json
import os
import re
import subprocess
import sys

PATHS = ('.github/workflows/standards.yml', '.github/workflows/test-gate.yml')
SHA = re.compile(r'[0-9a-f]{40}')


def api(endpoint):
    result = subprocess.run(['gh', 'api', '--include', endpoint], check=True,
                            capture_output=True, text=True, timeout=20)
    headers, body = result.stdout.replace('\r\n', '\n').split('\n\n', 1)
    if not re.match(r'HTTP/\S+ 200\b', headers) or re.search(r'^link:.*rel="next"', headers, re.I | re.M):
        raise ValueError('incomplete API response')
    return json.loads(body)


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
    # Injected workflow IDs are repository-specific. Their workflow_url namespace
    # distinguishes them from a repository-owned workflow at the very same path.
    page = api(f'{root}/actions/runs?event=pull_request&status=completed&per_page=100')
    runs = page['workflow_runs']
    if page['total_count'] != len(runs) or len(runs) > 100:
        return {}
    latest, identities = {}, {}
    api_root = os.environ.get('GITHUB_API_URL', 'https://api.github.com')
    server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
    for run in runs:
        path, head = run['path'], run['head_sha']
        if path not in PATHS:
            continue
        if (run['event'] != 'pull_request' or run['status'] != 'completed'
                or run['repository']['full_name'] != repo
                or run['head_repository']['full_name'] != repo):
            continue
        if (type(run['workflow_id']) is not int or type(run['id']) is not int
                or run['workflow_url'] != f"{api_root}/{root}/actions/required_workflows/{run['workflow_id']}"):
            continue
        identities.setdefault(path, set()).add(run['workflow_id'])
        if not SHA.fullmatch(head) or run['head_commit']['id'] != head:
            return {}
        key = (path, head)
        order = (run['run_started_at'], run['id'], run['run_attempt'])
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, run)
    if any(len(identities.get(path, set())) != 1 for path in PATHS):
        return {}
    candidates = []
    for (path, head), (order, run) in latest.items():
        if path != PATHS[0] or (PATHS[1], head) not in latest:
            continue
        pair = (run, latest[(PATHS[1], head)][1])
        if all(r['conclusion'] == 'success' and r['head_commit']['tree_id'] == tree for r in pair):
            candidates.append((order, head, pair))
    if not candidates:
        return {}
    _, head, pair = max(candidates, key=lambda candidate: candidate[0])
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
