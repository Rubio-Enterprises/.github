"""Exercise the standards gate's consumer lint-tool bootstrap command order."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/standards-gates.yml"


def consumer_lint_step() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.strip() == "- name: Install consumer lint tools"
    )
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2 :] if line.strip() else "")
    return "\n".join(body) + "\n"


class ConsumerLintBootstrapTests(unittest.TestCase):
    def run_step(self, listing: dict) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mise = root / "mise"
            mise.write_text(
                '#!/bin/bash\n'
                'if [[ "$1 $2 $3" == "ls --current --json" ]]; then\n'
                '  printf "%s\\n" "$LISTING"\n'
                'elif [[ "$1" == "install" ]]; then\n'
                '  printf "%s\\n" "$*" >> "$INSTALL_LOG"\n'
                'else\n'
                '  exit 2\n'
                'fi\n',
                encoding="utf-8",
            )
            mise.chmod(0o755)
            log = root / "installs"
            env = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "LISTING": json.dumps(listing),
                "INSTALL_LOG": str(log),
            }
            result = subprocess.run(
                ["bash", "-c", consumer_lint_step()],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
            return result, calls

    def test_consumer_pipx_tools_wait_for_configured_uv_and_python(self) -> None:
        result, calls = self.run_step({
            "pipx:ruff": [{"version": "0.16.5"}],
            "uv": [{"version": "0.12.15"}],
            "python": [{"version": "3.14.6"}],
            "node": [{"version": "22.12.0"}],
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["install uv", "install python", "install node pipx:ruff"])

    def test_prerequisites_are_only_installed_if_selected_by_consumer(self) -> None:
        result, calls = self.run_step({"pipx:ruff": [{"version": "0.16.5"}]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["install pipx:ruff"])

    def test_missing_lint_pins_preserve_gate_missing_tool_behavior(self) -> None:
        result, calls = self.run_step({
            "uv": [{"version": "0.12.15"}],
            "python": [{"version": "3.14.6"}],
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertIn("the gate reports them missing", result.stdout)


if __name__ == "__main__":
    unittest.main()
