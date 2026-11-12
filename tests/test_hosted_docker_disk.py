import json
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOSTED_LINUX = "runner.environment == 'github-hosted' && runner.os == 'Linux'"
COMPOSE = "needs.route.outputs.ci-image != 'true' && "
HOSTED_DOCKER_JOBS = [
    ('test-gate.yml', 'test-gate-linux',
     "steps.policy.outputs.opted-out != 'true' && env.CI_IMAGE != 'true' && " + HOSTED_LINUX,
     "${{ inputs.qualification-repository != '' && inputs.qualification-central-ref || 'main' }}"),
    ('standards.yml', 'standards-linux', COMPOSE + HOSTED_LINUX, 'main'),
    ('bootstrap-test-gate.yml', 'canonical-tests', HOSTED_LINUX, '${{ github.sha }}'),
    ('standards-cache.yml', 'warm',
     COMPOSE + "steps.trivy.outputs.cache-hit != 'true' && " + HOSTED_LINUX, 'main'),
    ('workflow-validation.yml', 'validate', HOSTED_LINUX, '${{ github.sha }}'),
    ('e2e.yml', 'e2e', COMPOSE + HOSTED_LINUX, 'main'),
    ('release-please.yml', 'release-please',
     "steps.pr.outputs.branch != '' && hashFiles('uv.lock') != '' && " + HOSTED_LINUX, 'main'),
    ('app-image-pipeline.yml', 'build-arm64', HOSTED_LINUX, 'main'),
    ('app-image-pipeline.yml', 'smoke', HOSTED_LINUX, 'main'),
    ('app-image-pipeline.yml', 'test', HOSTED_LINUX, 'main'),
    ('copilot-setup-steps.yml', 'copilot-setup-steps', HOSTED_LINUX, '${{ github.sha }}'),
]
JOB_IDS = [f'{row[0]}:{row[1]}' for row in HOSTED_DOCKER_JOBS]


def document(path):
    return json.loads(subprocess.check_output(['yq', '-o=json', str(ROOT / path)], text=True))


def test_hosted_preparation_precedes_candidate_download_and_setup():
    steps = document('.github/workflows/test-gate.yml')['jobs']['test-gate-linux']['steps']
    prepare = next(s for s in steps if s.get('name') == 'Prepare hosted Docker disk')
    assert prepare['if'] == (
        "steps.policy.outputs.opted-out != 'true' && env.CI_IMAGE != 'true' && "
        "runner.environment == 'github-hosted' && runner.os == 'Linux'"
    )
    assert prepare['uses'] == './.cache/central-test-gate/actions/prepare-hosted-docker'
    checkout = next(s for s in steps if s.get('name') == 'Check out central Docker preparation action')
    assert checkout['with']['ref'] == "${{ inputs.qualification-repository != '' && inputs.qualification-central-ref || 'main' }}"
    assert checkout['with']['persist-credentials'] is False
    assert checkout['with']['sparse-checkout'] == 'actions/prepare-hosted-docker'
    assert steps.index(checkout) < steps.index(prepare)
    for name in ('Download candidate base and source for qualification', 'Set up dependencies'):
        assert steps.index(prepare) < next(i for i, s in enumerate(steps) if s.get('name') == name)
    assert not any('/actions/prepare-hosted-docker@' in s.get('uses', '') for s in steps)


def test_central_checkouts_do_not_overlap_repository_execution():
    steps = document('.github/workflows/test-gate.yml')['jobs']['test-gate-linux']['steps']
    setup = next(i for i, s in enumerate(steps) if s.get('name') == 'Set up dependencies')
    lifecycle = next(i for i, s in enumerate(steps) if s.get('id') == 'lifecycle')
    checkouts = [s for s in steps if s.get('with', {}).get('repository') == 'Rubio-Enterprises/.github']
    assert len(checkouts) == 2
    early, junit = checkouts
    prepare = next(s for s in steps if s.get('name') == 'Prepare hosted Docker disk')
    remove = next(s for s in steps if s.get('name') == 'Remove central Docker preparation checkout')
    assert early['if'] == prepare['if'] == remove['if']
    assert early['with']['path'] == junit['with']['path'] == '.cache/central-test-gate'
    assert steps.index(early) < steps.index(prepare) < steps.index(remove) < setup < lifecycle < steps.index(junit)
    assert junit['with']['sparse-checkout'] == 'actions/junit-gate'
    evidence = next(s for s in steps if s.get('name') == 'Require JUnit evidence')
    assert junit['if'] == evidence['if']
    assert steps.index(junit) < steps.index(evidence)


def docker_work(step):
    return (
        re.search(r'\bjust (?:build|setup)(?:\s|$)|\bdocker (?:build|compose|load|pull|run)\b', step.get('run', ''))
        or step.get('uses', '').startswith(('docker/setup-buildx-action@', 'docker/build-push-action@'))
        or ('/actions/app-image@' in step.get('uses', '')
            and step.get('with', {}).get('operation') in ('smoke', 'pull'))
    )


