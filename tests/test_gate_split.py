from itertools import product
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_project_route import run_route, RUNNERS
from test_test_gate import WORKFLOW, ROOT, scripts, report, junit, run_shell, step

MACOS = 'test-gate-macos'


@pytest.mark.parametrize(('private', 'platform', 'override', 'declared', 'expected'), [
    (True, 'linux', None, ['test'], 'test'),
    (False, 'linux', None, ['package', 'test', 'test'], 'test package'),
    (True, 'macos', 'medium', ['package', 'test'], 'test package'),
    (True, 'linux', None, ['e2e', 'typecheck'], 'typecheck e2e'),
    (True, 'linux', None, None, ''),
    (True, 'linux', None, '', ''),
    (True, 'linux', None, [], ''),
    (True, 'linux', None, ['test', 'typecheck', 'e2e', 'package'], 'test typecheck e2e package'),
])
def test_test_route_declared_leg(private, platform, override, declared, expected):
    code, result = run_route(route='test', repository={'private': private, 'custom_properties': {
        'ci-platform': platform, 'ci-size': 'small', 'ci-test-route': override,
        'ci-test-macos': declared,
    }})
    assert code == 0, result
    assert result['calls'] == []
    assert result['output']['platform'] == 'linux'
    assert result['output']['macos-concerns'] == expected
    assert json.loads(result['output']['macos-runner']) == (RUNNERS['macos'] if private else ['macos-26'])


@pytest.mark.parametrize('declared', [['bad'], 'test', 1, False, {}, [None], ['test', 'bad']])
def test_invalid_macos_concerns_fail_closed(declared):
    code, result = run_route(route='test', repository={'custom_properties': {
        'ci-platform': 'linux', 'ci-size': 'small', 'ci-test-route': None,
        'ci-test-macos': declared,
    }})
    assert code != 0
    assert 'ci-test-macos must contain only' in result['error']
    assert result['output'] == {}


@pytest.mark.parametrize(('platform', 'override'), [('macos', None), ('linux', 'macos')])
def test_macos_execution_cannot_declare_an_extra_leg(platform, override):
    code, result = run_route(route='test', repository={'custom_properties': {
        'ci-platform': platform, 'ci-size': 'small', 'ci-test-route': override, 'ci-test-macos': ['test'],
    }})
    assert code != 0
    assert 'whose Test Gate runs on Linux' in result['error']
    assert result['output'] == {}


@pytest.mark.parametrize('route', ['auto', 'linux', 'small', 'docker', 'macos'])
def test_other_routes_ignore_macos_declaration(route):
    code, result = run_route(route=route, repository={'custom_properties': {
        'ci-platform': 'macos', 'ci-size': 'small', 'ci-test-macos': ['bad'],
    }})
    assert code == 0, result
    assert result['output']['macos-concerns'] == ''


@pytest.mark.parametrize('missing', ['ci-platform', 'ci-size', 'ci-test-route', 'ci-test-macos'])
def test_partial_test_metadata_reads_all_properties(missing):
    properties = {'ci-platform': 'linux', 'ci-size': 'small', 'ci-test-route': None, 'ci-test-macos': ['test', 'e2e']}
    code, result = run_route(route='test',
                            repository={'custom_properties': {k: v for k, v in properties.items() if k != missing}},
                            properties=[{'property_name': k, 'value': v} for k, v in properties.items()])
    assert code == 0, result
    assert len(result['calls']) == 1
    assert result['output']['macos-concerns'] == 'test e2e'


@pytest.mark.parametrize(('selected', 'expected'), [
    ('', ['test']), ('test', ['test']), ('package test', ['test', 'package']),
    ('e2e typecheck', ['typecheck', 'e2e']),
    ('package e2e typecheck test', ['test', 'typecheck', 'e2e', 'package']),
])
def test_macos_lifecycle_modes(tmp_path, selected, expected):
    scripts(tmp_path)
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, job=MACOS,
                               MACOS_CONCERNS=selected, PATH=f'{tmp_path / "bin"}:{os.environ["PATH"]}')
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'ran').read_text().splitlines() == expected
    concerns = output.removeprefix('concerns=').strip()
    assert concerns.split() == [name for name in ('test', 'e2e') if name in expected]
    assert junit(tmp_path, concerns=concerns).returncode == 0


@pytest.mark.parametrize('bad', ['absent', 'non-executable', 'dangling', 'directory'])
def test_declared_macos_concern_cannot_be_skipped(tmp_path, bad):
    scripts(tmp_path)
    target = tmp_path / 'bin/test.sh'
    if bad == 'non-executable':
        target.chmod(0o644)
    else:
        target.unlink()
        if bad == 'dangling':
            target.symlink_to(tmp_path / 'missing')
        elif bad == 'directory':
            target.mkdir()
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, job=MACOS, MACOS_CONCERNS='test')
    assert result.returncode != 0
    assert '::error::macOS concern test requires executable' in result.stdout
    assert output.strip() == 'concerns= test'


