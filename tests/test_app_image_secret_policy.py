from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "actions/app-image/trivy-secret.yaml"
THIRD_PARTY = ("usr/local/bin/widevine_cdm/**", "**/site-packages/pywiim/api/constants.py")


def pem(label: str = "RSA PRIVATE KEY") -> str:
    body = base64.b64encode(bytes(range(256)) * 4).decode()
    lines = "\n".join(body[i:i + 64] for i in range(0, 400, 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n"


class ThirdPartyFilesAreNarrowlySkipped(unittest.TestCase):
    def test_each_third_party_path_is_exact_and_explained(self):
        lines = POLICY.read_text().splitlines()
        patterns = re.findall(r'^  - "([^"]+)"$', "\n".join(lines), re.MULTILINE)
        self.assertEqual(tuple(patterns[-2:]), THIRD_PARTY)
        for pattern in THIRD_PARTY:
            comment = lines[lines.index(f'  - "{pattern}"') - 1]
            self.assertTrue(comment.startswith("  # "), pattern)
        self.assertIn("ghcr.io/music-assistant/server", POLICY.read_text())
        self.assertIn("pywiim", POLICY.read_text())
        # no extension or directory wide escape hatch for keys
        for broad in ("**/*.pem", "**/*.key", "**/*.py", "usr/**", "**/site-packages/**"):
            self.assertNotIn(f'"{broad}"', POLICY.read_text())


@unittest.skipUnless(os.environ.get("TRIVY_TEST_BINARY"), "set TRIVY_TEST_BINARY to a Trivy binary")
class RealTrivy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root / "image"
        self.fixture.mkdir()

    def put(self, name: str, content: str) -> None:
        path = self.fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def findings(self) -> set[str]:
        out = self.root / "out.json"
        result = subprocess.run([
            os.environ["TRIVY_TEST_BINARY"], "fs", "--scanners", "secret", "--secret-config", str(POLICY),
            "--cache-dir", str(self.root / "cache"), "--disable-telemetry", "--skip-version-check",
            "--quiet", "--format", "json", "--output", str(out), str(self.fixture),
        ], capture_output=True, text=True, check=False, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertNotIn("PRIVATE KEY-----\n" + pem().splitlines()[1], result.stdout + result.stderr)
        return {f"{r['Target']}" for r in report.get("Results", []) if r.get("Secrets")}

    def test_the_two_third_party_files_pass_and_the_same_key_anywhere_else_fails(self):
        skipped = (
            "usr/local/bin/widevine_cdm/private_key.pem",
            "app/venv/lib/python3.14/site-packages/pywiim/api/constants.py",
        )
        for name in skipped:
            self.put(name, pem())
        self.assertEqual(self.findings(), set())
        planted = (
            "app/private_key.pem",
            "usr/local/bin/other_cdm/private_key.pem",
            "app/venv/lib/python3.14/site-packages/otherpkg/api/constants.py",
            "app/venv/lib/python3.14/site-packages/pywiim/api/other.py",
        )
        for name in planted:
            self.put(name, pem())
        self.assertEqual(self.findings(), set(planted))


if __name__ == "__main__":
    unittest.main()
