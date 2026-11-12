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

from test_standards_route import workflow_jobs

CHECKSUM = 'e1eaf20469e586f65849256a0be6a58dd7a3e93ae8db68bc74dd7af5ca0fe7f6'


class NativeMiseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = workflow_jobs()['standards-native']['steps']
        cls.step = next(step for step in cls.steps if step.get('name') == 'Install mise if missing on macOS')
        cls.install_step = json.loads(subprocess.check_output(
            ['yq', '-o=json', str(Path(__file__).resolve().parents[1] / 'actions/macos-tools/action.yml')],
            text=True))['runs']['steps'][0]
        cls.source_script = (Path(__file__).resolve().parents[1] / 'actions/macos-tools/install-mise.sh').read_text()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.runner = self.root / 'runner'
        self.runner.mkdir()
        self.github_path = self.root / 'github-path'
        self.github_path.write_text('existing-path\n')
        for command in ('bash', 'mktemp', 'rm', 'mkdir', 'tar', 'gzip', 'shasum', 'cp'):
            (self.bin / command).symlink_to(shutil.which(command))
        self.stub('uname', 'case "$1" in -s) echo Darwin;; -m) echo "${ARCH:-arm64}";; esac\n')
        self.stub('curl', '''printf '%s\n' "$*" >> "$CALLS"
if [ "${DOWNLOAD_FAIL:-false}" = true ]; then exit 22; fi
while [ "$1" != -o ]; do shift; done
cp "$ARCHIVE" "$2"
''')
        self.archive = self.root / 'fixture.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            payload = b'#!/bin/sh\necho fixture-mise\n'
            member = tarfile.TarInfo('mise/bin/mise')
            member.size = len(payload)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(payload))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.script = self.source_script.replace(CHECKSUM, digest)
        self.env = {**os.environ, 'PATH': str(self.bin), 'RUNNER_TEMP': str(self.runner),
                    'GITHUB_PATH': str(self.github_path), 'ARCHIVE': str(self.archive),
                    'CALLS': str(self.root / 'calls')}

    def stub(self, name, script):
        path = self.bin / name
        path.write_text('#!/bin/bash\nset -euo pipefail\n' + script)
        path.chmod(0o755)

    def run_install(self, **overrides):
        return subprocess.run(['/bin/bash', '-euc', self.script], env={**self.env, **overrides},
                              capture_output=True, text=True)

    def test_pin_checksum_and_order_match_the_official_release_contract(self):
        self.assertEqual(self.install_step['shell'], 'bash')
        script = self.source_script
        self.assertIn('version=2026.11.1', script)
        self.assertIn('checksum=' + CHECKSUM, script)
        self.assertIn('SHASUMS256.txt', script)
        self.assertIn('https://github.com/jdx/mise/releases/download/v$version/mise-v$version-macos-arm64.tar.gz', script)
        self.assertIn("--proto '=https' --tlsv1.2 -fsSL --retry 3", script)
        self.assertLess(script.index('shasum -a 256 -c -'), script.index('tar -xzf'))
        self.assertLess(self.steps.index(self.step), next(
            i for i, step in enumerate(self.steps) if step.get('name') == 'Set up native Checks'))
        self.assertNotIn('sudo', script)
        self.assertNotRegex(script, r'curl[^\n]*\|\s*(?:ba)?sh')

    def test_existing_mise_is_untouched_without_download_or_path_change(self):
        self.stub('mise', 'exit 99\n')
        before = (self.bin / 'mise').read_bytes()
        result = self.run_install(ARCH='unsupported')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.bin / 'mise').read_bytes(), before)
        self.assertFalse((self.root / 'calls').exists())
        self.assertEqual(list(self.runner.iterdir()), [])
        self.assertEqual(self.github_path.read_text(), 'existing-path\n')

    def test_missing_mise_is_verified_extracted_and_added_to_path(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = self.runner / 'mise/bin/mise'
        self.assertTrue(os.access(installed, os.X_OK))
        self.assertEqual(installed.read_text(), '#!/bin/sh\necho fixture-mise\n')
        self.assertEqual(self.github_path.read_text(), f'existing-path\n{installed.parent}\n')
        self.assertEqual(list(self.runner.glob('mise.*')), [])
        calls = (self.root / 'calls').read_text()
        self.assertIn('mise-v2026.11.1-macos-arm64.tar.gz', calls)
        self.assertIn('--proto =https --tlsv1.2 -fsSL --retry 3', calls)

    def test_bad_checksum_failed_download_and_unsupported_platform_fail_closed(self):
        for mode in ('checksum', 'download', 'platform'):
            with self.subTest(mode=mode):
                overrides = {}
                if mode == 'checksum':
                    self.script = re.sub(r'checksum=[0-9a-f]{64}', 'checksum=' + '0' * 64, self.script)
                elif mode == 'download':
                    overrides['DOWNLOAD_FAIL'] = 'true'
                else:
                    overrides['ARCH'] = 'x86_64'
                result = self.run_install(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.runner / 'mise/bin/mise').exists())
                self.assertEqual(list(self.runner.iterdir()), [])
                self.assertEqual(self.github_path.read_text(), 'existing-path\n')


if __name__ == '__main__':
    unittest.main()
