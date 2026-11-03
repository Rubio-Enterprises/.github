from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETRY = ROOT / "actions/build-service/retry.sh"
CONSUMERS = [ROOT / "actions/dev-container-image/build-service.sh", ROOT / "actions/app-image/lib.sh"]


class Structure(unittest.TestCase):
    def test_the_function_is_defined_once_and_both_clients_source_that_file(self):
        definitions = [path for path in (ROOT / "actions").rglob("*.sh") if re.search(r"^retry_unavailable\(\) \{", path.read_text(), re.M)]
        self.assertEqual(definitions, [RETRY])
        for consumer in CONSUMERS:
            with self.subTest(consumer.name):
                text = consumer.read_text()
                self.assertIn("../build-service/retry.sh", text)
                self.assertNotIn("retry_unavailable() {", text)
        # Both sourced paths resolve from where the scripts live in the downloaded action checkout
        self.assertIn('source "$ASSETS/../build-service/retry.sh"', CONSUMERS[0].read_text())
        self.assertIn('source "$(dirname "${BASH_SOURCE[0]}")/../build-service/retry.sh"', CONSUMERS[1].read_text())

    def test_the_script_only_defines_the_function(self):
        text = RETRY.read_text()
        self.assertEqual(re.findall(r"^[a-z_]+\(\) \{", text, re.M), ["retry_unavailable() {"])
        self.assertEqual([line for line in text.splitlines() if line and not line.startswith(("#", " ", "}", "retry_unavailable"))], [])


class Behaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "bin").mkdir()
        sleeper = self.root / "bin/sleep"
        sleeper.write_text('#!/usr/bin/env bash\necho "$*" >> "$RUNNER_TEMP/sleeps"\n')
        sleeper.chmod(0o755)
        # A command that fails with the given message for its first FAILURES runs, then succeeds
        flaky = self.root / "bin/flaky"
        flaky.write_text('''#!/usr/bin/env bash
count="$(cat "$RUNNER_TEMP/count" 2>/dev/null || echo 0)"; echo $((count + 1)) > "$RUNNER_TEMP/count"
if (( count < ${FAILURES:-0} )); then echo "${MESSAGE:-rpc error: code = Unavailable desc = graceful_stop}" >&2; exit "${STATUS:-1}"; fi
echo ran-ok
''')
        flaky.chmod(0o755)
        self.env = {**os.environ, "RUNNER_TEMP": str(self.root), "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    def run_retry(self, **env):
        return subprocess.run(["bash", "-c", f"set -euo pipefail; source {RETRY}; retry_unavailable flaky"],
                              env={**self.env, **env}, capture_output=True, text=True)

    def runs(self) -> int:
        count = self.root / "count"
        return int(count.read_text()) if count.exists() else 0

    def test_success_runs_once_and_leaves_no_log(self):
        result = self.run_retry()
        self.assertEqual((result.returncode, self.runs()), (0, 1))
        self.assertEqual(sorted(path.name for path in self.root.glob("build-service-attempt.*")), [])

    def test_a_closed_connection_is_retried_once_after_the_delay(self):
        result = self.run_retry(FAILURES="1", BUILD_SERVICE_RETRY_DELAY="7")
        self.assertEqual((result.returncode, self.runs()), (0, 2))
        self.assertIn("::warning::The Build Service closed the connection; retrying once", result.stdout)
        self.assertEqual((self.root / "sleeps").read_text().split(), ["7"])

    def test_the_default_delay_is_twenty_seconds(self):
        self.assertEqual(self.run_retry(FAILURES="1").returncode, 0)
        self.assertEqual((self.root / "sleeps").read_text().split(), ["20"])

    def test_a_daemon_that_stays_down_fails_the_second_attempt_with_its_status(self):
        result = self.run_retry(FAILURES="2", STATUS="3")
        self.assertEqual((result.returncode, self.runs()), (3, 2))

    def test_any_other_failure_is_returned_without_a_retry(self):
        result = self.run_retry(FAILURES="1", MESSAGE="ERROR: failed to solve: process did not complete", STATUS="5")
        self.assertEqual((result.returncode, self.runs()), (5, 1))
        self.assertFalse((self.root / "sleeps").exists())


if __name__ == "__main__":
    unittest.main()
