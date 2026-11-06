from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = json.loads(subprocess.check_output(
    ['yq', '-o=json', str(ROOT / '.github/workflows/test-gate.yml')], text=True))
STEPS = WORKFLOW['jobs']['test-gate']['steps']
JUNIT = ROOT / 'actions/junit-gate/validate.py'
FIXTURES = ROOT / 'tests/fixtures/junit-gate'


def step(name):
    return next(item for item in STEPS if item.get('name') == name)


def run_shell(name, root, **env):
    output = root / 'output'
    output.write_text('')
    result = subprocess.run(
        ['bash', '-e', '-o', 'pipefail', '-c', step(name)['run']], cwd=root,
        env={**os.environ, 'GITHUB_OUTPUT': str(output), **env},
        capture_output=True, text=True,
    )
    return result, output.read_text()


def junit(root, concerns='test', outcome='success'):
    return subprocess.run(
        [sys.executable, str(JUNIT)], cwd=root,
        env={**os.environ, 'JUNIT_CONCERNS': concerns, 'SUITE_OUTCOME': outcome},
        capture_output=True, text=True,
    )


def report(root, fixture, location='reports/test/junit.xml'):
    target = root / location
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / fixture, target)
    return target


@pytest.mark.parametrize(('fixture', 'executed'), [
    ('passed.xml', 1), ('mixed.xml', 1), ('nested.xml', 2),
])
def test_junit_counts_leaf_cases_not_parent_totals(tmp_path, fixture, executed):
    report(tmp_path, fixture)
    result = junit(tmp_path)
    assert result.returncode == 0, result.stderr
    assert f'{executed} executed' in result.stdout


@pytest.mark.parametrize('fixture', [
    'all-skipped.xml', 'failed.xml', 'error.xml', 'malformed.xml',
])
def test_junit_rejects_invalid_evidence(tmp_path, fixture):
    report(tmp_path, fixture)
    result = junit(tmp_path)
    assert result.returncode != 0
    assert '::error::' in result.stderr


@pytest.mark.parametrize('xml', [
    '<testsuite tests="4"/>', '<html><testcase/></html>',
    '<testsuite errors="1"><testcase/></testsuite>',
    '<testsuite failures="1"><testcase/></testsuite>',
    '<testsuite failures="-1"><testcase/></testsuite>',
    '<testsuite errors="abc"><testcase/></testsuite>',
    '<testsuite><error/></testsuite>',
])
def test_junit_rejects_missing_cases_and_failed_summary_attributes(tmp_path, xml):
    target = tmp_path / 'reports/test/junit.xml'
    target.parent.mkdir(parents=True)
    target.write_text(xml)
    assert junit(tmp_path).returncode != 0


def test_junit_aggregates_nested_monorepo_reports_once(tmp_path):
    target = report(tmp_path, 'nested.xml', 'reports/test/packages/a/results.xml')
    report(tmp_path, 'mixed.xml', 'reports/test/packages/b/results.xml')
    (tmp_path / 'reports/test/linked.xml').symlink_to(target)
    result = junit(tmp_path)
    assert result.returncode == 0, result.stderr
    assert '3 executed' in result.stdout
    assert '3 skipped' in result.stdout


def test_junit_checks_every_file_and_each_required_concern(tmp_path):
    report(tmp_path, 'passed.xml')
    report(tmp_path, 'malformed.xml', 'reports/test/nested/invalid.xml')
    assert junit(tmp_path).returncode != 0
    (tmp_path / 'reports/test/nested/invalid.xml').unlink()
    assert junit(tmp_path, 'test e2e').returncode != 0
    report(tmp_path, 'passed.xml', 'reports/e2e/results.xml')
    assert junit(tmp_path, 'test e2e').returncode == 0


@pytest.mark.parametrize('outcome', ['failure', 'cancelled', 'skipped', ''])
def test_successful_reports_never_mask_an_unsuccessful_script(tmp_path, outcome):
    report(tmp_path, 'passed.xml')
    assert junit(tmp_path, outcome=outcome).returncode != 0
    assert junit(tmp_path, concerns='', outcome=outcome).returncode != 0


def test_absent_reports_fail_but_no_applicable_tests_is_valid(tmp_path):
    assert junit(tmp_path).returncode != 0
    assert junit(tmp_path, concerns='').returncode == 0
    assert junit(tmp_path, concerns='../../bad').returncode != 0


