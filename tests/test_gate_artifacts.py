import json
import os
from pathlib import Path
import subprocess
import sys
import resource
import shutil
import signal
from itertools import product

import pytest

from test_test_gate import ROOT, WORKFLOW, junit, report, run_shell, scripts, step

ACTION = ROOT / 'actions/gate-artifacts'
JOBS = ('test-gate-linux', 'test-gate-macos')


def document():
    return json.loads(subprocess.check_output(['yq', '-o=json', str(ACTION / 'action.yml')], text=True))


@pytest.mark.parametrize('job,image,selected', [
    (JOBS[0], 'true', ''), (JOBS[0], 'false', ''),
    (JOBS[1], 'false', ''), (JOBS[1], 'false', 'package test'),
])
@pytest.mark.parametrize('failure', [None, 'test', 'package'])
def test_lifecycle_announces_concern_set_and_package_attempt(tmp_path, job, image, selected, failure):
    scripts(tmp_path, fail=failure)
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, job=job, CI_IMAGE=image,
                               MACOS_CONCERNS=selected, PATH=f'{tmp_path / "bin"}:{os.environ["PATH"]}')
    values = dict(line.split('=', 1) for line in output.splitlines())
    expected = 'test-typecheck-e2e-package' if job == JOBS[0] else ('test-package' if selected else 'test')
    assert values['artifact-concerns'] == expected
    ran = (tmp_path / 'ran').read_text().splitlines()
    assert values.get('package') == ('true' if 'package' in ran else None)
    assert result.returncode == (7 if failure in ran else 0)


@pytest.mark.parametrize('job', JOBS)
def test_no_scripts_has_no_package_attempt(tmp_path, job):
    result, output = run_shell('Run Lifecycle Scripts', tmp_path, job=job, CI_IMAGE='true', MACOS_CONCERNS='')
    assert result.returncode == 0, result.stderr
    values = dict(line.split('=', 1) for line in output.splitlines())
    assert values == {'concerns': '', 'artifact-concerns': 'none'}


def stage(repo, temporary, package='true'):
    return subprocess.run([sys.executable, str(ACTION / 'stage.py')], cwd=repo,
                          env={**os.environ, 'RUNNER_TEMP': str(temporary), 'PACKAGE_ATTEMPT': package},
                          text=True, capture_output=True)


@pytest.mark.parametrize('kind', ['absent', 'empty', 'hidden'])
def test_empty_directories_are_silent(tmp_path, kind):
    repo = tmp_path / 'repo'
    repo.mkdir()
    if kind != 'absent':
        (repo / 'reports').mkdir()
    if kind == 'hidden':
        (repo / 'reports/.env').write_text('not public')
    result = stage(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / 'gate-artifacts').rglob('*'))
    assert 'warning' not in result.stdout + result.stderr


@pytest.mark.parametrize('root_link', [False, True])
def test_staging_never_dereferences_links_or_includes_hidden_files(tmp_path, root_link):
    repo = tmp_path / 'workspace'
    repo.mkdir()
    outside = tmp_path / 'private'
    outside.mkdir()
    (outside / 'token').write_text('secret')
    reports = repo / 'reports'
    if root_link:
        reports.symlink_to(outside, target_is_directory=True)
    else:
        reports.mkdir()
        (reports / 'external').symlink_to(outside, target_is_directory=True)
        (reports / 'external-file').symlink_to(outside / 'token')
        (reports / 'broken').symlink_to(outside / 'absent')
        (reports / 'cycle').symlink_to(reports, target_is_directory=True)
        (reports / 'test').mkdir()
        (reports / 'test/junit.xml').write_text('<testsuite><testcase/></testsuite>')
        (reports / 'alias.xml').symlink_to(reports / 'test/junit.xml')
        (reports / '.env').write_text('secret')
        (reports / '.cache').mkdir()
        (reports / '.cache/credential').write_text('secret')
        (reports / 'cache-alias').symlink_to(reports / '.cache', target_is_directory=True)
        os.mkfifo(reports / 'pipe')
    (repo / 'dist').mkdir()
    (repo / 'dist/app.zip').write_bytes(b'archive')
    (repo / '.cache').mkdir()
    (repo / '.cache/credential').write_text('secret')
    result = stage(repo, tmp_path)
    assert result.returncode == 0, result.stderr
    target = tmp_path / 'gate-artifacts'
    assert sorted(str(p.relative_to(target)) for p in target.rglob('*') if p.is_file()) == (
        ['dist/app.zip'] if root_link else ['dist/app.zip', 'reports/test/junit.xml'])
    assert not any(p.is_symlink() for p in target.rglob('*'))
    assert (target / 'dist/app.zip').stat().st_mode & 0o777 == 0o644