@pytest.mark.parametrize('selected', ['', 'test'])
def test_macos_clears_stale_evidence_and_requires_executed_cases(tmp_path, selected):
    scripts(tmp_path, empty_test=True)
    report(tmp_path, 'passed.xml')
    result, _ = run_shell('Run Lifecycle Scripts', tmp_path, job=MACOS, MACOS_CONCERNS=selected,
                          PATH=f'{tmp_path / "bin"}:{os.environ["PATH"]}')
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'reports/test').exists()
    assert junit(tmp_path).returncode != 0
    report(tmp_path, 'all-skipped.xml')
    assert junit(tmp_path).returncode != 0


@pytest.mark.parametrize('selected', ['', 'test'])
def test_macos_script_failure_and_shape_absence(tmp_path, selected):
    scripts(tmp_path, fail='test')
    result, _ = run_shell('Run Lifecycle Scripts', tmp_path, job=MACOS, MACOS_CONCERNS=selected,
                          PATH=f'{tmp_path / "bin"}:{os.environ["PATH"]}')
    assert result.returncode == 7
    assert junit(tmp_path, outcome='failure').returncode != 0
    (tmp_path / 'bin/test.sh').unlink()
    if not selected:
        result, output = run_shell('Run Lifecycle Scripts', tmp_path, job=MACOS, MACOS_CONCERNS='')
        assert result.returncode == 0
        assert output.strip() == 'concerns='


@pytest.mark.parametrize('selected', ['', 'test e2e'])
@pytest.mark.parametrize('lingering', [None, 'directory', 'symlink'])
def test_macos_setup_modes_and_checkout_lifetime(tmp_path, selected, lingering):
    scripts(tmp_path)
    setup = tmp_path / 'bin/setup.sh'
    setup.write_text('#!/bin/sh\necho setup >> ran\n')
    setup.chmod(0o755)
    helper = tmp_path / 'macos-recipe.sh'
    helper.write_text('echo "shape $*" >> ran\n')
    if lingering:
        central = tmp_path / '.cache/central-test-gate'
        central.parent.mkdir()
        if lingering == 'symlink':
            central.symlink_to(tmp_path / 'missing')
        else:
            central.mkdir()
    result, _ = run_shell('Set up dependencies', tmp_path, job=MACOS,
                          MACOS_CONCERNS=selected, RUNNER_TEMP=str(tmp_path))
    assert (result.returncode == 0) == (lingering is None), result.stderr
    if lingering is None:
        assert (tmp_path / 'ran').read_text().strip() == ('setup' if selected else 'shape setup')
    else:
        assert not (tmp_path / 'ran').exists()


def test_aggregate_truth_table(tmp_path):
    for qualification, platform, declared, route, linux, macos in product(
            (False, True), ('linux', 'macos'), ('', 'test'),
            ('success', 'failure', 'cancelled', 'skipped'),
            ('success', 'failure', 'cancelled', 'skipped'),
            ('success', 'failure', 'cancelled', 'skipped')):
        result, _ = run_shell('Require all applicable Test Gate legs to succeed', tmp_path, job='test-gate',
                              QUALIFICATION=str(qualification).lower(), QUALIFICATION_RESULT='success',
                              QUALIFICATION_PLATFORM=platform, ROUTE_RESULT=route, PLATFORM=platform,
                              MACOS_CONCERNS=declared, LINUX_RESULT=linux, MACOS_RESULT=macos)
        want_linux = platform != 'macos'
        want_macos = platform == 'macos' or (not qualification and bool(declared))
        expected = ((qualification or route == 'success')
                    and linux == ('success' if want_linux else 'skipped')
                    and macos == ('success' if want_macos else 'skipped'))
        assert (result.returncode == 0) == expected, (qualification, platform, declared, route, linux, macos)
    result, _ = run_shell('Require all applicable Test Gate legs to succeed', tmp_path, job='test-gate',
                          QUALIFICATION='true', QUALIFICATION_RESULT='failure', QUALIFICATION_PLATFORM='',
                          LINUX_RESULT='success', MACOS_RESULT='skipped')
    assert result.returncode != 0


@pytest.mark.parametrize(('platform', 'runner', 'expected'), [
    ('', '["macos-arm64"]', 'macos'), ('', '["macos-26"]', 'macos'),
    ('', '["ubuntu-latest"]', 'linux'), ('linux', '["macos-26"]', 'linux'),
    ('macos', '["custom"]', 'macos'),
])
def test_existing_qualification_callers_infer_platform_once(tmp_path, platform, runner, expected):
    result, output = run_shell('Select qualification platform', tmp_path, job='qualification',
                               QUALIFICATION_PLATFORM=platform, QUALIFICATION_RUNNER=runner, MACOS_CONCERNS='',
                               GITHUB_REPOSITORY='Rubio-Enterprises/standards')
    assert result.returncode == 0, result.stderr
    assert output == f'platform={expected}\n'


