import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_test_gate import ROOT, WORKFLOW, junit, run_shell, scripts, step

READER = ROOT / 'scripts/test-gate-policy.py'


def policy(root, text):
    path = root / '.github/standards.toml'
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)


def read(root, mode=None, declared=''):
    output = root / 'output'
    output.write_text('')
    command = [sys.executable, str(READER)]
    if mode:
        command += ['--select', mode]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True,
                            env={**os.environ, 'GITHUB_OUTPUT': str(output), 'MACOS_CONCERNS': declared})
    return result, dict(line.split('=', 1) for line in output.read_text().splitlines())


@pytest.mark.parametrize('text', [
    '[test-gate.unknown]\noff = "tracked"',
    '[test-gate]\ne2e = false',
    '[test-gate.e2e]',
    '[test-gate.e2e]\noff = ""',
    '[test-gate.e2e]\noff = "   "',
    '[test-gate.e2e]\noff = true',
    '[test-gate.e2e]\noff = 3',
    '[test-gate]\noff = "whole gate"\n[test-gate.e2e]\noff = "tracked"',
])
@pytest.mark.parametrize('mode', [None, 'linux', 'macos'])
def test_invalid_concern_policy_fails_closed(tmp_path, text, mode):
    scripts(tmp_path)
    policy(tmp_path, text)
    result, output = read(tmp_path, mode)
    assert result.returncode != 0
    assert '::error::invalid .github/standards.toml' in result.stderr
    assert output == {}


@pytest.mark.parametrize('concern', ['test', 'typecheck', 'e2e', 'package'])
@pytest.mark.parametrize('mode', [None, 'linux', 'macos'])
def test_stale_declaration_fails_on_every_leg(tmp_path, concern, mode):
    policy(tmp_path, f'[test-gate.{concern}]\noff = "tracked"')
    result, output = read(tmp_path, mode)
    assert result.returncode != 0
    assert f'requires existing bin/{concern}.sh' in result.stderr
    assert output == {}


@pytest.mark.parametrize(('mode', 'declared', 'expected'), [
    ('linux', '', ['test', 'typecheck', 'e2e', 'package']),
    ('macos', '', ['test', 'typecheck', 'e2e', 'package']),
    ('macos', 'package e2e test', ['test', 'e2e', 'package']),
])
def test_no_declaration_preserves_selection(tmp_path, mode, declared, expected):
    scripts(tmp_path)
    result, output = read(tmp_path, mode, declared)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == expected
    assert output['artifact-concerns'] == '-'.join(expected)
    assert output['concerns'].split() == [c for c in expected if c in ('test', 'e2e')]


@pytest.mark.parametrize('concern', ['test', 'typecheck', 'e2e', 'package'])
@pytest.mark.parametrize(('job', 'declared'), [
    ('test-gate-linux', ''), ('test-gate-macos', ''),
    ('test-gate-macos', 'test typecheck e2e package'),
])
def test_concern_optout_keeps_script_but_removes_execution_evidence_and_artifact_name(tmp_path, concern, job, declared):
    scripts(tmp_path)
    policy(tmp_path, f'[test-gate.{concern}]\noff = "Tracked in https://github.com/Rubio-Enterprises/noVNC-ARD/issues/124"')
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, job=job,
                               CI_IMAGE='true', MACOS_CONCERNS=declared,
                               PATH=f'{tmp_path / "bin"}:{os.environ["PATH"]}')
    assert result.returncode == 0, result.stderr
    expected = ['test', 'typecheck', 'e2e', 'package']
    expected = [c for c in expected if c != concern]
    assert (tmp_path / 'ran').read_text().splitlines() == expected if expected else not (tmp_path / 'ran').exists()
    assert (tmp_path / f'bin/{concern}.sh').exists()
    values = dict(line.split('=', 1) for line in output.splitlines())
    assert values['artifact-concerns'] == ('-'.join(expected) or 'none')
    assert values['concerns'].split() == [c for c in expected if c in ('test', 'e2e')]
    assert junit(tmp_path, concerns=values['concerns']).returncode == 0
    assert f'::notice::Test Gate concern {concern} opted out' in result.stderr
    assert 'reason recorded in .github/standards.toml' in result.stderr
    assert ('package' in values) == ('package' in expected)


@pytest.mark.parametrize('job', ['test-gate-linux', 'test-gate-macos'])
def test_staged_policy_reader_source_is_retained(job):
    stage = step('Stage Test Gate policy reader', job)
    assert 'cp .cache/central-test-gate/scripts/test-gate-policy.py ' in stage['run']
    assert READER.is_file(), 'Retain the reader source while the workflow stages it, including on revert'


def test_every_concern_computing_job_uses_one_reader_and_default_branch_delegates():
    for job in ('test-gate-linux', 'test-gate-macos'):
        assert 'test-gate-policy.py' in step('Check Test Gate policy', job)['run']
        lifecycle = step('Run Lifecycle Scripts', job)['run']
        assert f'test-gate-policy.py" --select {job.removeprefix("test-gate-")}' in lifecycle
        assert 'artifact_concerns=' not in lifecycle
        assert 'tomllib' not in str(WORKFLOW['jobs'][job])
        assert 'for step in test typecheck e2e package' not in lifecycle
        steps = WORKFLOW['jobs'][job]['steps']
        stage = step('Stage Test Gate policy reader', job)
        assert steps.index(stage) < steps.index(step('Check Test Gate policy', job))
        assert 'rm -rf .cache/central-test-gate' in stage['run']
    import json
    default = json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / '.github/workflows/default-branch-check.yml')]))
    assert default['jobs']['test-gate-execution']['uses'] == 'Rubio-Enterprises/.github/.github/workflows/test-gate.yml@main'
    assert 'concerns=' not in str(default)