@pytest.mark.parametrize(('policy', 'opted_out'), [
    (None, False), ('', False), ('[test-gate]\n', False),
    ('[test-gate]\noff = "docs only"\n', True),
    ('[app-image]\noff = "base image publisher"\n', False),
    ('[test-gate]\noff = "docs only"\n[app-image]\noff = "not an app"\n', True),
])
def test_policy_has_independent_reasoned_opt_outs(tmp_path, policy, opted_out):
    if policy is not None:
        path = tmp_path / '.github/standards.toml'
        path.parent.mkdir()
        path.write_text(policy)
    result, output = run_shell('Check Test Gate policy', tmp_path)
    assert result.returncode == 0, result.stderr
    assert output == f'opted-out={str(opted_out).lower()}\n'
    assert ('::notice::' in result.stdout) == opted_out


@pytest.mark.parametrize('policy', [
    'not valid TOML', 'test-gate = false', 'app-image = "off"',
    '[test-gate]\noff = true', '[test-gate]\noff = ""',
    '[test-gate]\noff = "   "', '[app-image]\noff = 7',
])
def test_invalid_policy_fails_without_an_opt_out_output(tmp_path, policy):
    path = tmp_path / '.github/standards.toml'
    path.parent.mkdir()
    path.write_text(policy)
    result, output = run_shell('Check Test Gate policy', tmp_path)
    assert result.returncode != 0
    assert output == ''
    assert '::error::invalid .github/standards.toml' in result.stderr


def scripts(root, *, empty_test=False, fail=None):
    (root / 'bin').mkdir()
    for name in ('test', 'typecheck', 'e2e', 'package'):
        evidence = ''
        if name in ('test', 'e2e') and not (name == 'test' and empty_test):
            evidence = (f'mkdir -p reports/{name}\n'
                        f"printf '<testsuite><testcase/></testsuite>' > reports/{name}/junit.xml\n")
        path = root / f'bin/{name}.sh'
        path.write_text(f'#!/bin/sh\necho {name} >> ran\n{evidence}exit {7 if fail == name else 0}\n')
        path.chmod(0o755)
    just = root / 'bin/just'
    just.write_text('#!/bin/sh\nexec "bin/$1.sh"\n')
    just.chmod(0o755)


@pytest.mark.parametrize(('image', 'os_name', 'expected'), [
    ('true', 'Linux', ['test', 'typecheck', 'e2e', 'package']),
    ('false', 'Linux', ['test', 'typecheck', 'e2e', 'package']),
    ('false', 'macOS', ['test']),
])
def test_workflow_executes_only_the_applicable_lifecycle_scripts(tmp_path, image, os_name, expected):
    scripts(tmp_path)
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, CI_IMAGE=image, RUNNER_OS=os_name,
                               PATH=f'{tmp_path / "bin"}:{os.environ["PATH"]}')
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'ran').read_text().splitlines() == expected
    concerns = output.removeprefix('concerns=').strip()
    assert concerns == ('test' if os_name == 'macOS' else 'test e2e')
    assert junit(tmp_path, concerns=concerns).returncode == 0


def test_stale_junit_cannot_prove_a_no_op_script_executed(tmp_path):
    scripts(tmp_path, empty_test=True)
    report(tmp_path, 'passed.xml')
    result, _ = run_shell('Run Lifecycle Scripts', tmp_path, CI_IMAGE='true', RUNNER_OS='Linux')
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'reports/test').exists()
    assert junit(tmp_path).returncode != 0


def test_non_executable_scripts_and_lifecycle_failures_are_not_skipped(tmp_path):
    scripts(tmp_path, fail='typecheck')
    result, _ = run_shell('Run Lifecycle Scripts', tmp_path, CI_IMAGE='true', RUNNER_OS='Linux')
    assert result.returncode == 7
    assert (tmp_path / 'ran').read_text().splitlines() == ['test', 'typecheck']
    (tmp_path / 'bin/test.sh').chmod(0o644)
    result, _ = run_shell('Run Lifecycle Scripts', tmp_path, CI_IMAGE='true', RUNNER_OS='Linux')
    assert result.returncode != 0


def test_no_scripts_is_vacuous_success(tmp_path):
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, CI_IMAGE='true', RUNNER_OS='Linux')
    assert result.returncode == 0, result.stderr
    assert output.strip() == 'concerns='
    assert junit(tmp_path, concerns='').returncode == 0


