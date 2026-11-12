import json
from pathlib import Path
import subprocess

import pytest

from test_release_assets import release_fixture, run_installer

ROOT = Path(__file__).resolve().parents[1]


def test_action_defaults_to_the_job_token_without_script_interpolation():
    action = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / 'actions/setup-just/action.yml')], text=True))
    assert action['inputs']['token']['default'] == '${{ github.token }}'
    step = action['runs']['steps'][0]
    assert step['env']['GH_TOKEN'] == '${{ inputs.token }}'
    assert '${{' not in step['run']
    pins = json.loads((ROOT / 'actions/release-asset/pins.json').read_text())
    assert len([key for key in pins if key.startswith('just-')]) == 4
    assert {pin['tag'] for key, pin in pins.items() if key.startswith('just-')} == {'1.59.0'}


@pytest.mark.parametrize(('os_name', 'arch', 'triple'), [
    ('Linux', 'x86_64', 'x86_64-unknown-linux-musl'),
    ('Linux', 'aarch64', 'aarch64-unknown-linux-musl'),
    ('Linux', 'arm64', 'aarch64-unknown-linux-musl'),
    ('Darwin', 'arm64', 'aarch64-apple-darwin'),
    ('Darwin', 'x86_64', 'x86_64-apple-darwin'),
])
def test_authenticated_download_installs_each_supported_platform(release_fixture, os_name, arch, triple):
    _, _, state, env, _ = release_fixture
    env.update(TEST_OS=os_name, TEST_ARCH=arch)
    result = run_installer(release_fixture, 'just')
    assert result.returncode == 0, result.stdout + result.stderr
    assert state['downloads'][0][2] == f'just-1.59.0-{triple}.tar.gz'
    assert Path(env['RUNNER_TEMP'], 'bin/just').is_file()
    assert str(Path(env['RUNNER_TEMP'], 'bin')) in Path(env['GITHUB_PATH']).read_text()
    assert not list(Path(env['RUNNER_TEMP']).glob('install-release.*'))


@pytest.mark.parametrize('failure', ['token', '403', 'corrupt', 'platform'])
def test_failures_never_install_or_update_path(release_fixture, failure):
    _, _, state, env, _ = release_fixture
    Path(env['GITHUB_PATH']).write_text('existing-path\n')
    if failure == 'token':
        env['GH_TOKEN'] = ''
    elif failure == '403':
        state['status'] = 403
    elif failure == 'corrupt':
        state['corrupt'] = True
    else:
        env['TEST_OS'] = 'unsupported'
    result = run_installer(release_fixture, 'just')
    assert result.returncode != 0
    assert not list(Path(env['RUNNER_TEMP']).iterdir())
    assert Path(env['GITHUB_PATH']).read_text() == 'existing-path\n'
