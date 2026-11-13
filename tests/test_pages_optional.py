import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_pages_workflow import ACTION, BUILD, DEPLOY, DIRECTORY, ROOT, document, shell, step


def validate_cache(root, path='.cache/tool', os_name='Linux', arch='X64'):
    output = root / 'directory-output'
    output.write_text('')
    action = document('actions/pages-directory/action.yml')
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', action['runs']['steps'][0]['run']],
                            cwd=root, capture_output=True, text=True,
                            env={**os.environ, 'DIRECTORY_PATH': path, 'PURPOSE': 'cache',
                                 'GITHUB_ACTION_PATH': str(DIRECTORY), 'GITHUB_OUTPUT': str(output),
                                 'RUNNER_OS': os_name, 'RUNNER_ARCH': arch})
    return result, dict(line.split('=', 1) for line in output.read_text().splitlines())


def test_directory_action_maps_inputs_and_all_outputs_to_the_real_validator():
    action = document('actions/pages-directory/action.yml')
    assert action['runs']['using'] == 'composite'
    assert set(action['inputs']) == {'path', 'purpose'}
    assert action['inputs']['path']['required'] is True
    assert action['inputs']['purpose']['default'] == 'site'
    validate, = action['runs']['steps']
    assert validate['id'] == 'validate'
    assert validate['shell'] == 'bash'
    assert validate['env'] == {'DIRECTORY_PATH': '${{ inputs.path }}', 'PURPOSE': '${{ inputs.purpose }}'}
    assert validate['run'] == 'bash "$GITHUB_ACTION_PATH/validate.sh"'
    for name in ('path', 'relative-path', 'cache-prefix'):
        assert action['outputs'][name]['value'] == '${{ steps.validate.outputs.' + name + ' }}'


def test_only_build_receives_the_scoped_app_token_and_node_keeps_the_job_token():
    mint = step('Mint read-only source token')
    assert mint['if'] == step('Validate App credentials')['if'] == "inputs.app-repositories != ''"
    assert step('Validate App credentials')['env'] == {
        'APP_REPOSITORIES': '${{ inputs.app-repositories }}',
        'APP_CLIENT_ID_PRESENT': "${{ secrets.app-client-id != '' }}",
        'APP_PRIVATE_KEY_PRESENT': "${{ secrets.app-private-key != '' }}",
    }
    assert mint['id'] == 'app-token'
    assert mint['uses'] == 'actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510'
    assert mint['with'] == {
        'client-id': '${{ secrets.app-client-id }}', 'private-key': '${{ secrets.app-private-key }}',
        'owner': '${{ github.repository_owner }}', 'repositories': '${{ inputs.app-repositories }}',
        'permission-contents': 'read',
    }
    consumers = [s['name'] for s in BUILD['steps'] if 'steps.app-token.outputs.token' in json.dumps(s)]
    assert consumers == ['Build the site']
    assert step('Build the site')['env']['NODE_AUTH_TOKEN'] == '${{ github.token }}'
    assert 'secrets.' not in json.dumps(DEPLOY)
    assert BUILD['steps'].index(step('Set up dependencies')) < BUILD['steps'].index(mint)
    assert BUILD['steps'].index(mint) < BUILD['steps'].index(step('Build the site'))


@pytest.mark.parametrize('repositories,client,key,valid', [
    ('one\ntwo', 'client', 'private-key', True), ('one,two', 'client', 'private-key', True),
    ('one', '', 'private-key', False), ('one', 'client', '', False),
    (' ', 'client', 'private-key', False), (' , \n,\t', 'client', 'private-key', False),
    ('\ufeff,\u00a0', 'client', 'private-key', False),
])
def test_partial_or_empty_app_scope_fails_without_logging_secrets(tmp_path, repositories, client, key, valid):
    result, output = shell(tmp_path, 'Validate App credentials', APP_REPOSITORIES=repositories,
                           APP_CLIENT_ID_PRESENT=str(bool(client)).lower(),
                           APP_PRIVATE_KEY_PRESENT=str(bool(key)).lower())
    assert (result.returncode == 0) == valid
    assert output == ''
    assert 'private-key' not in result.stdout + result.stderr
    assert 'client' not in result.stdout + result.stderr
    if not valid:
        assert 'nonempty repository list and both App secrets' in result.stderr


