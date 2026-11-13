import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / 'actions/macos-tools'
RUNNERS = ('aqua:gotestyourself/gotestsum aqua:nextest-rs/nextest/cargo-nextest '
           'aqua:bats-core/bats-core aqua:cpisciotta/xcbeautify')


def document(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(path)], text=True))


def test_action_contract_and_bootstrap_only_callers():
    action = document(ACTION / 'action.yml')
    assert set(action['inputs']) == {'provision', 'runners', 'standards-source', 'token'}
    assert action['inputs']['provision']['default'] == 'true'
    assert action['inputs']['runners']['default'] == RUNNERS
    assert action['inputs']['token']['default'] == '${{ github.token }}'
    install, provision = action['runs']['steps']
    assert install['shell'] == provision['shell'] == 'bash'
    assert install['run'] == 'bash "$GITHUB_ACTION_PATH/install-mise.sh"'
    assert provision['if'] == "inputs.provision == 'true'"
    assert provision['run'] == 'bash "$GITHUB_ACTION_PATH/provision.sh"'
    assert provision['env']['GITHUB_TOKEN'] == '${{ inputs.token }}'
    assert provision['env']['RUNNERS'] == '${{ inputs.runners }}'
    assert provision['env']['STANDARDS_SOURCE'] == '${{ inputs.standards-source }}'
    for filename, job, guard, setup_name in (
        ('standards', 'standards-macos', None, 'Set up native Checks'),
        ('test-gate', 'test-gate-macos', "steps.policy.outputs.opted-out != 'true'", 'Set up dependencies'),
    ):
        steps = document(ROOT / f'.github/workflows/{filename}.yml')['jobs'][job]['steps']
        checkout = next(s for s in steps if s.get('name') == 'Check out central macOS tools action')
        call = next(s for s in steps if s.get('name') == 'Install mise if missing on macOS')
        remove = next(s for s in steps if s.get('name') == 'Remove central macOS tools checkout')
        assert checkout.get('if') == call.get('if') == remove.get('if') == guard
        assert checkout['with']['repository'] == 'Rubio-Enterprises/.github'
        assert checkout['with']['persist-credentials'] is False
        assert 'actions/macos-tools' in checkout['with']['sparse-checkout']
        assert call['uses'] == './.cache/central-test-gate/actions/macos-tools'
        if filename == 'standards':
            assert call['with'] == {'provision': 'false'}
        else:
            assert call['with']['provision'] == "${{ env.MACOS_CONCERNS != '' && 'true' || 'false' }}"
            assert 'gate-source' in call['with']['standards-source']
        assert 'cp .cache/central-test-gate/actions/macos-tools/run-recipe.sh' in remove['run']
        assert remove['run'].endswith('rm -rf .cache/central-test-gate\n')
        setup = next(s for s in steps if s.get('name') == setup_name)
        assert steps.index(checkout) < steps.index(call) < steps.index(remove) < steps.index(setup)
        assert not any('checksum=' in s.get('run', '') for s in steps)


