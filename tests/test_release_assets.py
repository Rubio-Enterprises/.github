import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]


def archive(member, payload):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as tar:
        entry = tarfile.TarInfo(member)
        entry.size, entry.mode = len(payload), 0o755
        tar.addfile(entry, io.BytesIO(payload))
    return stream.getvalue()


@pytest.fixture
def release_fixture(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(ROOT / 'actions', source / 'actions')
    ref = os.environ.get('RELEASE_TEST_REF')
    if ref:
        for file in ('setup-just/install.sh', 'macos-tools/install-mise.sh', 'build-service/tools.mjs'):
            content = subprocess.check_output(['git', 'show', f'{ref}:actions/{file}'])
            (source / 'actions' / file).write_bytes(content)
        workflow = subprocess.check_output(['git', 'show', f'{ref}:.github/workflows/secret-scan.yml'], text=True)
        parsed = json.loads(subprocess.check_output(['yq', '-o=json'], input=workflow, text=True))
        step = next(s for s in parsed['jobs']['trufflehog']['steps'] if s.get('name') == 'Install trufflehog')
        (source / 'baseline-trufflehog.sh').write_text(step['run'])
    pins_path = source / 'actions/release-asset/pins.json'
    pins = json.loads(pins_path.read_text())
    assets = {}
    for key, pin in pins.items():
        tool = key.rsplit('-', 2)[0]
        version = pin['tag'].removeprefix('v')
        versions = {'just': f'just {version}', 'mise': f'{version} macos-arm64',
                    'trufflehog': f'trufflehog {version}',
                    'crane': version, 'docker-buildx': f'github.com/docker/buildx v{version} abc'}
        payload = f'#!/bin/sh\necho "{versions[tool]}"\n'.encode()
        content = archive(pin['member'], payload) if 'member' in pin else payload
        pin['sha256'] = hashlib.sha256(content).hexdigest()
        name = pin['asset'].replace('{version}', version)
        assets[(pin['repo'], pin['tag'], name)] = content
    pins_path.write_text(json.dumps(pins))
    cert, key = tmp_path / 'ca.pem', tmp_path / 'ca.key'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(key), '-out', str(cert), '-subj', '/CN=release fixture',
                    '-addext', 'subjectAltName=DNS:api.github.com,DNS:github.com,DNS:release-assets.githubusercontent.com'],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    state = {'requests': [], 'status': 200, 'message': 'fixture refusal', 'corrupt': False, 'redirect': True, 'downloads': []}
    indexed = list(assets.items())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_CONNECT(self):
            self.send_response(200)
            self.end_headers()
            self.connection = context.wrap_socket(self.connection, server_side=True)
            self.rfile = self.connection.makefile('rb')
            self.wfile = self.connection.makefile('wb')
            self.close_connection = True
            self.handle_one_request()

        def reply(self, status, body=b'', **headers):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = self.headers['Host']
            auth = self.headers.get('Authorization')
            state['requests'].append((host, self.path, auth, self.headers.get('Accept')))
            if host == 'release-assets.githubusercontent.com':
                assert auth is None
                content = indexed[int(self.path.rsplit('/', 1)[1])][1]
                self.reply(200, b'corrupt' if state['corrupt'] else content)
                return
            if host != 'api.github.com' or '/releases/download/' in self.path:
                self.reply(403)
                return
            if auth not in ('Bearer fixture-token', 'token fixture-token'):
                self.reply(401)
                return
            if state['status'] != 200:
                self.reply(state['status'], json.dumps({'message': state['message']}).encode())
                return
            for i, ((repo, tag, name), content) in enumerate(indexed):
                if self.path == f'/repos/{repo}/releases/tags/{tag}':
                    entries = [{'id': j, 'name': n, 'url': f'https://api.github.com/repos/{r}/releases/assets/{j}',
                                'browser_download_url': f'https://github.com/{r}/releases/download/{t}/{n}',
                                'state': 'uploaded', 'size': len(c)}
                               for j, ((r, t, n), c) in enumerate(indexed) if (r, t) == (repo, tag)]
                    self.reply(200, json.dumps({'tag_name': tag, 'assets': entries}).encode(), **{'Content-Type': 'application/json'})
                    return
                if self.path == f'/repos/{repo}/releases/assets/{i}':
                    state['downloads'].append((repo, tag, name))
                    if state['redirect']:
                        self.reply(302, Location=f'https://release-assets.githubusercontent.com/asset/{i}')
                    else:
                        self.reply(200, b'corrupt' if state['corrupt'] else content)
                    return
            self.reply(404, b'{"message":"unexpected API path"}')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bin_dir, runner = tmp_path / 'bin', tmp_path / 'runner'
    bin_dir.mkdir()
    runner.mkdir()
    commands = ('bash', 'dirname', 'mktemp', 'rm', 'mkdir', 'tar', 'gzip', 'shasum', 'mv', 'chmod', 'jq', 'curl', 'node', 'tr', 'grep', 'sha256sum', 'cp')
    for command in commands:
        (bin_dir / command).symlink_to(shutil.which(command))
    (bin_dir / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo "$TEST_OS";; -m) echo "$TEST_ARCH";; esac\n')
    (bin_dir / 'uname').chmod(0o755)
    env = {**os.environ, 'PATH': str(bin_dir), 'RUNNER_TEMP': str(runner), 'GITHUB_PATH': str(tmp_path / 'github-path'),
           'GH_TOKEN': 'fixture-token', 'GH_HOST': 'github.com', 'GH_PROMPT_DISABLED': '1',
           'SSL_CERT_FILE': str(cert), 'CURL_CA_BUNDLE': str(cert), 'NODE_EXTRA_CA_CERTS': str(cert), 'NODE_USE_ENV_PROXY': '1', 'TEST_OS': 'Linux', 'TEST_ARCH': 'x86_64',
           'HTTPS_PROXY': f'http://127.0.0.1:{server.server_port}', 'https_proxy': f'http://127.0.0.1:{server.server_port}',
           'NO_PROXY': '', 'no_proxy': '', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64'}
    env.pop('GITHUB_TOKEN', None)
    env.pop('GH_DEBUG', None)
    yield source, pins, state, env, bin_dir
    server.shutdown()
    server.server_close()
    thread.join()


def run_installer(fixture, tool):
    source, _, _, env, _ = fixture
    if tool == 'crane' or tool == 'docker-buildx':
        module = (source / 'actions/build-service/tools.mjs').as_uri()
        code = f"import {{installTools, CLIENT_TOOLS}} from {json.dumps(module)}; await installTools(process.env, CLIENT_TOOLS.filter(t => t.name === '{tool}'));"
        return subprocess.run(['node', '--input-type=module', '-e', code], env=env, text=True, capture_output=True)
    scripts = {'just': 'setup-just/install.sh', 'mise': 'macos-tools/install-mise.sh'}
    if tool == 'mise':
        env.update(TEST_OS='Darwin', TEST_ARCH='arm64')
    if tool == 'trufflehog' and (source / 'baseline-trufflehog.sh').exists():
        env['TRUFFLEHOG_VERSION'] = 'v3.95.5'
        args = ['bash', str(source / 'baseline-trufflehog.sh')]
    elif tool == 'trufflehog':
        args = ['bash', str(source / 'actions/release-asset/install.sh'), tool, env['RUNNER_TEMP'] + '/bin/trufflehog']
    else:
        args = ['bash', str(source / 'actions' / scripts[tool])]
    return subprocess.run(args, env=env, text=True, capture_output=True)


@pytest.mark.parametrize('transport', ['gh', 'curl'])
@pytest.mark.parametrize('tool', ['just', 'mise', 'trufflehog', 'crane', 'docker-buildx'])
def test_authenticated_install_and_cross_host_redirect(release_fixture, transport, tool):
    _, _, state, env, bin_dir = release_fixture
    if transport == 'gh':
        gh = bin_dir / 'gh'
        gh.write_text('#!/bin/sh\necho gh-must-not-be-called >&2\nexit 99\n')
        gh.chmod(0o755)
    result = run_installer(release_fixture, tool)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state['requests']
    assert all(host in ('api.github.com', 'release-assets.githubusercontent.com') for host, *_ in state['requests'])
    assert any(host == 'release-assets.githubusercontent.com' and auth is None for host, _, auth, _ in state['requests'])
    assert any('/releases/assets/' in path and accept == 'application/octet-stream' for _, path, _, accept in state['requests'])
    assert 'fixture-token' not in result.stdout + result.stderr
    assert 'OK' in result.stderr


@pytest.mark.parametrize('transport', ['gh', 'curl'])
@pytest.mark.parametrize('failure', ['token', '403', 'corrupt'])
@pytest.mark.parametrize('tool', ['just', 'mise', 'trufflehog', 'crane', 'docker-buildx'])
def test_release_failures_never_fall_back(release_fixture, transport, failure, tool):
    _, _, state, env, bin_dir = release_fixture
    if transport == 'gh':
        gh = bin_dir / 'gh'
        gh.write_text('#!/bin/sh\necho gh-must-not-be-called >&2\nexit 99\n')
        gh.chmod(0o755)
    if failure == 'token':
        env['GH_TOKEN'] = ''
    elif failure == '403':
        state['status'] = 403
    else:
        state['corrupt'] = True
    result = run_installer(release_fixture, tool)
    assert result.returncode != 0
    assert not list(Path(env['RUNNER_TEMP']).rglob(tool))
    assert all(host in ('api.github.com', 'release-assets.githubusercontent.com') for host, *_ in state['requests'])
    if failure == 'token':
        assert not state['requests']
        assert 'GH_TOKEN' in result.stderr
    elif failure == '403':
        assert len(state['requests']) == 1
    else:
        assert 'checksum' in result.stderr.lower()


@pytest.mark.parametrize('tool', ['just', 'mise', 'trufflehog', 'crane', 'docker-buildx'])
@pytest.mark.parametrize('matching', [True, False])
def test_only_the_exact_installed_version_is_reused(release_fixture, tool, matching):
    _, pins, state, env, bin_dir = release_fixture
    key = 'mise-macOS-ARM64' if tool == 'mise' else f'{tool}-Linux-X64'
    version = pins[key]['tag'].removeprefix('v') if matching else '0.0.0'
    output = {'just': f'just {version}', 'mise': f'{version} macos-arm64', 'trivy': f'Version: {version}',
              'trufflehog': f'trufflehog {version}', 'crane': version,
              'docker-buildx': f'github.com/docker/buildx v{version} abc'}[tool]
    executable = bin_dir / ('docker' if tool == 'docker-buildx' else tool)
    executable.write_text(f'#!/bin/sh\necho "{output}"\n')
    executable.chmod(0o755)
    result = run_installer(release_fixture, tool)
    assert result.returncode == 0, result.stdout + result.stderr
    assert bool(state['requests']) is not matching


@pytest.mark.parametrize(('os_name', 'arch', 'key', 'suffix'), [
    ('Linux', 'x86_64', 'mise-Linux-X64', 'linux-x64'),
    ('Linux', 'aarch64', 'mise-Linux-ARM64', 'linux-arm64'),
    ('Darwin', 'x86_64', 'mise-macOS-X64', 'macos-x64'),
    ('Darwin', 'arm64', 'mise-macOS-ARM64', 'macos-arm64'),
])
def test_mise_bootstrap_platforms(release_fixture, os_name, arch, key, suffix):
    source, pins, state, env, _ = release_fixture
    tag = pins[key]['tag']
    asset = f'mise-{tag}-{suffix}.tar.gz'
    env.update(TEST_OS=os_name, TEST_ARCH=arch)
    result = subprocess.run(['bash', str(source / 'actions/macos-tools/install-mise.sh')],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert state['downloads'] == [('jdx/mise', tag, asset)]
    assert (Path(env['RUNNER_TEMP']) / 'mise/bin/mise').is_file()


def test_literal_pins_and_renovate_digest_tracking():
    pins = json.loads((ROOT / 'actions/release-asset/pins.json').read_text())
    config = json.loads((ROOT / 'renovate.json').read_text())
    manager = next(m for m in config['customManagers'] if m.get('datasourceTemplate') == 'github-release-attachments')
    import re
    pattern = manager['matchStrings'][0].replace('(?<', '(?P<')
    matches = list(re.finditer(pattern, (ROOT / 'actions/release-asset/pins.json').read_text()))
    assert len(matches) == len(pins)
    assert all(len(m['currentDigest']) == 64 and 'latest' not in m['currentValue'] for m in matches)


def test_allowlist_refusal_names_the_organization_without_printing_the_body(release_fixture):
    source, _, state, env, _ = release_fixture
    state.update(status=403, message='Organization IP allow list enabled: secret-fixture-detail')
    result = subprocess.run(['bash', str(source / 'actions/release-asset/fetch.sh'), 'aquasecurity/trivy',
                             'v0.76.1', 'asset', 'a' * 64, env['RUNNER_TEMP'] + '/asset'],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'organization aquasecurity (HTTP 403): IP allow list restriction' in result.stderr
    assert 'secret-fixture-detail' not in result.stderr
    assert len(state['requests']) == 1


@pytest.mark.parametrize('status', [201, 204, 304, 404, 429, 500])
def test_every_non_200_response_fails_without_retry(release_fixture, status):
    _, _, state, _, _ = release_fixture
    state['status'] = status
    result = run_installer(release_fixture, 'just')
    assert result.returncode != 0
    assert f'organization casey (HTTP {status})' in result.stderr
    assert len(state['requests']) == 1


def test_user_curl_config_cannot_enable_credential_forwarding_or_retries(release_fixture, tmp_path):
    _, _, state, env, _ = release_fixture
    curl_home = tmp_path / 'curl-home'
    curl_home.mkdir()
    (curl_home / '.curlrc').write_text('location-trusted\nretry = 3\n')
    env['CURL_HOME'] = str(curl_home)
    result = run_installer(release_fixture, 'just')
    assert result.returncode == 0, result.stderr
    assert len(state['requests']) == 3
    assert state['requests'][-1][0] == 'release-assets.githubusercontent.com'
    assert state['requests'][-1][2] is None


def test_binary_200_without_a_redirect_is_verified(release_fixture):
    _, _, state, _, _ = release_fixture
    state['redirect'] = False
    result = run_installer(release_fixture, 'just')
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stderr
    assert len(state['requests']) == 2
    assert all(host == 'api.github.com' for host, *_ in state['requests'])
