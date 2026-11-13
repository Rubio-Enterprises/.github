import os

import pytest

from test_test_gate import junit, report, run_shell, scripts

CONCERNS = ('test', 'typecheck', 'e2e', 'package')


def shape(root):
    scripts(root)


def execute(root):
    result, output = run_shell('Run Lifecycle Scripts', root, job='test-gate-macos',
                               MACOS_CONCERNS='',
                               PATH=f'{root / "bin"}:{os.environ["PATH"]}')
    values = dict(line.split('=', 1) for line in output.splitlines())
    return result, values


def test_shape_runs_all_concerns_through_just(tmp_path):
    shape(tmp_path)
    result, values = execute(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'ran').read_text().splitlines() == list(CONCERNS)
    assert values == {'concerns': ' test e2e', 'artifact-concerns': '-'.join(CONCERNS),
                      'package': 'true'}
    evidence = junit(tmp_path, values['concerns'])
    assert evidence.returncode == 0, evidence.stderr
    assert evidence.stdout.count('1 executed') == 2


@pytest.mark.parametrize('kind', ['missing', 'empty', 'zero', 'all-skipped', 'failed', 'malformed'])
def test_shape_requires_fresh_executed_e2e_cases(tmp_path, kind):
    shape(tmp_path)
    report(tmp_path, 'passed.xml', 'reports/e2e/stale.xml')
    script = tmp_path / 'bin/e2e.sh'
    script.write_text('#!/bin/sh\necho e2e >> ran\n')
    if kind != 'missing':
        xml = {
            'empty': '', 'zero': '<testsuite tests="0"/>',
            'all-skipped': '<testsuite><testcase><skipped/></testcase></testsuite>',
            'failed': '<testsuite><testcase><failure/></testcase></testsuite>',
            'malformed': '<testsuite>',
        }[kind]
        script.write_text(script.read_text() + 'mkdir -p reports/e2e\n'
                          + f"printf '%s' '{xml}' > reports/e2e/junit.xml\n")
    result, values = execute(tmp_path)
    assert result.returncode == 0, result.stderr
    assert values['concerns'] == ' test e2e'
    assert not (tmp_path / 'reports/e2e/stale.xml').exists()
    assert junit(tmp_path, values['concerns']).returncode != 0


def test_shape_e2e_failure_fails_even_with_passing_reports(tmp_path):
    shape(tmp_path)
    script = tmp_path / 'bin/e2e.sh'
    script.write_text(script.read_text().replace('exit 0', 'exit 7'))
    result, values = execute(tmp_path)
    assert result.returncode != 0
    assert (tmp_path / 'ran').read_text().splitlines() == ['test', 'typecheck', 'e2e']
    assert junit(tmp_path, values['concerns'], outcome='failure').returncode != 0


@pytest.mark.parametrize('concern', CONCERNS)
def test_shape_rejects_non_executable_concerns(tmp_path, concern):
    shape(tmp_path)
    (tmp_path / f'bin/{concern}.sh').chmod(0o644)
    result, _ = execute(tmp_path)
    assert result.returncode != 0
    assert f'macOS concern {concern} requires executable' in result.stdout


@pytest.mark.parametrize('concern', CONCERNS)
def test_shape_selects_only_existing_concerns(tmp_path, concern):
    shape(tmp_path)
    for name in CONCERNS:
        if name != concern:
            (tmp_path / f'bin/{name}.sh').unlink()
    result, values = execute(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'ran').read_text().splitlines() == [concern]
    assert values['artifact-concerns'] == concern
    assert junit(tmp_path, values['concerns']).returncode == 0