def test_cache_restore_save_guards_order_and_content_addressed_keys():
    before, restore = step('Validate cache directory'), step('Restore build cache')
    restored, after = step('Verify restored cache directory'), step('Verify cache directory after build')
    plan, save = step('Plan cache save'), step('Save build cache')
    for call in (before, restored):
        assert call['if'] == "inputs.cache-path != ''"
    for call in (before, restored, after):
        assert call['uses'] == 'Rubio-Enterprises/.github/actions/pages-directory@main'
        assert call['with'] == {'path': '${{ inputs.cache-path }}', 'purpose': 'cache'}
    assert restore['if'] == "inputs.cache-path != ''"
    assert restore['with'] == {
        'path': './${{ steps.cache-path.outputs.relative-path }}/',
        'key': '${{ steps.cache-path.outputs.cache-prefix }}-restore-${{ github.run_id }}-${{ github.run_attempt }}',
        'restore-keys': '${{ steps.cache-path.outputs.cache-prefix }}-',
    }
    assert after['if'] == "${{ !cancelled() && steps.cache-path.outcome == 'success' && steps.build.outcome != 'skipped' }}"
    assert plan['if'] == "${{ !cancelled() && steps.cache-after.outcome == 'success' }}"
    assert plan['env'] == {
        'CACHE_PREFIX': '${{ steps.cache-path.outputs.cache-prefix }}',
        'CACHE_DIGEST': "${{ hashFiles(format('./{0}/**', steps.cache-after.outputs.relative-path)) }}",
        'MATCHED_KEY': '${{ steps.cache.outputs.cache-matched-key }}',
    }
    assert save['if'] == "${{ !cancelled() && steps.cache-key.outputs.save == 'true' }}"
    assert save['with'] == {'path': './${{ steps.cache-after.outputs.relative-path }}/',
                            'key': '${{ steps.cache-key.outputs.key }}'}
    assert restore['uses'] == 'actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
    assert save['uses'] == 'actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
    ordered = [step('Set up dependencies'), before, restore, restored, step('Build the site'), after, plan, save,
               step('Verify the site directory'), step('Upload Pages artifact')]
    assert [BUILD['steps'].index(s) for s in ordered] == sorted(BUILD['steps'].index(s) for s in ordered)
    assert not any('continue-on-error' in s for s in ordered)


def test_cache_path_creates_empty_directory_with_stable_per_path_and_runner_namespace(tmp_path):
    result, first = validate_cache(tmp_path)
    assert result.returncode == 0, result.stderr
    assert first['path'] == str(tmp_path / '.cache/tool')
    assert first['relative-path'] == '.cache/tool'
    assert first['cache-prefix'].startswith('pages-cache-v1-Linux-X64-')
    assert (tmp_path / '.cache/tool').is_dir()
    assert list((tmp_path / '.cache/tool').iterdir()) == []
    result, alias = validate_cache(tmp_path, './.cache/tool/')
    assert result.returncode == 0, result.stderr
    assert alias == first
    for path, os_name, arch in [('.cache/second', 'Linux', 'X64'), ('.cache/tool', 'macOS', 'ARM64'),
                                ('.cache/tool', 'Linux', 'ARM64')]:
        result, other = validate_cache(tmp_path, path, os_name, arch)
        assert result.returncode == 0, result.stderr
        assert other['cache-prefix'] != first['cache-prefix']


@pytest.mark.parametrize('path', ['~/.cache/tool', '#cache', ' leading', 'trailing '])
def test_literal_cache_names_stay_inside_the_workspace(tmp_path, path):
    result, output = validate_cache(tmp_path, path)
    assert result.returncode == 0, result.stderr
    assert output['relative-path'] == path
    assert Path(output['path']).is_relative_to(tmp_path)
    assert (tmp_path / path).is_dir()


@pytest.mark.parametrize('path', ['', '.', './', '..', '../outside', '/etc', '.cache/../other',
                                  'cache\nOUTPUT=bad', 'cache\rOUTPUT=bad', 'cache*', 'cache?',
                                  'cache[0]', 'cache{one,two}', 'cache\\name', '!cache'])
def test_cache_rejects_unsafe_or_glob_paths_before_creating_them(tmp_path, path):
    result, output = validate_cache(tmp_path, path)
    assert result.returncode != 0
    assert '::error::pages:' in result.stderr
    assert output == {}


