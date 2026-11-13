import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def document(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / path)], text=True))


WORKFLOW = document('.github/workflows/pages.yml')
BUILD = WORKFLOW['jobs']['build']
DEPLOY = WORKFLOW['jobs']['deploy']
ACTION = document('actions/lifecycle/action.yml')
DIRECTORY = ROOT / 'actions/pages-directory'


def step(name):
    return next(s for s in BUILD['steps'] if s.get('name') == name)


def shell(root, name, **env):
    output = root / 'output'
    output.write_text('')
    body = step(name).get('run')
    if name == 'Verify the site directory':
        body = 'bash "$GITHUB_ACTION_PATH/validate.sh"'
        env = {**env, 'GITHUB_ACTION_PATH': str(DIRECTORY),
               'DIRECTORY_PATH': env['SITE_PATH'], 'PURPOSE': 'site'}
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', body], cwd=root,
                            env={**os.environ, 'GITHUB_OUTPUT': str(output), **env},
                            capture_output=True, text=True)
    return result, output.read_text()


def test_interface_keeps_base_inputs_and_only_two_optional_capabilities():
    assert set(WORKFLOW['on']) == {'workflow_call'}
    interface = WORKFLOW['on']['workflow_call']
    assert set(interface) == {'inputs', 'secrets'}
    assert set(interface['inputs']) == {'recipe', 'path', 'app-repositories', 'cache-path'}
    assert interface['inputs']['app-repositories']['default'] == ''
    assert interface['inputs']['cache-path']['default'] == ''
    assert interface['secrets'] == {'app-client-id': {'required': False}, 'app-private-key': {'required': False}}
    assert interface['inputs']['recipe']['type'] == 'string'
    assert interface['inputs']['recipe']['default'] == 'package'
    assert interface['inputs']['path']['type'] == 'string'
    assert interface['inputs']['path']['required'] is True


def test_route_is_the_test_gate_route_with_shared_image_detection():
    route = WORKFLOW['jobs']['route']
    gate = document('.github/workflows/test-gate.yml')['jobs']['route']
    assert route['uses'] == gate['uses']
    assert route['with'] == gate['with'] == {'route': 'test', 'inspect-image-inputs': True}
    assert BUILD['needs'] == 'route'
    assert BUILD['runs-on'] == '${{ fromJSON(needs.route.outputs.runner) }}'
    assert BUILD['env']['LIFECYCLE_MODE'] == (
        "${{ needs.route.outputs.ci-image == 'true' && 'image' || "
        "needs.route.outputs.platform == 'macos' && 'macos' || 'compose' }}")


def test_dispatch_is_shared_and_receives_pages_url_and_job_token():
    setup, build = step('Set up dependencies'), step('Build the site')
    assert setup['uses'] == build['uses']
    assert setup['uses'].startswith('Rubio-Enterprises/.github/actions/lifecycle@')
    assert setup['with'] == {'mode': '${{ env.LIFECYCLE_MODE }}', 'recipe': 'setup'}
    assert build['with'] == {'mode': '${{ env.LIFECYCLE_MODE }}', 'recipe': '${{ inputs.recipe }}'}
    for name in ('GH_TOKEN', 'GITHUB_TOKEN', 'NODE_AUTH_TOKEN'):
        assert setup['env'][name] == '${{ github.token }}'
    for name in ('GH_TOKEN', 'GITHUB_TOKEN'):
        assert build['env'][name] == '${{ steps.app-token.outputs.token || github.token }}'
    assert build['env']['NODE_AUTH_TOKEN'] == '${{ github.token }}'
    assert build['env']['SITE_URL'] == '${{ steps.pages.outputs.origin }}'
    assert build['env']['BASE_PATH'] == '${{ steps.pages.outputs.base_path }}'
    assert step('Read Pages settings')['id'] == 'pages'
    assert step('Read Pages settings').get('with', {}).get('enablement', 'false') == 'false'
    for s in BUILD['steps']:
        assert not re.search(r'\bjust (build|setup)|bin/.*\.sh', s.get('run', ''))