@pytest.fixture
def provision(tmp_path):
    repo = tmp_path / 'repository with spaces'
    image = repo / 'image'
    image.mkdir(parents=True)
    (image / 'mise.toml').write_text('[tools]\nnode = "24.21.0"\n')
    (image / 'mise.lock').write_text('# repo-lock\n')
    sidecar = image / '.mise/locks/npm-pnpm'
    sidecar.mkdir(parents=True)
    (sidecar / 'package-lock.json').write_text('sidecar\n')
    (repo / 'Dockerfile').write_text('FROM ghcr.io/rubio-enterprises/base:main AS dev\n')
    source = tmp_path / 'candidate source'
    (source / 'mise').mkdir(parents=True)
    (source / 'mise/standards.toml').write_text('[tools]\nbats = "1.14.0"\n')
    (source / 'mise/standards.lock').write_text('# base-lock\n')
    package = tmp_path / 'package'
    shutil.copytree(source / 'mise', package / 'mise')
    archive = tmp_path / 'standards.tgz'
    with tarfile.open(archive, 'w:gz') as stream:
        stream.add(package, arcname='package')
    runner = tmp_path / 'runner'
    runner.mkdir()
    path_file, env_file, calls = [tmp_path / name for name in ('path', 'env', 'calls')]
    path_file.touch()
    env_file.touch()
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    stub = '''#!/usr/bin/env python3
import json, os, pathlib, shutil, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
with open(os.environ['CALLS'], 'a') as stream:
    stream.write(json.dumps({'tool': name, 'args': sys.argv[1:], 'cwd': os.getcwd(),
                            'env': dict(os.environ)}) + '\\n')
if name == 'npm':
    if os.environ.get('FAIL') == 'pack': sys.exit(22)
    dest = sys.argv[sys.argv.index('--pack-destination') + 1]
    shutil.copyfile(os.environ['ARCHIVE'], pathlib.Path(dest) / 'standards.tgz')
elif sys.argv[1] == 'install':
    config = pathlib.Path(os.environ['MISE_OVERRIDE_CONFIG_FILENAMES'])
    if config.name == 'mise.toml':
        assert (config.parent / 'mise.lock').read_text() == '# repo-lock\\n'
        assert (config.parent / '.mise/locks/npm-pnpm/package-lock.json').read_text() == 'sidecar\\n'
        if os.environ.get('FAIL') == 'repo': sys.exit(23)
    else:
        assert (config.parent / 'mise.lock').read_text() == '# base-lock\\n'
        assert sys.argv[3:] == os.environ['RUNNERS'].split()
        if os.environ.get('FAIL') == 'runners': sys.exit(24)
else:
    assert sys.argv[1:] == ['env', '--json']
    if os.environ.get('REAL_MISE'):
        sys.exit(subprocess.run([os.environ['REAL_MISE'], *sys.argv[1:]]).returncode)
    data = os.environ['MISE_DATA_DIR']
    cargo = os.environ.get('CARGO_HOME', os.path.expanduser('~/.cargo'))
    print(json.dumps({'PATH': data + '/installs/node/bin:' + data + '/installs/bats/bin:' + cargo + '/bin:/usr/bin',
                      'GOROOT': data + '/installs/go', 'FIXTURE': os.environ.get('VALUE', 'safe')}))
'''
    for name in ('mise', 'npm'):
        target = binaries / name
        target.write_text(stub)
        target.chmod(0o755)
    env = {**os.environ, 'PATH': f'{binaries}:{os.environ["PATH"]}',
           'GITHUB_WORKSPACE': str(repo), 'RUNNER_TEMP': str(runner),
           'GITHUB_PATH': str(path_file), 'GITHUB_ENV': str(env_file),
           'GITHUB_ACTION_PATH': str(ACTION), 'STANDARDS_SOURCE': str(source),
           'GITHUB_TOKEN': 'fixture-token', 'RUNNERS': RUNNERS,
           'CALLS': str(calls), 'ARCHIVE': str(archive)}

    def run(**overrides):
        result = subprocess.run(['bash', str(ACTION / 'provision.sh')], cwd=repo,
                                env={**env, **overrides}, text=True, capture_output=True)
        records = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, records, path_file.read_text(), env_file.read_text()
    return run, repo, source, runner


def test_candidate_installs_locked_repo_then_only_named_runners_and_exports(provision):
    run, repo, source, runner = provision
    result, calls, paths, env_text = run(RUNNERS='gotestsum bats')
    assert result.returncode == 0, result.stderr
    assert [c['args'] for c in calls] == [
        ['install', '--locked'], ['install', '--locked', 'gotestsum', 'bats'], ['env', '--json']]
    home = runner / 'macos-tools'
    assert calls[0]['env']['MISE_OVERRIDE_CONFIG_FILENAMES'] == str(repo / 'image/mise.toml')
    base_config = str(home / 'base/mise/standards.toml')
    assert calls[1]['env']['MISE_OVERRIDE_CONFIG_FILENAMES'] == base_config
    assert calls[2]['env']['MISE_OVERRIDE_CONFIG_FILENAMES'] == f'{base_config}:{repo}/image/mise.toml'
    expected = {
        'MISE_DATA_DIR': str(home / 'data'), 'MISE_CACHE_DIR': str(home / 'cache'),
        'MISE_STATE_DIR': str(home / 'state'), 'MISE_CONFIG_DIR': str(home / 'config'),
        'MISE_SYSTEM_CONFIG_DIR': str(home / 'system'),
        'MISE_GLOBAL_CONFIG_FILE': str(home / 'config/empty.toml'),
        'MISE_SHIMS_DIR': str(home / 'data/shims'), 'MISE_TRUSTED_CONFIG_PATHS': str(repo),
        'MISE_YES': '1', 'MISE_AUTO_INSTALL': '0',
    }
    for call in calls:
        assert call['env']['GITHUB_TOKEN'] == 'fixture-token'
        for name, value in expected.items():
            assert call['env'][name] == value
    for name, value in expected.items():
        assert f'{name}={value}\n' in env_text
    assert paths.splitlines() == [str(home / 'data/cargo/bin'),
                                  str(home / 'data/installs/bats/bin'), str(home / 'data/installs/node/bin')]
    assert f'GOROOT={home}/data/installs/go\n' in env_text
    assert 'fixture-token' not in result.stdout + result.stderr + env_text + paths
    assert (source / 'mise/standards.lock').read_text() == '# base-lock\n'
    assert not (source / 'mise/mise.lock').exists()
    assert not (repo / '.mise').exists()