def test_split_structure_shared_policy_and_evidence_contract():
    jobs = WORKFLOW['jobs']
    assert jobs[MACOS]['name'] == MACOS
    assert jobs[MACOS]['timeout-minutes'] == 60
    assert jobs['test-gate']['timeout-minutes'] == 5
    assert jobs['test-gate']['name'] == 'test-gate'
    assert jobs['test-gate']['permissions'] == {}
    assert jobs['test-gate']['needs'] == ['route', 'qualification', 'test-gate-linux', MACOS]
    assert 'always()' in jobs['test-gate']['if']
    for name in ('Require successful project routing or an authorized Release Check', 'Check Test Gate policy',
                 'Check out central JUnit action', 'Require JUnit evidence'):
        assert step(name) == step(name, MACOS)
    for name in ('qualification-platform', 'qualification-macos-concerns'):
        assert WORKFLOW['on']['workflow_call']['inputs'][name]['default'] == ''
    assert 'RUNNER_OS' not in step('Run Lifecycle Scripts')['run']
    assert 'RUNNER_OS' not in step('Set up dependencies')['run']
    assert 'docker' not in json.dumps(jobs[MACOS]).lower()
    for job in ('test-gate-linux', MACOS):
        assert 'always()' in jobs[job]['if']
        assert "github.repository != 'Rubio-Enterprises/.github'" in jobs[job]['if']
        for checkout in (s for s in jobs[job]['steps'] if 'actions/checkout@' in s.get('uses', '')):
            assert checkout['with']['persist-credentials'] is False
        assert next(s for s in jobs[job]['steps'] if s.get('with', {}).get('fetch-depth') == 0)


@pytest.mark.parametrize(('filename', 'job'), [('standards', 'standards-macos'), ('test-gate', MACOS)])
def test_download_cache_and_step_scoped_tokens(filename, job):
    workflow = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / f'.github/workflows/{filename}.yml')], text=True))
    steps = workflow['jobs'][job]['steps']
    restore = next(s for s in steps if s.get('id') == 'macos-downloads')
    save = next(s for s in steps if s.get('name') == 'Save macOS tool downloads')
    assert restore['uses'] == 'actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
    assert save['uses'] == 'actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
    assert restore['with'] == {
        'path': '${{ runner.temp }}/macos-tools/cache/downloads-cas',
        'key': "macos-tools-downloads-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('image/mise.lock') }}",
    }
    assert save['with']['path'] == restore['with']['path']
    assert save['with']['key'] == '${{ steps.macos-downloads.outputs.cache-primary-key }}'
    assert "steps.macos-downloads.outputs.cache-hit != 'true'" in save['if']
    assert 'always()' not in save['if']
    install = next(s for s in steps if s.get('name') == 'Install mise if missing on macOS')
    setup = next(s for s in steps if s.get('name') in ('Set up dependencies', 'Set up native Checks'))
    lifecycle = next(s for s in steps if s.get('name') in ('Run Lifecycle Scripts', 'Native Checks'))
    assert steps.index(restore) < steps.index(install) < steps.index(setup) < steps.index(save)
    for s in (setup, lifecycle):
        assert s['env']['GITHUB_TOKEN'] == '${{ github.token }}'


@pytest.mark.parametrize(('recipes', 'command', 'expected'), [
    ('setup-native check-native', 'setup', 'setup-native'),
    ('setup-native setup-macos check-native', 'setup', 'setup-macos'),
    ('setup-macos check-macos', 'setup', 'setup-macos'),
    ('check-native setup-native', 'check', 'check-native'),
    ('check-native check-macos', 'check', 'check-macos'),
])
@pytest.mark.parametrize('failure', [False, True])
def test_recipe_compatibility_selects_from_summary_and_never_retries(tmp_path, recipes, command, expected, failure):
    binary = tmp_path / 'just'
    binary.write_text('#!/bin/sh\n'
                      'echo "$*" >> "$CALLS"\n'
                      'if [ "$1" = --summary ]; then printf "%s\\n" "$RECIPES"; exit 0; fi\n'
                      'exit "$STATUS"\n')
    binary.chmod(0o755)
    result = subprocess.run(['bash', str(ROOT / 'actions/macos-tools/run-recipe.sh'), command],
                            cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}',
                                 'CALLS': str(tmp_path / 'calls'), 'RECIPES': recipes,
                                 'STATUS': '7' if failure else '0'})
    assert result.returncode == (7 if failure else 0), result.stderr
    assert (tmp_path / 'calls').read_text().splitlines() == ['--summary', expected]


def test_recipe_summary_failure_cannot_trigger_execution(tmp_path):
    binary = tmp_path / 'just'
    binary.write_text('#!/bin/sh\necho "$*" >> "$CALLS"\nexit 9\n')
    binary.chmod(0o755)
    result = subprocess.run(['bash', str(ROOT / 'actions/macos-tools/run-recipe.sh'), 'check'],
                            cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}',
                                 'CALLS': str(tmp_path / 'calls')})
    assert result.returncode == 9
    assert (tmp_path / 'calls').read_text().splitlines() == ['--summary']
