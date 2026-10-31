"""The reusable E2E workflow: capability routing and the suite-command detector."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import extract_step

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/e2e.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
MACOS_STEP = "Prepare the macOS toolchain"
SETUP_STEP = "Install the Standards Release, tools and dependencies"


def job(name: str) -> str:
    """Return one top-level job block."""
    jobs = TEXT.split("\njobs:\n", 1)[1]
    blocks = re.split(r"(?m)^  (?=[a-z][a-z0-9-]*:\n)", jobs)
    return next(block for block in blocks if block.startswith(f"{name}:\n"))


def command_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "id: command")
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
            ["bash", "-c", command_script()],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, output.read_text(encoding="utf-8") if output.exists() else ""


class E2ERouteTests(unittest.TestCase):
    def test_one_job_routes_from_the_ci_platform_property(self) -> None:
        self.assertEqual(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):$", TEXT.split("\njobs:\n", 1)[1]),
                         ["e2e"])
        suite = " ".join(job("e2e").split())
        self.assertIn(
            "github.event.repository.custom_properties['ci-platform'] == 'macos' "
            "&& (fromJSON(vars.RUNNERS || '{}').macos || fromJSON('[\"macos-26\"]')) "
            "|| (fromJSON(vars.RUNNERS || '{}').arm || fromJSON('[\"ubuntu-24.04-arm\"]'))",
            suite,
        )

    def test_suite_job_keeps_its_id_without_a_name(self) -> None:
        self.assertNotRegex(job("e2e"), r"(?m)^    name:")

    def test_copier_answers_are_not_read(self) -> None:
        self.assertNotIn(".copier-answers.yml", TEXT)
        self.assertNotIn("standards-bootstrap", TEXT)

    def test_setup_installs_the_package_before_the_suite_is_found(self) -> None:
        step = extract_step(TEXT, SETUP_STEP)
        self.assertIn("run: mise run setup", step)
        self.assertIn("NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", step)
        self.assertIn("packages: read", job("e2e"))
        self.assertLess(TEXT.index(f"- name: {SETUP_STEP}\n"), TEXT.index("id: command\n"))

    def test_macos_toolchain_is_prepared_only_on_macos(self) -> None:
        step = extract_step(TEXT, MACOS_STEP)
        self.assertIn("if: runner.os == 'macOS'", step)
        self.assertIn("xcodebuild -version", step)
        self.assertLess(TEXT.index(f"- name: {MACOS_STEP}\n"), TEXT.index("id: command\n"))

    def test_no_signing_or_publishing_credentials(self) -> None:
        secrets = {
            line.split("secrets.", 1)[1].split()[0].rstrip("}")
            for line in TEXT.splitlines()
            if "secrets." in line and not line.lstrip().startswith("#")
        }
        self.assertEqual(secrets, {"GITHUB_TOKEN"})


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
