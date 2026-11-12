import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = 'Rubio-Enterprises/.github/actions/setup-check-dependencies@main'


def document(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / path)]))


@pytest.mark.parametrize('workflow', ['standards', 'default-branch-check'])
def test_ci_image_dependencies_precede_checks_and_fail_closed(workflow):
    steps = document(f'.github/workflows/{workflow}.yml')['jobs']['standards-linux']['steps']
    setup, = [step for step in steps if step.get('uses') == ACTION]
    assert setup['name'] == 'Set up dependencies (CI Image)'
    assert setup['if'] == "needs.route.outputs.ci-image == 'true'"
    assert 'continue-on-error' not in setup
    checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
    assert steps.index(checkout) < steps.index(setup)
    for check in (step for step in steps if step.get('id') in ('check-ci', 'check-pr-ci')):
        assert steps.index(setup) < steps.index(check)
        assert 'continue-on-error' not in check
    whole_tree = next(step for step in steps if step.get('id') == 'check-ci')
    assert whole_tree['if'] == setup['if']
    assert whole_tree['run'] == 'standards check'
    if workflow == 'standards':
        pr = next(step for step in steps if step.get('id') == 'check-pr-ci')
        assert "steps['check-ci'].outcome != 'skipped'" in pr['if']


def test_shared_action_uses_test_gate_dependency_interface_without_hooks():
    action = document('actions/setup-check-dependencies/action.yml')
    assert action['runs']['using'] == 'composite'
    step, = action['runs']['steps']
    assert step['run'] == 'bin/setup.sh'
    assert step['shell'] == 'bash'
    assert step['env'] == {'GH_TOKEN': '${{ github.token }}', 'NODE_AUTH_TOKEN': '${{ github.token }}'}
    assert 'continue-on-error' not in step
    gate = document('.github/workflows/test-gate.yml')['jobs']['test-gate-linux']['steps']
    setup = next(step for step in gate if step.get('name') == 'Set up dependencies')
    assert 'if [ "$CI_IMAGE" = true ]; then\n  ' + step['run'] + '\nelse' in setup['run']
    assert setup['env'] == step['env']


@pytest.mark.parametrize('status', [0, 23])
def test_dependency_script_failure_is_propagated(tmp_path, status):
    step, = document('actions/setup-check-dependencies/action.yml')['runs']['steps']
    (tmp_path / 'bin').mkdir()
    setup = tmp_path / 'bin/setup.sh'
    setup.write_text(f'#!/bin/sh\nprintf setup > dependencies-attempted\nexit {status}\n')
    setup.chmod(0o755)
    result = subprocess.run(['bash', '-e', '-c', step['run']], cwd=tmp_path, capture_output=True)
    assert result.returncode == status
    assert (tmp_path / 'dependencies-attempted').read_text() == 'setup'
