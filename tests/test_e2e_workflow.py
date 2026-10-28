"""The reusable E2E workflow: capability routing and the suite-command detector."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import extract_run_block, extract_step

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/e2e.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
PICK_STEP = "Pick the runner from the Swift facet"
MACOS_STEP = "Prepare the macOS toolchain"
RUNNERS = {"MACOS_RUNNER": '[\n  "macos-tart"\n]', "ARM_RUNNER": '["linux-arm64"]'}


def job(name: str) -> str:
    """Return one top-level job block."""
    jobs = TEXT.split("\njobs:\n", 1)[1]
    blocks = re.split(r"(?m)^  (?=[a-z][a-z0-9-]*:\n)", jobs)
    return next(block for block in blocks if block.startswith(f"{name}:\n"))


def pick(answers: str | None) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        if answers is not None:
            (repo / ".copier-answers.yml").write_text(answers, encoding="utf-8")
        output = repo / "github-output"
        result = subprocess.run(
            ["bash", "-c", extract_run_block(TEXT, PICK_STEP)],
            cwd=repo,
            env={**os.environ, **RUNNERS, "GITHUB_OUTPUT": str(output)},
            capture_output=True,
            text=True,
            check=False,
        )
        values = {}
        if output.exists():
            values = dict(
                line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()
            )
        return result, values


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
    def test_detect_job_is_light_and_the_suite_follows_it(self) -> None:
        self.assertIn(
            "runs-on: ${{ fromJSON(vars.RUNNERS || '{}').small || "
            "fromJSON('[\"ubuntu-slim\"]') }}",
            job("detect"),
        )
        suite = job("e2e")
        self.assertIn("needs: detect\n", suite)
        self.assertIn("runs-on: ${{ fromJSON(needs.detect.outputs.runner) }}", suite)

    def test_routes_keep_their_hosted_fallbacks(self) -> None:
        step = extract_step(TEXT, PICK_STEP)
        self.assertIn(
            "MACOS_RUNNER: ${{ toJSON(fromJSON(vars.RUNNERS || '{}').macos || "
            "fromJSON('[\"macos-26\"]')) }}",
            step,
        )
        self.assertIn(
            "ARM_RUNNER: ${{ toJSON(fromJSON(vars.RUNNERS || '{}').arm || "
            "fromJSON('[\"ubuntu-24.04-arm\"]')) }}",
            step,
        )

    def test_required_context_job_keeps_its_id_and_name(self) -> None:
        # .github-private requires "e2e / e2e": caller job id / this job id.
        suite = job("e2e")
        self.assertNotRegex(suite, r"(?m)^    name:")
        self.assertEqual(re.findall(r"(?m)^  ([a-z][a-z0-9-]*):$", TEXT.split("\njobs:\n", 1)[1]),
                         ["detect", "e2e"])

    def test_swift_suites_route_to_macos_and_others_to_arm(self) -> None:
        for answers, runner in (
            ("has_swift: true\n", ["macos-tart"]),
            ("has_swift: true\nhas_typescript: true\n", ["macos-tart"]),
            ("has_swift: false\nhas_typescript: true\n", ["linux-arm64"]),
            ("has_swift: 'true'\n", ["linux-arm64"]),
            ("# has_swift: true\n", ["linux-arm64"]),
            ("", ["linux-arm64"]),
            (None, ["linux-arm64"]),
        ):
            with self.subTest(answers=answers):
                result, out = pick(answers)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(out["runner"]), runner)
                self.assertNotIn("\n", out["runner"])

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
        self.assertEqual(secrets, {"STANDARDS_READER_APP_ID", "STANDARDS_READER_APP_PRIVATE_KEY"})


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
