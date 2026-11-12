import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def document(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / path)], text=True))


def test_hosted_preparation_precedes_candidate_download_and_setup():
    steps = document('.github/workflows/test-gate.yml')['jobs']['test-gate']['steps']
    prepare = next(s for s in steps if s.get('name') == 'Prepare hosted Docker disk')
    assert prepare['if'] == (
        "steps.policy.outputs.opted-out != 'true' && env.CI_IMAGE != 'true' && "
        "runner.environment == 'github-hosted' && runner.os == 'Linux'"
    )
    assert prepare['uses'] == './.cache/central-test-gate/actions/prepare-hosted-docker'
    checkout = next(s for s in steps if s.get('name') == 'Check out central actions')
    assert checkout['with']['ref'] == "${{ inputs.qualification-repository != '' && inputs.qualification-central-ref || 'main' }}"
    assert checkout['with']['persist-credentials'] is False
    assert set(checkout['with']['sparse-checkout'].split()) == {
        'actions/junit-gate', 'actions/prepare-hosted-docker',
    }
    assert steps.index(checkout) < steps.index(prepare)
    for name in ('Download candidate base and source for qualification', 'Set up dependencies'):
        assert steps.index(prepare) < next(i for i, s in enumerate(steps) if s.get('name') == name)
    assert not any('/actions/prepare-hosted-docker@' in s.get('uses', '') for s in steps)


def action_step():
    action = document('actions/prepare-hosted-docker/action.yml')
    assert action['runs']['using'] == 'composite'
    step, = action['runs']['steps']
    assert step['if'] == "runner.environment == 'github-hosted' && runner.os == 'Linux'"
    assert step['shell'] == 'bash'
    assert '${{' not in step['run']
    return step['run']


@pytest.mark.parametrize('failure', [False, True])
def test_preparation_removes_only_unused_host_sdks_and_logs_space(tmp_path, failure):
    script = action_step()
    binary = tmp_path / 'bin'
    binary.mkdir()
    targets = ('/usr/local/lib/android', '/usr/share/dotnet')
    for target in (*targets, '/opt/hostedtoolcache', '/var/lib/docker', '/var/lib/containerd'):
        directory = tmp_path / target.lstrip('/')
        directory.mkdir(parents=True)
        (directory / 'keep-or-remove').touch()
    sudo = binary / 'sudo'
    sudo.write_text(
        '#!/bin/bash\nset -eu\n'
        'echo cleanup\n'
        'if [ "$FAIL" = true ]; then exit 23; fi\n'
        'test "$*" = "rm -rf /usr/local/lib/android /usr/share/dotnet"\n'
        'shift 2\n'
        'for path; do rm -rf "$FIXTURE$path"; done\n'
    )
    sudo.chmod(0o755)
    result = subprocess.run(
        ['bash', '-e', '-o', 'pipefail', '-c', script], capture_output=True, text=True,
        env={**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}',
             'FIXTURE': str(tmp_path), 'FAIL': str(failure).lower()},
    )
    assert result.returncode == (23 if failure else 0), result.stderr
    before, after = result.stdout.split('\ncleanup\n')
    assert 'Filesystem' in before
    assert ('Filesystem' in after) == (not failure)
    for target in targets:
        assert (tmp_path / target.lstrip('/')).exists() == failure
    for target in ('/opt/hostedtoolcache', '/var/lib/docker', '/var/lib/containerd'):
        assert (tmp_path / target.lstrip('/') / 'keep-or-remove').exists()