def test_every_hosted_docker_job_in_the_listed_workflows_is_covered():
    discovered = set()
    for workflow in {row[0] for row in HOSTED_DOCKER_JOBS}:
        for name, job in document(f'.github/workflows/{workflow}')['jobs'].items():
            if any(docker_work(step) for step in job.get('steps', [])):
                discovered.add((workflow, name))
    assert discovered == {(row[0], row[1]) for row in HOSTED_DOCKER_JOBS}


@pytest.mark.parametrize('workflow,job,guard,ref', HOSTED_DOCKER_JOBS, ids=JOB_IDS)
def test_hosted_docker_jobs_share_the_temporary_sparse_checkout(workflow, job, guard, ref):
    steps = document(f'.github/workflows/{workflow}')['jobs'][job]['steps']
    checkout, = [s for s in steps if s.get('name') == 'Check out central Docker preparation action']
    prepare, = [s for s in steps if s.get('name') == 'Prepare hosted Docker disk']
    remove, = [s for s in steps if s.get('name') == 'Remove central Docker preparation checkout']
    assert checkout['if'] == prepare['if'] == remove['if'] == guard
    assert checkout['uses'].startswith('actions/checkout@')
    assert checkout['with'] == {
        'repository': 'Rubio-Enterprises/.github',
        'ref': ref,
        'path': '.cache/central-test-gate',
        'persist-credentials': False,
        'sparse-checkout': 'actions/prepare-hosted-docker',
    }
    assert prepare['uses'] == './.cache/central-test-gate/actions/prepare-hosted-docker'
    position = steps.index(checkout)
    assert steps[position:position + 3] == [checkout, prepare, remove]
    assert remove['run'] == 'rm -rf .cache/central-test-gate'
    assert position + 2 < next(i for i, s in enumerate(steps) if docker_work(s))
    assert not any('/actions/prepare-hosted-docker@' in s.get('uses', '') for s in steps)
    if workflow == 'standards.yml':
        assert position + 2 < next(i for i, s in enumerate(steps) if s.get('id') == 'trivy-db')


@pytest.mark.parametrize('workflow,job,guard,ref', HOSTED_DOCKER_JOBS, ids=JOB_IDS)
@pytest.mark.parametrize('symlink', [False, True])
def test_preparation_checkout_is_removed_before_repository_code(tmp_path, symlink, workflow, job, guard, ref):
    steps = document(f'.github/workflows/{workflow}')['jobs'][job]['steps']
    remove = next(s for s in steps if s.get('name') == 'Remove central Docker preparation checkout')
    foreign = tmp_path / 'central'
    foreign.mkdir()
    (foreign / 'README.md').write_text('foreign root file')
    cache = tmp_path / '.cache'
    cache.mkdir()
    checkout = cache / 'central-test-gate'
    if symlink:
        checkout.symlink_to(foreign, target_is_directory=True)
    else:
        checkout.mkdir()
        (checkout / 'README.md').write_text('foreign root file')
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', remove['run']],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not checkout.exists() and not checkout.is_symlink()
    assert (foreign / 'README.md').read_text() == 'foreign root file'


def action_step():
    action = document('actions/prepare-hosted-docker/action.yml')
    assert action['runs']['using'] == 'composite'
    step, = action['runs']['steps']
    assert step['if'] == "runner.environment == 'github-hosted' && runner.os == 'Linux'"
    assert step['shell'] == 'bash'
    assert '${{' not in step['run']
    return step['run']


@pytest.mark.parametrize('failure', [False, True])
def test_preparation_removes_only_unused_host_sdks_and_logs_space(tmp_path, failure):
    script = action_step()
    binary = tmp_path / 'bin'
    binary.mkdir()
    targets = ('/usr/local/lib/android', '/usr/share/dotnet')
    for target in (*targets, '/opt/hostedtoolcache', '/var/lib/docker', '/var/lib/containerd'):
        directory = tmp_path / target.lstrip('/')
        directory.mkdir(parents=True)
        (directory / 'keep-or-remove').touch()
    sudo = binary / 'sudo'
    sudo.write_text(
        '#!/bin/bash\nset -eu\n'
        'echo cleanup\n'
        'if [ "$FAIL" = true ]; then exit 23; fi\n'
        'test "$*" = "rm -rf /usr/local/lib/android /usr/share/dotnet"\n'
        'shift 2\n'
        'for path; do rm -rf "$FIXTURE$path"; done\n'
    )
    sudo.chmod(0o755)
    result = subprocess.run(
        ['bash', '-e', '-o', 'pipefail', '-c', script], capture_output=True, text=True,
        env={**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}',
             'FIXTURE': str(tmp_path), 'FAIL': str(failure).lower()},
    )
    assert result.returncode == (23 if failure else 0), result.stderr
    before, after = result.stdout.split('\ncleanup\n')
    assert 'Filesystem' in before
    assert ('Filesystem' in after) == (not failure)
    for target in targets:
        assert (tmp_path / target.lstrip('/')).exists() == failure
    for target in ('/opt/hostedtoolcache', '/var/lib/docker', '/var/lib/containerd'):
        assert (tmp_path / target.lstrip('/') / 'keep-or-remove').exists()
