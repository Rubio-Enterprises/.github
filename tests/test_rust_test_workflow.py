"""Behavioral contract tests for the Rust Required Governance Workflow."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "rust-test.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")


def extract_run_block(step_name: str) -> str:
    lines = WORKFLOW_TEXT.splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    run_index = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    body: list[str] = []
    for line in lines[run_index + 1 :]:
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent <= run_indent:
            break
        body.append(line[run_indent + 2 :] if line.strip() else "")
    return "\n".join(body) + "\n"


def run_script(script: str, **environment: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile() as github_output:
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "GITHUB_OUTPUT": github_output.name,
            **environment,
        }
        return subprocess.run(
            ["bash", "-c", script],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )


class RustWorkflowContractTests(unittest.TestCase):
    def test_candidate_and_production_runs_have_distinct_concurrency_groups(self) -> None:
        self.assertIn(
            "group: ${{ github.workflow_ref }}-${{ github.ref }}",
            WORKFLOW_TEXT,
        )
        self.assertNotIn(
            "group: ${{ github.workflow }}-${{ github.ref }}",
            WORKFLOW_TEXT,
        )

    def test_reusable_interface_requires_explicit_policy(self) -> None:
        workflow_call = re.search(
            r"workflow_call:\n(?P<body>.*?)\n  pull_request:",
            WORKFLOW_TEXT,
            re.DOTALL,
        )
        if workflow_call is None:
            self.fail("workflow_call input block is missing")
        body = workflow_call.group("body")
        self.assertRegex(
            body,
            r"workload-class:\s*\{\s*type:\s*string,\s*required:\s*true\s*\}",
        )
        self.assertRegex(
            body,
            r"timeout-minutes:\s*\{\s*type:\s*number,\s*required:\s*true\s*\}",
        )

    def test_policy_precheck_accepts_supported_classes_and_timeout_bounds(self) -> None:
        script = extract_run_block("Validate Rust test execution policy")
        cases = (
            ("pull_request", "false", "GLUE", "5", "gpu", "4"),
            ("workflow_dispatch", "false", "LiNuX-ArM", "120", "gpu", "4"),
            ("pull_request", "true", "gpu", "4", "glue", "20"),
            ("merge_group", "true", "gpu", "4", "LINUX-ARM", "60"),
        )
        for event, is_direct, input_class, input_timeout, repo_class, repo_timeout in cases:
            with self.subTest(event=event, workload_class=input_class or repo_class):
                completed = run_script(
                    script,
                    EVENT_NAME=event,
                    IS_DIRECT=is_direct,
                    INPUT_WORKLOAD_CLASS=input_class,
                    INPUT_TIMEOUT_MINUTES=input_timeout,
                    REPOSITORY_WORKLOAD_CLASS=repo_class,
                    REPOSITORY_TIMEOUT_MINUTES=repo_timeout,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_policy_precheck_rejects_invalid_policy_before_checkout(self) -> None:
        script = extract_run_block("Validate Rust test execution policy")
        invalid = (
            ("", "20"),
            ("gpu", "20"),
            ("glue", ""),
            ("glue", "4"),
            ("glue", "121"),
            ("glue", "020"),
            ("glue", "20.5"),
            ("glue", "20 21"),
        )
        for workload_class, timeout in invalid:
            with self.subTest(workload_class=workload_class, timeout=timeout):
                completed = run_script(
                    script,
                    EVENT_NAME="pull_request",
                    IS_DIRECT="true",
                    INPUT_WORKLOAD_CLASS="",
                    INPUT_TIMEOUT_MINUTES="",
                    REPOSITORY_WORKLOAD_CLASS=workload_class,
                    REPOSITORY_TIMEOUT_MINUTES=timeout,
                )
                self.assertNotEqual(completed.returncode, 0)

                reusable = run_script(
                    script,
                    EVENT_NAME="pull_request",
                    IS_DIRECT="false",
                    INPUT_WORKLOAD_CLASS=workload_class,
                    INPUT_TIMEOUT_MINUTES=timeout,
                    REPOSITORY_WORKLOAD_CLASS="glue",
                    REPOSITORY_TIMEOUT_MINUTES="20",
                )
                self.assertNotEqual(reusable.returncode, 0)

        self.assertLess(
            WORKFLOW_TEXT.index("- name: Validate Rust test execution policy"),
            WORKFLOW_TEXT.index("uses: actions/checkout@"),
        )

    def test_workload_uses_inline_symbolic_routes_and_canonical_task(self) -> None:
        self.assertIn(
            "(inputs.workload-class == 'glue' && (vars.RUNNER_GLUE || '[\"ubuntu-slim\"]'))",
            WORKFLOW_TEXT,
        )
        self.assertIn(
            "(inputs.workload-class == 'linux-arm' && (vars.RUNNER_LINUX_ARM || '[\"ubuntu-24.04-arm\"]'))",
            WORKFLOW_TEXT,
        )
        self.assertIn(
            "(vars.RUST_TEST_WORKLOAD_CLASS == 'glue' && (vars.RUNNER_GLUE || '[\"ubuntu-slim\"]'))",
            WORKFLOW_TEXT,
        )
        self.assertIn(
            "(vars.RUST_TEST_WORKLOAD_CLASS == 'linux-arm' && (vars.RUNNER_LINUX_ARM || '[\"ubuntu-24.04-arm\"]'))",
            WORKFLOW_TEXT,
        )
        self.assertIn("contains(fromJSON(", WORKFLOW_TEXT)
        self.assertNotIn("contains(' 5 6", WORKFLOW_TEXT)
        timeout_arrays = re.findall(
            r"contains\(fromJSON\('\[([^]]+)\]'\), format\('\{0\}', "
            r"(?:inputs\.timeout-minutes|vars\.RUST_TEST_TIMEOUT_MINUTES)\)\)",
            WORKFLOW_TEXT,
        )
        timeout_counts = Counter(
            int(value)
            for array in timeout_arrays
            for value in re.findall(r'"(\d+)"', array)
        )
        self.assertEqual(set(timeout_counts), set(range(5, 121)))
        self.assertEqual(set(timeout_counts.values()), {4})
        self.assertIn('["ubuntu-slim"]', WORKFLOW_TEXT)
        self.assertIn('["ubuntu-24.04-arm"]', WORKFLOW_TEXT)
        self.assertNotIn("self-hosted", WORKFLOW_TEXT)
        self.assertNotRegex(WORKFLOW_TEXT, r"\n  (?:route|routing):")
        self.assertNotIn("Detect Rust project", WORKFLOW_TEXT)
        self.assertNotIn("Cargo.toml", WORKFLOW_TEXT)
        self.assertEqual(WORKFLOW_TEXT.count("run: mise run test"), 1)

    def test_source_repository_direct_events_are_an_explicit_noop(self) -> None:
        self.assertIn(
            "startsWith(github.workflow_ref, 'Rubio-Enterprises/.github/.github/workflows/rust-test.yml@')",
            WORKFLOW_TEXT,
        )
        self.assertNotIn("github.event_name == 'workflow_call'", WORKFLOW_TEXT)
        self.assertNotIn("invocation-kind", WORKFLOW_TEXT)
        self.assertIn("github.repository != 'Rubio-Enterprises/.github'", WORKFLOW_TEXT)
        self.assertIn("SOURCE_REPOSITORY_NOOP", WORKFLOW_TEXT)

    def test_aggregate_accepts_only_exact_workload_success(self) -> None:
        script = extract_run_block("Aggregate gate result")
        success = run_script(
            script,
            WORKLOAD_RESULT="success",
            SOURCE_REPOSITORY_NOOP="false",
        )
        self.assertEqual(success.returncode, 0, success.stderr)

        for result in ("failure", "cancelled", "skipped", "neutral", "unexpected"):
            with self.subTest(result=result):
                completed = run_script(
                    script,
                    WORKLOAD_RESULT=result,
                    SOURCE_REPOSITORY_NOOP="false",
                )
                self.assertNotEqual(completed.returncode, 0)

        source_noop = run_script(
            script,
            WORKLOAD_RESULT="skipped",
            SOURCE_REPOSITORY_NOOP="true",
        )
        self.assertEqual(source_noop.returncode, 0, source_noop.stderr)

    @unittest.skipUnless(shutil.which("actionlint"), "actionlint is not installed")
    def test_actionlint_accepts_complete_reusable_caller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            shutil.copy2(WORKFLOW, workflows / "rust-test.yml")
            caller = workflows / "caller.yml"
            caller.write_text(
                textwrap.dedent(
                    """\
                    name: caller
                    on: workflow_dispatch
                    jobs:
                      rust:
                        uses: ./.github/workflows/rust-test.yml
                        with:
                          workload-class: glue
                          timeout-minutes: 20
                    """
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                ["actionlint", str(caller), str(workflows / "rust-test.yml")],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
