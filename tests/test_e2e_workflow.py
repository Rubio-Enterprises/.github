"""The reusable E2E detector accepts real repo-owned tasks without an apex npm project."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/e2e.yml"


def detect_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "- id: detect")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2 :] if line.strip() else "")
    return "\n".join(body) + "\n"


def run_detector(*, tasks: list[str], package: dict | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if package is not None:
            (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        mise = bin_dir / "mise"
        payload = json.dumps([{"name": name} for name in tasks])
        mise.write_text(f"#!/bin/sh\nprintf '%s\\n' '{payload}'\n", encoding="utf-8")
        mise.chmod(0o755)
        output = root / "github-output"
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GITHUB_OUTPUT": str(output)}
        result = subprocess.run(
            ["bash", "-c", detect_script()],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, output.read_text(encoding="utf-8") if output.exists() else ""


class E2EDetectorTests(unittest.TestCase):
    def test_nested_repo_with_real_mise_task_needs_no_apex_package(self) -> None:
        code, output = run_detector(tasks=["e2e"])
        self.assertEqual(code, 0)
        self.assertIn("cmd=mise run e2e\n", output)
        self.assertIn("setup=mise\n", output)

    def test_apex_script_remains_supported(self) -> None:
        code, output = run_detector(tasks=[], package={"scripts": {"e2e": "playwright test"}})
        self.assertEqual(code, 0)
        self.assertIn("cmd=npm run e2e\n", output)

    def test_no_real_e2e_command_fails_closed(self) -> None:
        code, output = run_detector(tasks=[])
        self.assertEqual(code, 1)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
