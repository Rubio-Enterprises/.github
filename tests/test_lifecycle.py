import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_test_gate import ROOT, WORKFLOW, run_shell, scripts, step

ACTION = ROOT / 'actions/lifecycle'
DOCUMENT = json.loads(subprocess.check_output(['yq', '-o=json', str(ACTION / 'action.yml')], text=True))


def stage(root, source=ACTION):
    output = root / 'action-output'
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', DOCUMENT['runs']['steps'][0]['run']],
                            cwd=root, capture_output=True, text=True,
                            env={**os.environ, 'GITHUB_ACTION_PATH': str(source),
                                 'RUNNER_TEMP': str(root), 'GITHUB_OUTPUT': str(output)})
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f'runner={root}/lifecycle/run.sh\n'
    return root / 'lifecycle/run.sh'


@pytest.fixture
def repository(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    just = bin_dir / 'just'
    just.write_text('#!/bin/sh\n'
                    'echo "just $*" >> calls\n'
                    'if [ "$1" = --summary ]; then echo "$RECIPES"; exit "$SUMMARY_STATUS"; fi\n'
                    'if [ "$1" = build ]; then exit "$BUILD_STATUS"; fi\n'
                    'printf "%s" "$GH_TOKEN|$NODE_AUTH_TOKEN|$SITE_URL|$BASE_PATH" > environment\n'
                    'echo recipe-output >> "$GITHUB_OUTPUT"\n'
                    'exit "$RECIPE_STATUS"\n')
    just.chmod(0o755)
    for name in ('setup', 'site-build', 'test'):
        script = bin_dir / f'{name}.sh'
        script.write_text('#!/bin/sh\n'
                          f'echo "script {name}" >> calls\n'
                          'printf "%s" "$GH_TOKEN|$NODE_AUTH_TOKEN|$SITE_URL|$BASE_PATH" > environment\n'
                          'echo recipe-output >> "$GITHUB_OUTPUT"\n'
                          'exit "$RECIPE_STATUS"\n')
        script.chmod(0o755)
    runner = stage(tmp_path)
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
           'GITHUB_OUTPUT': str(tmp_path / 'output'), 'GH_TOKEN': 'read-token',
           'NODE_AUTH_TOKEN': 'package-token', 'SITE_URL': 'https://example.test', 'BASE_PATH': '/site',
           'BUILD_STATUS': '0', 'RECIPE_STATUS': '0', 'SUMMARY_STATUS': '0',
           'RECIPES': 'setup-macos check-macos'}

    def run(mode, recipe, **overrides):
        return subprocess.run(['bash', str(runner), mode, recipe], cwd=tmp_path,
                              env={**env, **overrides}, capture_output=True, text=True)
    return tmp_path, run


@pytest.mark.parametrize('mode,recipe,expected', [
    ('image', 'setup', ['script setup']), ('image', 'site-build', ['script site-build']),
    ('compose', 'setup', ['just build', 'just setup']), ('compose', 'site-build', ['just site-build']),
    ('macos', 'setup', ['just setup-macos']),
    ('macos', 'check', ['just check-macos']),
    ('macos', 'test', ['just test']), ('macos', 'site-build', ['just site-build']),
])
def test_dispatch_commands_environment_outputs_and_no_report_side_effects(repository, mode, recipe, expected):
    root, run = repository
    report = root / 'reports/test/stale.xml'
    report.parent.mkdir(parents=True)
    report.write_text('caller owns report cleanup')
    result = run(mode, recipe)
    assert result.returncode == 0, result.stderr
    assert (root / 'calls').read_text().splitlines() == expected
    assert (root / 'environment').read_text() == 'read-token|package-token|https://example.test|/site'
    assert (root / 'output').read_text() == 'recipe-output\n'
    assert report.read_text() == 'caller owns report cleanup'
    assert '::group::' not in result.stdout


@pytest.mark.parametrize('mode,recipe', [('image', 'setup'), ('compose', 'setup'), ('macos', 'setup'),
                                         ('image', 'site-build'), ('compose', 'site-build'), ('macos', 'test')])