def test_dist_is_staged_only_after_package_and_stale_staging_is_removed(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'dist').mkdir(parents=True)
    (repo / 'dist/app').write_text('package')
    assert stage(repo, tmp_path).returncode == 0
    assert (tmp_path / 'gate-artifacts/dist/app').is_file()
    assert stage(repo, tmp_path, package='').returncode == 0
    assert not list((tmp_path / 'gate-artifacts').rglob('*'))


@pytest.mark.parametrize('directory', [False, True])
def test_unreadable_evidence_is_an_error_not_silent_loss(tmp_path, directory):
    repo = tmp_path / 'repo'
    reports = repo / 'reports'
    reports.mkdir(parents=True)
    target = reports / 'unreadable'
    if directory:
        target.mkdir()
        (target / 'evidence').write_text('evidence')
    else:
        target.write_text('evidence')
    target.chmod(0)
    try:
        result = stage(repo, tmp_path)
        assert result.returncode != 0
        assert 'PermissionError' in result.stderr
        assert not (tmp_path / 'gate-artifacts').exists()
    finally:
        target.chmod(0o755 if directory else 0o644)


def test_insufficient_space_warns_with_sizes_without_consuming_the_reserve(tmp_path):
    repo = tmp_path / 'repo'
    report(repo, 'passed.xml')
    (repo / 'dist').mkdir()
    size = shutil.disk_usage(tmp_path).free + 1
    with (repo / 'dist/large.zip').open('wb') as stream:
        stream.truncate(size)
    result = stage(repo, tmp_path)
    assert result.returncode != 0
    assert '::warning::' in result.stdout
    assert f'dist/ ({size} bytes)' in result.stdout
    assert 'reports/ (' in result.stdout
    assert 'free' in result.stdout
    assert not (tmp_path / 'gate-artifacts').exists()
    assert junit(repo).returncode == 0


