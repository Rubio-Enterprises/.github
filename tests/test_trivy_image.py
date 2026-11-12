import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
REF = 'example.invalid/image@sha256:' + 'a' * 64


@pytest.fixture
def image_fixture(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(ROOT / 'actions', source / 'actions')
    ref = os.environ.get('RELEASE_TEST_REF')
    if ref:
        for file in ('repository-image/scan.sh', 'app-image/structure.sh'):
            (source / 'actions' / file).write_bytes(subprocess.check_output(['git', 'show', f'{ref}:actions/{file}']))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    runner = tmp_path / 'runner'
    runner.mkdir()
    for command in ('bash', 'dirname', 'mktemp', 'rm', 'mkdir', 'tar', 'gzip', 'mv', 'chmod', 'jq', 'cp', 'cat'):
        (bin_dir / command).symlink_to(shutil.which(command))
    (bin_dir / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo "$TEST_ARCH";; esac\n')
    (bin_dir / 'uname').chmod(0o755)
    payload = b'''#!/bin/bash
if [[ "$1" == --version ]]; then echo "Version: 0.76.1"; exit 0; fi
printf '%s|%s|%s\n' "${TRIVY_SKIP_DB_UPDATE:-}" "${TRIVY_CACHE_DIR:-}" "$*" >> "$RUNNER_TEMP/scans"
for ((i=1; i<=$#; i++)); do
  if [[ "${!i}" == --output ]]; then j=$((i+1)); printf '{}' > "${!j}"; fi
done
echo sensitive-scan-output
exit "${SCAN_STATUS:-0}"
'''
    archive = tmp_path / 'image.tar'
    with tarfile.open(archive, 'w') as tar:
        member = tarfile.TarInfo('usr/local/bin/trivy')
        member.size, member.mode = len(payload), 0o755
        tar.addfile(member, io.BytesIO(payload))
    crane = bin_dir / 'crane'
    crane.write_text('''#!/bin/bash
set -euo pipefail
case "$1" in
  auth)
    [[ "$*" == 'auth login ghcr.io --username fixture-actor --password-stdin' ]]
    [[ "$DOCKER_CONFIG" != "$ORIGINAL_DOCKER_CONFIG" ]]
    token=$(cat)
    [[ "$token" == fixture-job-token ]]
    printf '%s' '{"auths":{"ghcr.io":{"auth":"fixture-credential"}}}' > "$DOCKER_CONFIG/config.json"
    ;;
  export)
    [[ -d "$DOCKER_CONFIG" && -e "$DOCKER_CONFIG/config.json" ]]
    [[ "$DOCKER_CONFIG" != "$ORIGINAL_DOCKER_CONFIG" ]]
    [[ "$*" == *'@sha256:'* ]]
    printf '%s\n' "$*" >> "$RUNNER_TEMP/registry-requests"
    [[ "${REGISTRY_FAIL:-false}" != true ]] || exit 1
    cp "$ARCHIVE" "${!#}"
    ;;
  config)
    printf '%s' '{"config":{"User":"65532","Labels":{"org.opencontainers.image.source":"fixture","org.opencontainers.image.revision":"fixture"}}}'
    ;;
  *) echo unexpected-crane-operation >&2; exit 99 ;;
esac
''')
    crane.chmod(0o755)
    (bin_dir / 'curl').write_text('#!/bin/sh\necho github-download-forbidden >&2\nexit 99\n')
    (bin_dir / 'curl').chmod(0o755)
    credentials = tmp_path / 'credentials'
    credentials.mkdir()
    (credentials / 'config.json').write_text('{"auths":{"ghcr.io":{"auth":"job-credential"}}}')
    env = {**os.environ, 'PATH': str(bin_dir), 'RUNNER_TEMP': str(runner), 'REF': REF,
           'DOCKER_CONFIG': str(credentials), 'ORIGINAL_DOCKER_CONFIG': str(credentials), 'ARCHIVE': str(archive),
           'GH_TOKEN': 'fixture-job-token', 'GITHUB_ACTOR': 'fixture-actor',
           'TEST_ARCH': 'x86_64', 'TRIVY_SKIP_DB_UPDATE': 'true', 'TRIVY_CACHE_DIR': str(tmp_path / 'database')}
    env.pop('TRIVY_BIN', None)
    yield source, env, bin_dir, archive


def run_consumer(fixture, consumer):
    source, env, _, _ = fixture
    script = {'ci': 'repository-image/scan.sh', 'app': 'app-image/structure.sh'}[consumer]
    return subprocess.run(['bash', str(source / 'actions' / script)], env=env, capture_output=True, text=True)


@pytest.mark.parametrize('consumer', ['ci', 'app'])
@pytest.mark.parametrize(('arch', 'platform'), [('x86_64', 'linux/amd64'), ('aarch64', 'linux/arm64')])
def test_both_consumers_extract_the_native_binary_and_keep_scan_environment(image_fixture, consumer, arch, platform):
    _, env, _, _ = image_fixture
    env['TEST_ARCH'] = arch
    result = run_consumer(image_fixture, consumer)
    assert result.returncode == 0, result.stdout + result.stderr
    requests = Path(env['RUNNER_TEMP'], 'registry-requests').read_text().splitlines()
    assert len(requests) == 1
    assert requests[0].startswith(f'export --platform {platform} ghcr.io/aquasecurity/trivy:0.76.1@sha256:')
    scans = Path(env['RUNNER_TEMP'], 'scans').read_text().splitlines()
    assert all(line.startswith('true|' + env['TRIVY_CACHE_DIR'] + '|') for line in scans)
    assert '--scanners secret' in scans[0] and '--image-config-scanners secret' in scans[0]
    assert len(scans) == (1 if consumer == 'ci' else 2)
    assert not list(Path(env['RUNNER_TEMP']).glob('trivy-image.*'))
    if consumer == 'ci':
        assert 'sensitive-scan-output' not in result.stdout + result.stderr
        assert not Path(env['RUNNER_TEMP'], 'ci-scan').exists()
    else:
        assert '--scanners vuln' in scans[1]


@pytest.mark.parametrize('consumer', ['ci', 'app'])
@pytest.mark.parametrize('failure', ['token', 'registry', 'corrupt', 'missing', 'wrong-version', 'scan'])
def test_failures_stay_gating_without_a_release_fallback(image_fixture, consumer, failure):
    _, env, _, archive = image_fixture
    if failure == 'token':
        env['GH_TOKEN'] = ''
    elif failure == 'registry':
        env['REGISTRY_FAIL'] = 'true'
    elif failure == 'corrupt':
        archive.write_bytes(b'corrupt archive')
    elif failure in ('missing', 'wrong-version'):
        with tarfile.open(archive, 'w') as tar:
            payload = b'#!/bin/sh\necho "Version: 0.0.0"\n'
            member = tarfile.TarInfo('wrong-member' if failure == 'missing' else 'usr/local/bin/trivy')
            member.size, member.mode = len(payload), 0o755
            tar.addfile(member, io.BytesIO(payload))
    else:
        env['SCAN_STATUS'] = '1'
    result = run_consumer(image_fixture, consumer)
    assert result.returncode != 0
    assert 'github-download-forbidden' not in result.stdout + result.stderr
    requests = Path(env['RUNNER_TEMP'], 'registry-requests')
    if failure == 'token':
        assert not requests.exists()
    else:
        assert len(requests.read_text().splitlines()) == 1
    assert not list(Path(env['RUNNER_TEMP']).glob('trivy-image.*'))
    assert 'sensitive-scan-output' not in result.stdout + result.stderr


@pytest.mark.parametrize('consumer', ['ci', 'app'])
@pytest.mark.parametrize('matching', [True, False])
def test_exact_version_reuse_is_the_only_shortcut(image_fixture, consumer, matching):
    _, env, bin_dir, _ = image_fixture
    version = '0.76.1' if matching else '0.76.0'
    trivy = bin_dir / 'trivy'
    trivy.write_text(f'''#!/bin/bash
if [[ "$1" == --version ]]; then echo "Version: {version}"; exit 0; fi
for ((i=1; i<=$#; i++)); do
  if [[ "${{!i}}" == --output ]]; then j=$((i+1)); echo '{{}}' > "${{!j}}"; fi
done
''')
    trivy.chmod(0o755)
    if consumer == 'app':
        env['TRIVY_BIN'] = str(trivy)
    result = run_consumer(image_fixture, consumer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(env['RUNNER_TEMP'], 'registry-requests').exists() is not matching


def test_renovate_updates_the_vendor_image_tag_and_digest_together():
    config = json.loads((ROOT / 'renovate.json').read_text())
    manager = next(m for m in config['customManagers'] if m.get('datasourceTemplate') == 'docker')
    match = re.search(manager['matchStrings'][0].replace('(?<', '(?P<'), (ROOT / 'actions/trivy/install.sh').read_text())
    assert match['depName'] == 'ghcr.io/aquasecurity/trivy'
    assert match['currentValue'] == '0.76.1'
    assert match['currentDigest'] == 'sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c'
    rule = next(r for r in config['packageRules'] if 'actions/trivy/install.sh' in r.get('matchFileNames', []))
    assert rule['pinDigests'] is True
    assert all(pin['repo'] != 'aquasecurity/trivy' for pin in json.loads((ROOT / 'actions/release-asset/pins.json').read_text()).values())