def test_setup_uses_native_scripts_or_literal_host_recipes(tmp_path):
    binary = tmp_path / 'bin'
    binary.mkdir()
    (binary / 'setup.sh').write_text('#!/bin/sh\necho image >> ran\n')
    (binary / 'setup.sh').chmod(0o755)
    (binary / 'just').write_text('#!/bin/sh\necho "$*" >> ran\n')
    (binary / 'just').chmod(0o755)
    for image, os_name, expected in [('true', 'Linux', ['image']),
                                     ('false', 'Linux', ['build', 'setup']),
                                     ('false', 'macOS', ['setup-native'])]:
        (tmp_path / 'ran').unlink(missing_ok=True)
        result, _ = run_shell('Set up dependencies', tmp_path, CI_IMAGE=image, RUNNER_OS=os_name,
                              PATH=f'{binary}:{os.environ["PATH"]}')
        assert result.returncode == 0, result.stderr
        assert (tmp_path / 'ran').read_text().splitlines() == expected


def test_injected_identity_routes_fail_closed_and_retains_arms_label():
    jobs = WORKFLOW['jobs']
    assert WORKFLOW['on'].keys() == {'pull_request', 'merge_group', 'workflow_call'}
    assert WORKFLOW['permissions'] == {'contents': 'read', 'packages': 'read'}
    assert jobs['route']['with'] == {'route': 'test'}
    gate = jobs['test-gate']
    assert gate['name'] == 'test-gate'
    assert gate['needs'] == 'route'
    assert 'always()' in gate['if']
    assert "github.repository != 'Rubio-Enterprises/.github'" in gate['if']
    assert 'fromJSON(needs.route.outputs.runner)' in gate['runs-on']
    assert '.docker' not in gate['runs-on']
    assert gate['timeout-minutes'] == 30
    dependency = jobs['dependency-review']
    assert 'needs' not in dependency
    assert dependency['timeout-minutes'] == 10
    assert dependency['steps'][0]['with'] == {'fail-on-severity': 'high'}
    assert 'public' in dependency['if']
    for checkout in (s for s in STEPS if 'actions/checkout@' in s.get('uses', '')):
        assert checkout['with']['persist-credentials'] is False
    checkout = next(s for s in STEPS if 'actions/checkout@' in s.get('uses', ''))
    assert checkout['with']['fetch-depth'] == 0
    assert 'runner.os == \'Linux\'' in step('Log in to GHCR for dev image builds')['if']
    assert 'pull_request_target' not in json.dumps(WORKFLOW)
    assert 'id-token' not in json.dumps(WORKFLOW)
    assert 'toJSON(secrets)' not in json.dumps(WORKFLOW)


def test_route_failure_and_unauthorized_qualification_stop_before_checkout(tmp_path):
    name = 'Require successful project routing or an authorized Release Check'
    result, _ = run_shell(name, tmp_path, QUALIFICATION_REPOSITORY='', ROUTE_RESULT='failure')
    assert result.returncode != 0
    result, _ = run_shell(name, tmp_path, QUALIFICATION_REPOSITORY='org-ops', ROUTE_RESULT='skipped',
                          GITHUB_REPOSITORY='Rubio-Enterprises/other')
    assert result.returncode != 0
    result, _ = run_shell(name, tmp_path, QUALIFICATION_REPOSITORY='../../bad', ROUTE_RESULT='skipped',
                          GITHUB_REPOSITORY='Rubio-Enterprises/standards')
    assert result.returncode != 0


def test_qualification_exercises_production_orchestration_and_candidate_parser():
    assert step('Require JUnit evidence')['uses'] == './.cache/central-test-gate/actions/junit-gate'
    assert step('Check out central JUnit action')['with']['ref'] == "${{ inputs.qualification-repository != '' && inputs.qualification-central-ref || 'main' }}"
    assert not any('/actions/junit-gate@' in item.get('uses', '') for item in STEPS)
    assert 'docker compose build dev' in step('Select candidate base or native config')['run']
    assert step('Run Lifecycle Scripts')['id'] == 'lifecycle'
    assert step('Clean up dependency volumes and services')['if'].startswith('${{ always()')
    assert 'docker compose down --volumes --remove-orphans' in step('Clean up dependency volumes and services')['run']
