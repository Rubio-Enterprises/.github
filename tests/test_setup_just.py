import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / 'actions/setup-just/install.sh').read_text()


class SetupJustTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.runner = self.root / 'runner'
        self.runner.mkdir()
        self.github_path = self.root / 'github-path'
        self.github_path.write_text('existing-path\n')
        for command in ('mktemp', 'rm', 'mkdir', 'tar', 'gzip', 'shasum', 'cp'):
            (self.bin / command).symlink_to(shutil.which(command))
        self.stub('uname', 'case "$1" in -s) echo "$OS";; -m) echo "$ARCH";; esac\n')
        self.stub('curl', '''printf 'curl %s\\n' "$*" >> "$CALLS"
while [ "$1" != -o ]; do shift; done
cp "$ARCHIVE" "$2"
''')
        self.stub('gh', '''printf 'gh %s\\n' "$*" >> "$CALLS"
test "$GH_TOKEN" = fixture-token
if [ "${DOWNLOAD_FAIL:-false}" = true ]; then exit 22; fi
while [ "$1" != --output ]; do shift; done
cp "$ARCHIVE" "$2"
''')
        self.archive = self.root / 'fixture.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            payload = b'#!/bin/sh\necho fixture-just\n'
            member = tarfile.TarInfo('just')
            member.size = len(payload)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(payload))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.script = re.sub(r'checksum=[0-9a-f]{64}', 'checksum=' + digest, INSTALL)
        self.env = {**os.environ, 'PATH': str(self.bin), 'RUNNER_TEMP': str(self.runner),
                    'GITHUB_PATH': str(self.github_path), 'ARCHIVE': str(self.archive),
                    'CALLS': str(self.root / 'calls'), 'GH_TOKEN': 'fixture-token',
                    'OS': 'Linux', 'ARCH': 'x86_64'}

    def stub(self, name, script):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -euo pipefail\n' + script)
        path.chmod(0o755)

    def run_install(self, **overrides):
        return subprocess.run(['/bin/bash', '-euc', self.script],
                              env={**self.env, **overrides}, capture_output=True, text=True)

    def test_action_defaults_to_the_job_token_without_script_interpolation(self):
        action = json.loads(subprocess.check_output(
            ['yq', '-o=json', str(ROOT / 'actions/setup-just/action.yml')], text=True))
        self.assertEqual(action['inputs']['token']['default'], '${{ github.token }}')
        self.assertEqual(action['runs']['steps'][0]['env']['GH_TOKEN'], '${{ inputs.token }}')
        self.assertNotIn('${{', action['runs']['steps'][0]['run'])
        self.assertIn('version=1.59.0', INSTALL)
        self.assertEqual(len(re.findall(r'checksum=[0-9a-f]{64}', INSTALL)), 4)
        self.assertLess(INSTALL.index('shasum -a 256 -c -'), INSTALL.index('tar -xzf'))

    def test_authenticated_download_installs_each_supported_platform(self):
        for os_name, arch, triple in (
            ('Linux', 'x86_64', 'x86_64-unknown-linux-musl'),
            ('Linux', 'aarch64', 'aarch64-unknown-linux-musl'),
            ('Linux', 'arm64', 'aarch64-unknown-linux-musl'),
            ('Darwin', 'arm64', 'aarch64-apple-darwin'),
            ('Darwin', 'x86_64', 'x86_64-apple-darwin'),
        ):
            with self.subTest(platform=(os_name, arch)):
                result = self.run_install(OS=os_name, ARCH=arch)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = (self.root / 'calls').read_text()
                self.assertIn('gh release download --repo casey/just 1.59.0', calls)
                self.assertIn('--pattern just-1.59.0-' + triple + '.tar.gz', calls)
                self.assertNotIn('curl ', calls)
                self.assertNotIn('fixture-token', calls + result.stdout + result.stderr)
                self.assertTrue(os.access(self.runner / 'bin/just', os.X_OK))
                self.assertEqual(list(self.runner.glob('just.*')), [])
                self.assertIn(str(self.runner / 'bin'), self.github_path.read_text())

    def test_missing_token_or_cli_uses_the_existing_curl_fallback(self):
        for mode in ('token', 'gh'):
            with self.subTest(missing=mode):
                if mode == 'gh':
                    (self.bin / 'gh').unlink()
                result = self.run_install(**({'GH_TOKEN': ''} if mode == 'token' else {}))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = (self.root / 'calls').read_text()
                self.assertNotIn('gh ', calls)
                self.assertIn('curl --proto =https --tlsv1.2 -fsSL --retry 3', calls)
                self.assertIn('/1.59.0/just-1.59.0-x86_64-unknown-linux-musl.tar.gz', calls)

    def test_download_checksum_and_platform_failures_never_install_or_update_path(self):
        for mode in ('download', 'checksum', 'platform'):
            with self.subTest(failure=mode):
                overrides = {}
                if mode == 'download':
                    overrides['DOWNLOAD_FAIL'] = 'true'
                elif mode == 'checksum':
                    self.script = re.sub(r'checksum=[0-9a-f]{64}', 'checksum=' + '0' * 64,
                                         self.script)
                else:
                    overrides['OS'] = 'unsupported'
                result = self.run_install(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(self.runner.iterdir()), [])
                self.assertEqual(self.github_path.read_text(), 'existing-path\n')
                self.assertNotIn('curl ', (self.root / 'calls').read_text())


if __name__ == '__main__':
    unittest.main()