def test_tool_homes_are_isolated_before_every_install_and_exported(provision):
    run, _, _, runner = provision
    home = runner / 'macos-tools'
    expected = {
        'RUSTUP_HOME': str(home / 'data/rustup'),
        'CARGO_HOME': str(home / 'data/cargo'),
        'UV_PYTHON_INSTALL_DIR': str(home / 'data/uv-python'),
        'UV_CACHE_DIR': str(home / 'cache/uv'),
        'GOPATH': str(home / 'data/go'),
        'GOMODCACHE': str(home / 'cache/go-mod'),
        'GOCACHE': str(home / 'cache/go-build'),
        'npm_config_cache': str(home / 'cache/npm'),
        'PNPM_HOME': str(home / 'data/pnpm'),
        'npm_config_store_dir': str(home / 'cache/pnpm/store'),
        'npm_config_cache_dir': str(home / 'cache/pnpm'),
        'npm_config_state_dir': str(home / 'state/pnpm'),
    }
    inherited = {name: str(runner / 'shared-user-home' / name) for name in expected}
    result, calls, paths, env_text = run(STANDARDS_SOURCE='', **inherited)
    assert result.returncode == 0, result.stderr
    assert calls[0]['tool'] == 'npm'
    for call in calls:
        for name, value in expected.items():
            assert call['env'][name] == value, (call['args'], name)
    for name, value in expected.items():
        assert f'{name}={value}\n' in env_text
    assert str(home / 'data/cargo/bin') in paths.splitlines()
    assert all(Path(path).is_relative_to(home / 'data') for path in paths.splitlines())
    assert '/usr/bin' not in paths.splitlines()
    assert 'shared-user-home' not in env_text + paths


@pytest.mark.parametrize(('tag', 'version'), [('main', 'latest'), ('v5.1.4', '5.1.4'), ('5.1.4', '5.1.4')])
def test_package_honors_base_release_and_uses_existing_npm_auth_form(provision, tag, version):
    run, repo, _, _ = provision
    (repo / 'Dockerfile').write_text(f'FROM ghcr.io/rubio-enterprises/base:{tag} AS dev\n')
    result, calls, _, _ = run(STANDARDS_SOURCE='')
    assert result.returncode == 0, result.stderr
    pack, *mise = calls
    assert pack['tool'] == 'npm'
    assert pack['args'][:3] == ['pack', f'@rubio-enterprises/standards@{version}', '--ignore-scripts']
    assert pack['env']['npm_config_@rubio-enterprises:registry'] == 'https://npm.pkg.github.com'
    assert pack['env']['npm_config_//npm.pkg.github.com/:_authToken'] == '${NODE_AUTH_TOKEN}'
    assert pack['env']['NODE_AUTH_TOKEN'] == 'fixture-token'
    assert [c['tool'] for c in mise] == ['mise'] * 3


@pytest.mark.parametrize('failure', ['repo', 'runners', 'pack'])
def test_external_failures_stop_without_exporting_partial_environment(provision, failure):
    run, _, _, _ = provision
    result, calls, paths, env = run(FAIL=failure, **({'STANDARDS_SOURCE': ''} if failure == 'pack' else {}))
    assert result.returncode == {'repo': 23, 'runners': 24, 'pack': 22}[failure]
    assert not paths and not env
    assert len(calls) == (2 if failure == 'runners' else 1)


