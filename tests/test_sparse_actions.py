import re
from pathlib import Path

import pytest

from test_macos_tools import document

ROOT = Path(__file__).resolve().parents[1]


def sparse_action_checkouts():
    checkouts = []
    for workflow in sorted((ROOT / '.github/workflows').glob('*.yml')):
        for job_name, job in document(workflow)['jobs'].items():
            for step in job.get('steps', []):
                inputs = step.get('with', {})
                if inputs.get('repository') != 'Rubio-Enterprises/.github':
                    continue
                selected = set(inputs.get('sparse-checkout', '').split())
                actions = {path for path in selected if path.startswith('actions/')}
                if actions:
                    checkouts.append(pytest.param(actions, id=f'{workflow.name}:{job_name}:{step["name"]}'))
    return checkouts


def sibling_dependencies(directory):
    return {
        'actions/' + name
        for script in directory.rglob('*')
        if script.is_file() and script.suffix in ('.sh', '.mjs', '.py', '.yml')
        for name in re.findall(r'\.\./([a-zA-Z0-9_-]+)/', script.read_text())
    }


@pytest.mark.parametrize('selected', sparse_action_checkouts())
def test_sparse_actions_include_their_sibling_dependencies(selected):
    pending = list(selected)
    visited = set()
    while pending:
        action = pending.pop()
        if action in visited:
            continue
        visited.add(action)
        directory = ROOT / action
        assert directory.is_dir(), action
        dependencies = sibling_dependencies(directory)
        assert dependencies <= selected, f'{action} references missing sparse directories: {dependencies - selected}'
        pending.extend(dependencies - visited)


def test_sibling_discovery_covers_shell_and_node(tmp_path):
    action = tmp_path / 'action'
    action.mkdir()
    (action / 'install.sh').write_text('bash "$(dirname "$0")/../release-asset/install.sh"\n')
    (action / 'tools.mjs').write_text("new URL('../registry/', import.meta.url);\n")
    assert sibling_dependencies(action) == {'actions/release-asset', 'actions/registry'}