def test_failure_status_is_preserved_without_retry(repository, mode, recipe):
    root, run = repository
    result = run(mode, recipe, RECIPE_STATUS='23')
    assert result.returncode == 23
    calls = (root / 'calls').read_text().splitlines()
    assert len(calls) == (2 if recipe == 'setup' and mode == 'compose' else 1)


def test_build_failure_stops_before_setup(repository):
    root, run = repository
    assert run('compose', 'setup', BUILD_STATUS='31').returncode == 31
    assert (root / 'calls').read_text().splitlines() == ['just build']
    assert not (root / 'environment').exists()


def test_missing_or_nonexecutable_direct_script_fails(repository):
    root, run = repository
    assert run('image', 'absent').returncode != 0
    (root / 'bin/setup.sh').chmod(0o644)
    assert run('image', 'setup').returncode != 0
    assert not (root / 'calls').exists()


@pytest.mark.parametrize('mode,recipe', [('bad', 'test'), ('image', '../test'), ('compose', 'test; echo injected')])
def test_invalid_invocations_cannot_execute(repository, mode, recipe):
    root, run = repository
    result = run(mode, recipe)
    assert result.returncode != 0
    assert '::error::' in result.stderr
    assert not (root / 'calls').exists()


def test_stage_is_self_contained_after_central_checkout_removal(repository):
    root, run = repository
    source = root / 'central/actions'
    shutil.copytree(ACTION, source / 'lifecycle')
    shutil.copytree(ROOT / 'actions/macos-tools', source / 'macos-tools')
    (root / 'action-output').unlink()
    stage(root, source / 'lifecycle')
    shutil.rmtree(root / 'central')
    assert run('macos', 'setup').returncode == 0
    assert (root / 'calls').read_text().splitlines() == ['just setup-macos']


def test_composite_direct_recipe_uses_the_same_dispatcher(repository):
    root, _ = repository
    assert DOCUMENT['runs']['using'] == 'composite'
    assert set(DOCUMENT['inputs']) == {'recipe', 'mode'}
    assert DOCUMENT['inputs']['recipe']['default'] == ''
    assert DOCUMENT['outputs']['runner']['value'] == '${{ steps.stage.outputs.runner }}'
    call = DOCUMENT['runs']['steps'][1]
    assert call['if'] == "inputs.recipe != ''"
    assert call['env'] == {'RECIPE': '${{ inputs.recipe }}', 'MODE': '${{ inputs.mode }}'}
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', call['run']], cwd=root,
                            env={**os.environ, 'RUNNER_TEMP': str(root), 'MODE': 'image',
                                 'RECIPE': 'site-build', 'RECIPE_STATUS': '0',
                                 'GH_TOKEN': '', 'NODE_AUTH_TOKEN': '', 'SITE_URL': '', 'BASE_PATH': '',
                                 'GITHUB_OUTPUT': str(root / 'output')}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (root / 'calls').read_text() == 'script site-build\n'


@pytest.mark.parametrize('job', ['test-gate-linux', 'test-gate-macos'])
def test_gate_retains_loop_policy_outputs_and_grouping(tmp_path, job):
    scripts(tmp_path)
    result, _ = run_shell('Run Lifecycle Scripts', tmp_path, job=job, CI_IMAGE='true',
                           MACOS_CONCERNS='test package' if job.endswith('macos') else '')
    assert result.returncode == 0, result.stderr
    expected = ['test', 'package'] if job.endswith('macos') else ['test', 'typecheck', 'e2e', 'package']
    assert result.stdout.splitlines() == [line for name in expected
                                         for line in (f'::group::bin/{name}.sh', '::endgroup::')]
    body = step('Run Lifecycle Scripts', job)['run']
    assert 'rm -rf "reports/$step"' in body
    assert 'echo \'package=true\' >> "$GITHUB_OUTPUT"' in body
    assert 'just "$step"' not in body
    steps = WORKFLOW['jobs'][job]['steps']
    assert steps.index(step('Stage Lifecycle dispatcher', job)) < steps.index(step('Stage Test Gate policy reader', job))