def test_empty_runner_list_cannot_install_all_base_checks(provision):
    run, _, _, _ = provision
    result, calls, paths, env = run(RUNNERS='')
    assert result.returncode != 0
    assert 'runner tool names are required' in result.stderr
    assert not calls and not paths and not env


def test_missing_candidate_lock_fails_without_installing(provision):
    run, _, source, _ = provision
    (source / 'mise/standards.lock').unlink()
    result, calls, paths, env = run()
    assert result.returncode != 0
    assert 'standards.lock' in result.stderr
    assert not calls and not paths and not env


def test_missing_repo_lock_fails_without_installing(provision):
    run, repo, _, _ = provision
    (repo / 'image/mise.lock').unlink()
    result, calls, paths, env = run()
    assert result.returncode != 0
    assert (ACTION / 'provision.sh').is_file()
    assert not calls and not paths and not env


def test_default_runners_resolve_to_the_base_config_with_real_mise(tmp_path):
    config = tmp_path / 'standards.toml'
    tools = (
        'aqua:gotestyourself/gotestsum', 'aqua:nextest-rs/nextest/cargo-nextest',
        'aqua:bats-core/bats-core', 'aqua:cpisciotta/xcbeautify',
    )
    config.write_text('[tools]\n' + ''.join(f'"{tool}" = "1.0.0"\n' for tool in tools))
    default = document(ACTION / 'action.yml')['inputs']['runners']['default']
    result = subprocess.run(
        ['mise', 'ls', '--current', '--json', *default.split()], text=True, capture_output=True,
        env={**os.environ, 'MISE_OVERRIDE_CONFIG_FILENAMES': str(config),
             'MISE_GLOBAL_CONFIG_FILE': str(tmp_path / 'empty.toml'),
             'MISE_DATA_DIR': str(tmp_path / 'data'),
             'MISE_CACHE_DIR': str(tmp_path / 'cache'),
             'MISE_CONFIG_DIR': str(tmp_path / 'config'),
             'MISE_SYSTEM_CONFIG_DIR': str(tmp_path / 'system'),
             'MISE_TRUSTED_CONFIG_PATHS': str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    versions = json.loads(result.stdout)
    assert len(versions) == 4
    assert all(version['version'] == '1.0.0' for version in versions)
    assert all(version['source']['path'] == str(config) for version in versions)


def test_real_mise_environment_keeps_repository_precedence(provision):
    run, repo, source, _ = provision
    (repo / 'image/mise.toml').write_text('[env]\nREPO_ONLY = "repo"\nSHARED = "repo"\n')
    (source / 'mise/standards.toml').write_text('[env]\nBASE_ONLY = "base"\nSHARED = "base"\n')
    result, _, _, env = run(REAL_MISE=shutil.which('mise'))
    assert result.returncode == 0, result.stderr
    assert 'REPO_ONLY=repo\n' in env
    assert 'BASE_ONLY=base\n' in env
    assert 'SHARED=repo\n' in env


def test_real_mise_rust_environment_keeps_cargo_inside_the_path_boundary(provision):
    run, repo, source, runner = provision
    (repo / 'image/mise.toml').write_text('[tools]\nrust = "1.99.1"\n')
    (source / 'mise/standards.toml').write_text('[tools]\n')
    data = runner / 'macos-tools/data'
    (data / 'installs/rust/1.99.1').mkdir(parents=True)
    result, _, paths, env = run(REAL_MISE=shutil.which('mise'))
    assert result.returncode == 0, result.stderr
    assert str(data / 'cargo/bin') in paths.splitlines()
    assert f'CARGO_HOME={data}/cargo\n' in env
    assert f'RUSTUP_HOME={data}/rustup\n' in env
    assert all(Path(path).is_relative_to(data) for path in paths.splitlines())


def test_invalid_environment_cannot_inject_github_commands(provision):
    run, _, _, _ = provision
    result, _, paths, env = run(VALUE='safe\nINJECTED=bad')
    assert result.returncode != 0
    assert 'multiline mise environment' in result.stderr
    assert not paths and not env
