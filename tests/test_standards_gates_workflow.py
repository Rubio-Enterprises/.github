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
        if line.strip() == "- name: Install consumer config tools"
    )
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2 :] if line.strip() else "")
    return "\n".join(body) + "\n"

def mise_resolver_step() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "- name: Resolve installed mise version")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2:] if line.strip() else "")
    return "\n".join(body) + "\n"


class MiseVersionResolutionTests(unittest.TestCase):
    def run_resolver(self, binary: str | None) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            (data / "bin").mkdir(parents=True)
            if binary is not None:
                executable = data / "bin/mise"
                executable.write_text(binary, encoding="utf-8")
                executable.chmod(0o755)
            output = root / "output"
            result = subprocess.run(
                ["bash", "-c", mise_resolver_step()],
                capture_output=True, text=True, check=False,
                env={**os.environ, "MISE_DATA_DIR": str(data),
                     "MISE_VERSION": "2026.9.11", "GITHUB_OUTPUT": str(output)},
            )
            return result, output.read_text() if output.exists() else ""

    def test_warm_binary_wins_over_fallback_without_self_update(self) -> None:
        result, output = self.run_resolver(
            '#!/bin/sh\nprintf \'{"version":"2026.7.7 linux-x64 (2026-07-07)"}\\n\'\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, "version=2026.7.7\n")

    def test_cold_runner_uses_approved_pin(self) -> None:
        result, output = self.run_resolver(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, "version=2026.9.11\n")

    def test_present_but_unparseable_binary_fails_closed(self) -> None:
        result, output = self.run_resolver(
            '#!/bin/sh\nprintf \'{"version":"not mise"}\\n\'\n'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")


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
            "pipx:copier": [{"version": "9.18.1"}],
            "uv": [{"version": "0.12.15"}],
            "python": [{"version": "3.14.6"}],
            "node": [{"version": "22.12.0"}],
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["install uv", "install python", "install pipx:copier"])

    def test_linters_are_never_installed_by_config_gate(self) -> None:
        result, calls = self.run_step({
            "pipx:ruff": [], "npm:@biomejs/biome": [],
            "npm:markdownlint-cli2": [], "pipx:yamllint": [], "node": [],
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_producer_and_verdict_consumer_use_the_new_name(self) -> None:
        text = WORKFLOW.read_text()
        self.assertIn("id: gate_lint_config", text)
        self.assertIn("steps.gate_lint_config.outputs.verdict", text)
        self.assertIn('report_gate lint-config', text)
        self.assertNotIn('steps.gate_lint_format', text)
        # The rename bridge is retired (standards#623): no legacy selector or
        # forwarder remains for the published workflow to depend on.
        self.assertNotIn('gate-lint-format', text)
        self.assertIn('ci/gate-lint-config.sh', text)

    def test_prerequisites_are_only_installed_if_selected_by_consumer(self) -> None:
        result, calls = self.run_step({"pipx:copier": [{"version": "9.18.1"}]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["install pipx:copier"])

    def test_missing_config_pins_preserve_gate_missing_tool_behavior(self) -> None:
        result, calls = self.run_step({
            "uv": [{"version": "0.12.15"}],
            "python": [{"version": "3.14.6"}],
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertIn("the gate reports them missing", result.stdout)


if __name__ == "__main__":
    unittest.main()