def test_permissions_and_failure_graph_keep_build_separate_from_deployment():
    assert WORKFLOW['permissions'] == {'contents': 'read', 'packages': 'read'}
    assert WORKFLOW['jobs']['route']['permissions'] == {'contents': 'read'}
    assert BUILD['permissions'] == {'contents': 'read', 'packages': 'read', 'pages': 'read'}
    assert DEPLOY['permissions'] == {'pages': 'write', 'id-token': 'write'}
    assert DEPLOY['needs'] == 'build'
    assert 'if' not in DEPLOY
    assert len(DEPLOY['steps']) == 1
    assert all('checkout' not in s.get('uses', '') for s in DEPLOY['steps'])
    assert 'environment' not in BUILD
    assert not any('continue-on-error' in s for job in WORKFLOW['jobs'].values() for s in job.get('steps', []))
    for name in ('Require one Pages publisher', 'Read Pages settings', 'Set up dependencies',
                 'Build the site', 'Verify the site directory', 'Upload Pages artifact'):
        assert 'if' not in step(name)
    assert [s['name'] for s in BUILD['steps'] if s['name'] in ('Require one Pages publisher',
            'Read Pages settings', 'Set up dependencies', 'Build the site', 'Verify the site directory',
            'Upload Pages artifact')] == ['Require one Pages publisher', 'Read Pages settings',
            'Set up dependencies', 'Build the site', 'Verify the site directory', 'Upload Pages artifact']


def test_deployment_matches_codewiki_lock_environment_and_action_pins():
    codewiki = document('.github/workflows/codewiki-pages.yml')
    for key in ('concurrency', 'environment', 'runs-on'):
        assert DEPLOY[key] == codewiki['jobs']['deploy'][key]
    assert DEPLOY['concurrency'] == {'group': 'pages', 'cancel-in-progress': False}
    assert 'concurrency' not in WORKFLOW
    assert 'concurrency' not in BUILD
    assert DEPLOY['steps'] == codewiki['jobs']['deploy']['steps']
    upload = step('Upload Pages artifact')
    assert upload['uses'] == codewiki['jobs']['build']['steps'][-1]['uses']
    assert upload['with'] == {'path': '${{ steps.site.outputs.path }}'}
    assert step('Verify the site directory')['id'] == 'site'


def test_first_party_actions_follow_main_and_third_party_actions_are_pinned():
    for job in WORKFLOW['jobs'].values():
        for s in job.get('steps', []):
            if 'uses' in s:
                if s['uses'].startswith('Rubio-Enterprises/.github/actions/'):
                    assert s['uses'].endswith('@main'), s
                else:
                    assert re.search(r'@[a-f0-9]{40}$', s['uses']), s
    assert 'github.ref' not in json.dumps(WORKFLOW)
    assert not any('hook' in name for name in WORKFLOW['on']['workflow_call']['inputs'])


def test_runner_bootstrap_and_compose_teardown_are_not_used_on_images_or_macos():
    no_image = "needs.route.outputs.ci-image != 'true'"
    hosted = no_image + " && runner.environment == 'github-hosted' && runner.os == 'Linux'"
    assert step('Install just')['if'] == no_image
    assert step('Log in to GHCR for dev image builds')['if'] == no_image + " && runner.os == 'Linux'"
    assert step('Prepare hosted Docker disk')['if'] == hosted
    assert step('Prepare container-writable hosted checkout')['if'] == hosted
    macos = step('Install mise if missing on macOS')
    assert macos['if'] == "runner.os == 'macOS'"
    assert macos['with'] == {'provision': 'false'}
    cleanup = step('Clean up dependency volumes and services')
    assert cleanup['if'] == "${{ always() && " + no_image + " && runner.os == 'Linux' }}"
    assert 'docker compose down --volumes --remove-orphans' in cleanup['run']
    assert BUILD['steps'].index(cleanup) > BUILD['steps'].index(step('Upload Pages artifact'))


@pytest.mark.parametrize('kind', ['file', 'directory', 'dangling-link'])
def test_codewiki_publisher_is_rejected_before_setup(tmp_path, kind):
    metadata = tmp_path / 'docs/codewiki/metadata.json'
    metadata.parent.mkdir(parents=True)
    if kind == 'file':
        metadata.write_text('{}')
    elif kind == 'directory':
        metadata.mkdir()
    else:
        metadata.symlink_to('missing')
    result, output = shell(tmp_path, 'Require one Pages publisher', RECIPE='package')
    assert result.returncode != 0
    assert 'CodeWiki already publishes' in result.stderr
    assert output == ''


def test_empty_recipe_is_not_a_build_bypass(tmp_path):
    result, output = shell(tmp_path, 'Require one Pages publisher', RECIPE='')
    assert result.returncode != 0
    assert 'a build recipe is required' in result.stderr
    assert output == ''