def test_copy_failure_removes_partial_staging_and_preserves_junit(tmp_path):
    repo = tmp_path / 'repo'
    original = report(repo, 'passed.xml').read_bytes()
    (repo / 'dist').mkdir()
    (repo / 'dist/app.zip').write_bytes(b'x' * (2 * 1024 * 1024))

    def limit_file_size():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))

    result = subprocess.run([sys.executable, str(ACTION / 'stage.py')], cwd=repo,
                            env={**os.environ, 'RUNNER_TEMP': str(tmp_path), 'PACKAGE_ATTEMPT': 'true'},
                            preexec_fn=limit_file_size, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'File too large' in result.stderr
    assert not (tmp_path / 'gate-artifacts').exists()
    assert (repo / 'reports/test/junit.xml').read_bytes() == original
    assert junit(repo).returncode == 0


@pytest.mark.parametrize('job', JOBS)
@pytest.mark.parametrize('upload', ['failure', 'timeout'])
@pytest.mark.parametrize('fixture,outcome,expected', [
    ('passed.xml', 'success', 0), ('failed.xml', 'success', 1), ('passed.xml', 'failure', 1),
])
def test_upload_failure_or_timeout_cleans_staging_without_changing_junit(tmp_path, job, upload, fixture, outcome, expected):
    repo = tmp_path / 'repo'
    original = report(repo, fixture).read_bytes()
    environment = tmp_path / 'environment'
    environment.touch()
    staged = subprocess.run([sys.executable, str(ACTION / 'stage.py')], cwd=repo,
                            env={**os.environ, 'RUNNER_TEMP': str(tmp_path), 'GITHUB_ENV': str(environment)},
                            text=True, capture_output=True)
    assert staged.returncode == 0, staged.stderr
    assert (tmp_path / 'gate-artifacts/reports/test/junit.xml').is_file()
    sizes = dict(line.split('=', 1) for line in environment.read_text().splitlines())
    assert sizes['GATE_ARTIFACT_SIZES'] == f'reports/ ({len(original)} bytes)'
    if upload == 'failure':
        assert subprocess.run(['bash', '-c', 'exit 23']).returncode == 23
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(['sleep', '10'], timeout=0.05)
    warning, _ = run_shell('Report a failed artifact upload', repo, job=job, **sizes)
    assert warning.returncode == 0
    assert '::warning::' in warning.stdout
    assert sizes['GATE_ARTIFACT_SIZES'] in warning.stdout
    cleaned, _ = run_shell('Clean up Lifecycle artifacts', repo, job=job, RUNNER_TEMP=str(tmp_path))
    assert cleaned.returncode == 0, cleaned.stderr
    assert not (tmp_path / 'gate-artifacts').exists()
    assert (repo / 'reports/test/junit.xml').read_bytes() == original
    assert junit(repo, outcome=outcome).returncode == expected


def test_composite_always_cleanup_removes_staging_but_not_sources(tmp_path):
    repo = tmp_path / 'repo'
    report(repo, 'passed.xml')
    assert stage(repo, tmp_path).returncode == 0
    cleanup = document()['runs']['steps'][-1]
    assert cleanup['if'] == '${{ always() }}'
    result = subprocess.run(['bash', '-e', '-c', cleanup['run']], cwd=repo,
                            env={**os.environ, 'RUNNER_TEMP': str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'gate-artifacts').exists()
    assert junit(repo).returncode == 0


def test_upload_interface_is_shared_warn_only_and_keeps_permissions():
    for name in ('Check out central JUnit action', 'Upload Lifecycle artifacts', 'Report a failed artifact upload'):
        assert step(name) == step(name, JOBS[1])
    assert WORKFLOW['permissions'] == {'contents': 'read', 'packages': 'read'}
    for job in JOBS:
        steps = WORKFLOW['jobs'][job]['steps']
        call = step('Upload Lifecycle artifacts', job)
        assert call['continue-on-error'] is True
        assert call['timeout-minutes'] == 10
        assert call['if'] == "${{ !cancelled() && steps.lifecycle.outcome != 'skipped' }}"
        assert call['uses'] == './.cache/central-test-gate/actions/gate-artifacts'
        assert call['with'] == {
            'name': "${{ github.job }}-${{ steps.lifecycle.outputs.artifact-concerns || 'none' }}",
            'representative': '${{ inputs.qualification-repository }}',
            'package': '${{ steps.lifecycle.outputs.package }}',
        }
        assert 'actions/gate-artifacts' in step('Check out central JUnit action', job)['with']['sparse-checkout']
        assert steps.index(step('Run Lifecycle Scripts', job)) < steps.index(call) < steps.index(step('Require JUnit evidence', job))
        if job == JOBS[0]:
            assert steps.index(call) < steps.index(step('Clean up dependency volumes and services', job))
        assert 'actions' not in WORKFLOW['jobs'][job].get('permissions', {})
        warning = step('Report a failed artifact upload', job)
        assert "steps.artifacts.outcome == 'failure'" in warning['if']
        assert '::warning::' in warning['run']
        assert 'GATE_ARTIFACT_SIZES' in warning['run']
        cleanup = step('Clean up Lifecycle artifacts', job)
        assert cleanup['if'] == '${{ always() }}'
        assert steps.index(call) < steps.index(cleanup) < steps.index(step('Require JUnit evidence', job))


def test_upload_action_has_one_pin_short_retention_and_no_empty_warning():
    steps = document()['runs']['steps']
    uploads = [s for s in steps if 'actions/upload-artifact@' in s.get('uses', '')]
    assert len(uploads) == 2
    pins = {s['uses'] for s in uploads}
    assert pins == {'actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9'}
    for upload in uploads:
        assert upload.get('continue-on-error', False) is False
        assert upload['with']['retention-days'] == 7
        assert upload['with']['if-no-files-found'] == 'ignore'
        assert upload['with'].get('include-hidden-files', False) is False
        assert upload['with'].get('overwrite', False) is False
        assert upload['with']['path'].startswith('${{ runner.temp }}/gate-artifacts/')
        assert 'steps.stage.outcome' in upload['if']
    assert "inputs.package == 'true'" in uploads[1]['if']
    assert steps[-1]['if'] == '${{ always() }}'
    assert 'rm -rf' in steps[-1]['run']
    assert all('timeout-minutes' not in s for s in steps)
    assert steps[0].get('continue-on-error', False) is False
    for workflow in (ROOT / '.github/workflows').glob('*.yml'):
        for line in workflow.read_text().splitlines():
            if 'uses: actions/upload-artifact@' in line:
                assert next(iter(pins)) in line


def test_names_are_unique_for_legs_concerns_attempts_and_representatives():
    steps = document()['runs']['steps']
    names = set()
    for job, concerns, attempt, repo, upload in product(JOBS, ('test', 'test-package', 'test-typecheck-e2e-package'),
                                                       (1, 2), ('', 'org-ops', 'SaneBar'), ('reports', 'package')):
        pattern = next(s['with']['name'] for s in steps if s.get('id') == upload)
        assert pattern == "${{ inputs.name }}-" + upload + "-${{ github.run_attempt }}${{ inputs.representative != '' && format('-{0}', inputs.representative) || '' }}"
        name = f'{job}-{concerns}-{upload}-{attempt}' + (f'-{repo}' if repo else '')
        assert name not in names
        names.add(name)