@pytest.mark.parametrize('kind', ['file', 'root-link', 'parent-link', 'entry-link', 'dangling-link'])
def test_cache_validation_refuses_links_before_restore_and_after_build(tmp_path, kind):
    result, _ = validate_cache(tmp_path)
    assert result.returncode == 0
    cache = tmp_path / '.cache/tool'
    if kind == 'file':
        cache.rmdir()
        cache.write_text('not a directory')
    elif kind in ('root-link', 'parent-link'):
        cache.rmdir()
        target = tmp_path / 'elsewhere'
        target.mkdir()
        link = cache if kind == 'root-link' else cache.parent
        if kind == 'parent-link':
            link.rmdir()
        link.symlink_to(target, target_is_directory=True)
    else:
        if kind == 'entry-link':
            (tmp_path / 'output').write_text('outside the cache')
        (cache / 'linked').symlink_to(tmp_path / ('missing' if kind == 'dangling-link' else 'output'))
    result, output = validate_cache(tmp_path)
    assert result.returncode != 0
    assert output == {}


@pytest.mark.parametrize('digest,matched,save', [
    ('abc', '', True), ('abc', 'prefix-abc', False), ('def', 'prefix-abc', True), ('', '', False),
    ('', 'prefix-abc', False),
])
def test_save_plan_skips_unchanged_or_empty_content(tmp_path, digest, matched, save):
    result, output = shell(tmp_path, 'Plan cache save', CACHE_PREFIX='prefix',
                           CACHE_DIGEST=digest, MATCHED_KEY=matched)
    assert result.returncode == 0, result.stderr
    assert output == (f'key=prefix-{digest}\nsave=true\n' if save else '')


@pytest.mark.parametrize('mode', ['image', 'compose', 'macos'])
@pytest.mark.parametrize('token', ['job-token', 'app-token'])
@pytest.mark.parametrize('status', [0, 23])
def test_recipe_receives_selected_read_token_package_token_and_configured_cache(tmp_path, mode, token, status):
    binary = tmp_path / 'bin'
    binary.mkdir()
    recipe = binary / 'site-build.sh'
    recipe.write_text('#!/bin/sh\n'
                      'printf "%s|%s|%s|%s|%s" "$GH_TOKEN" "$GITHUB_TOKEN" "$NODE_AUTH_TOKEN" "$SITE_URL" "$BASE_PATH" > received\n'
                      'mkdir -p .cache/tool\n'
                      'printf cached-read > .cache/tool/reads.sqlite\n'
                      f'exit {status}\n')
    recipe.chmod(0o755)
    just = binary / 'just'
    just.write_text('#!/bin/sh\nexec "bin/$1.sh"\n')
    just.chmod(0o755)
    action = ACTION['runs']['steps']
    result = subprocess.run(['bash', '-e', '-c', action[0]['run'] + '\n' + action[1]['run']], cwd=tmp_path,
                            capture_output=True, text=True,
                            env={**os.environ, 'GITHUB_ACTION_PATH': str(ROOT / 'actions/lifecycle'),
                                 'GITHUB_OUTPUT': str(tmp_path / 'output'), 'RUNNER_TEMP': str(tmp_path),
                                 'PATH': f'{binary}:{os.environ["PATH"]}', 'RECIPE': 'site-build', 'MODE': mode,
                                 'GH_TOKEN': token, 'GITHUB_TOKEN': token, 'NODE_AUTH_TOKEN': 'job-token',
                                 'SITE_URL': 'https://example.test', 'BASE_PATH': '/site'})
    assert result.returncode == status, result.stderr
    assert (tmp_path / 'received').read_text() == f'{token}|{token}|job-token|https://example.test|/site'
    verified, values = validate_cache(tmp_path)
    assert verified.returncode == 0, verified.stderr
    digest = hashlib.sha256((tmp_path / '.cache/tool/reads.sqlite').read_bytes()).hexdigest()
    plan, output = shell(tmp_path, 'Plan cache save', CACHE_PREFIX=values['cache-prefix'],
                          CACHE_DIGEST=digest, MATCHED_KEY='previous-content')
    assert plan.returncode == 0, plan.stderr
    assert output == f'key={values["cache-prefix"]}-{digest}\nsave=true\n'
    assert DEPLOY['needs'] == 'build'
    assert 'if' not in DEPLOY