@pytest.mark.parametrize('path', ['dist', 'dist/', 'nested/site', 'a site', '-site', "site'quoted", './dist'])
def test_site_path_is_verified_and_emitted_without_shell_interpolation(tmp_path, path):
    site = tmp_path / path
    site.mkdir(parents=True, exist_ok=True)
    (site / 'index.html').write_text('site')
    result, output = shell(tmp_path, 'Verify the site directory', SITE_PATH=path)
    assert result.returncode == 0, result.stderr
    assert dict(line.split('=', 1) for line in output.splitlines()) == {
        'path': str(site.resolve()), 'relative-path': str(site.resolve().relative_to(tmp_path))}
    assert step('Verify the site directory')['with'] == {'path': '${{ inputs.path }}'}
    assert '${{ inputs.path }}' not in (DIRECTORY / 'validate.sh').read_text()


@pytest.mark.parametrize('path', ['', '/', '/etc', '..', '../site', 'site/..', 'site/../dist', '.', './',
                                  './.', 'site\nINJECTED=true', 'site\rINJECTED=true'])
def test_unsafe_paths_and_repository_root_are_rejected(tmp_path, path):
    (tmp_path / 'index.html').write_text('not a site directory')
    result, output = shell(tmp_path, 'Verify the site directory', SITE_PATH=path)
    assert result.returncode != 0
    assert '::error::pages:' in result.stderr
    assert output == ''


@pytest.mark.parametrize('kind', ['missing-directory', 'missing-index', 'file'])
def test_missing_site_or_index_cannot_publish(tmp_path, kind):
    if kind == 'missing-index':
        (tmp_path / 'site').mkdir()
    elif kind == 'file':
        (tmp_path / 'site').write_text('not a directory')
    result, output = shell(tmp_path, 'Verify the site directory', SITE_PATH='site')
    assert result.returncode != 0
    assert 'contain index.html' in result.stderr
    assert output == ''


@pytest.mark.parametrize('kind', ['root', 'parent', 'index', 'nested', 'dangling', 'hidden'])
def test_symlinks_cannot_escape_the_declared_site(tmp_path, kind):
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'index.html').write_text('site')
    outside = tmp_path / 'private-token'
    outside.write_text('do not publish')
    if kind == 'root':
        (tmp_path / 'alias').symlink_to(site, target_is_directory=True)
        path = 'alias'
    elif kind == 'parent':
        nested = site / 'nested'
        nested.mkdir()
        (nested / 'index.html').write_text('site')
        (tmp_path / 'alias').symlink_to(site, target_is_directory=True)
        path = 'alias/nested'
    else:
        path = 'site'
        name = {'index': 'index.html', 'nested': 'sub/leak', 'dangling': 'broken', 'hidden': '.env'}[kind]
        link = site / name
        link.parent.mkdir(exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(outside if kind != 'dangling' else tmp_path / 'missing')
    result, output = shell(tmp_path, 'Verify the site directory', SITE_PATH=path)
    assert result.returncode != 0
    assert 'symlink' in result.stderr
    assert output == ''


@pytest.mark.parametrize('mode', ['image', 'compose', 'macos'])
@pytest.mark.parametrize('status', [0, 23])
def test_real_dispatcher_builds_custom_recipe_or_stops_before_artifact(tmp_path, mode, status):
    binary = tmp_path / 'bin'
    binary.mkdir()
    setup = binary / 'setup.sh'
    setup.write_text('#!/bin/sh\necho setup >> calls\n')
    setup.chmod(0o755)
    build = binary / 'site-build.sh'
    build.write_text(f'#!/bin/sh\necho site-build >> calls\nexit {status}\n')
    build.chmod(0o755)
    just = binary / 'just'
    just.write_text('#!/bin/sh\n'
                    'case "$1" in\n'
                    '  --summary) echo setup-macos ;;\n'
                    '  build) echo build >> calls ;;\n'
                    '  setup-macos) exec bin/setup.sh ;;\n'
                    '  *) exec "bin/$1.sh" ;;\n'
                    'esac\n')
    just.chmod(0o755)
    action_steps = ACTION['runs']['steps']
    call = action_steps[1]['run']
    body = (step('Require one Pages publisher')['run'] + '\n' + action_steps[0]['run'] + '\n'
            + 'RECIPE=setup\n' + call + '\nRECIPE=site-build\n' + call
            + '\necho upload-reached > uploaded\n')
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', body], cwd=tmp_path,
                            env={**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}',
                                 'GITHUB_ACTION_PATH': str(ROOT / 'actions/lifecycle'),
                                 'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(tmp_path / 'output'),
                                 'MODE': mode, 'RECIPE': 'site-build'}, capture_output=True, text=True)
    assert result.returncode == status, result.stderr
    assert (tmp_path / 'calls').read_text().splitlines() == (
        ['build', 'setup', 'site-build'] if mode == 'compose' else ['setup', 'site-build'])
    assert (tmp_path / 'uploaded').exists() == (status == 0)
