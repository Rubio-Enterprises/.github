import json
import os
from pathlib import Path
import subprocess

import pytest

from test_release_assets import release_fixture, run_installer
from test_standards_route import workflow_jobs

ROOT = Path(__file__).resolve().parents[1]
CHECKSUM = 'e1eaf20469e586f65849256a0be6a58dd7a3e93ae8db68bc74dd7af5ca0fe7f6'


def test_pin_checksum_and_order_match_the_official_release_contract():
    action = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / 'actions/macos-tools/action.yml')], text=True))
    install = action['runs']['steps'][0]
    assert install['shell'] == 'bash'
    assert install['env']['GH_TOKEN'] == '${{ inputs.token }}'
    pin = json.loads((ROOT / 'actions/release-asset/pins.json').read_text())['mise-macOS-ARM64']
    assert pin['tag'] == 'v2026.11.1'
    assert pin['sha256'] == CHECKSUM
    steps = workflow_jobs()['standards-macos']['steps']
    first = next(i for i, step in enumerate(steps) if step.get('name') == 'Install mise if missing on macOS')
    assert first < next(i for i, step in enumerate(steps) if step.get('name') == 'Set up native Checks')
    script = (ROOT / 'actions/macos-tools/install-mise.sh').read_text()
    assert 'sudo' not in script
    assert 'curl' not in script


def test_existing_pinned_mise_is_untouched_without_download(release_fixture):
    _, _, state, env, bin_dir = release_fixture
    mise = bin_dir / 'mise'
    payload = '#!/bin/sh\necho "2026.11.1 macos-arm64"\n'
    mise.write_text(payload)
    mise.chmod(0o755)
    result = run_installer(release_fixture, 'mise')
    assert result.returncode == 0, result.stderr
    assert mise.read_text() == payload
    assert not state['requests']
    assert not list(Path(env['RUNNER_TEMP']).iterdir())


def test_missing_mise_is_verified_extracted_and_added_to_path(release_fixture):
    _, _, state, env, _ = release_fixture
    result = run_installer(release_fixture, 'mise')
    assert result.returncode == 0, result.stdout + result.stderr
    installed = Path(env['RUNNER_TEMP'], 'mise/bin/mise')
    assert os.access(installed, os.X_OK)
    assert '2026.11.1' in installed.read_text()
    assert Path(env['GITHUB_PATH']).read_text() == f'{installed.parent}\n'
    assert not list(Path(env['RUNNER_TEMP']).glob('install-release.*'))
    assert state['requests']


@pytest.mark.parametrize('failure', ['corrupt', '403', 'platform'])
def test_bad_checksum_failed_download_and_unsupported_platform_fail_closed(release_fixture, failure):
    _, _, state, env, _ = release_fixture
    if failure == 'corrupt':
        state['corrupt'] = True
    elif failure == '403':
        state['status'] = 403
    else:
        env.update(TEST_OS='FreeBSD', TEST_ARCH='x86_64')
        result = subprocess.run(['bash', str(release_fixture[0] / 'actions/macos-tools/install-mise.sh')], env=env, capture_output=True)
        assert result.returncode != 0
        assert not state['requests']
        return
    result = run_installer(release_fixture, 'mise')
    assert result.returncode != 0
    assert not list(Path(env['RUNNER_TEMP']).iterdir())
    assert not Path(env['GITHUB_PATH']).exists()
