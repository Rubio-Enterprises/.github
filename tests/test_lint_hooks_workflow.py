"""Behavioral contract tests for the lint-hooks reusable workflow.

Two invariants are pinned here, both of which failed silently in production
rather than loudly:

1. Every producer of the fork-mode file list must use ``-z``. lefthook's
   ``--files-from-stdin`` parses NUL, not newlines; fed a newline-separated list
   it resolves ZERO paths, so every ``glob:``-scoped hook skips and the job exits
   0 having checked nothing. A newline-separated producer is not a style slip —
   it silently disables the gate.

2. The fork path is warn-only by default and the all-files path is not. The
   ``-z`` fix revives a gate that has been passing vacuously across the whole
   fork cohort, so enforcing on contact would turn ~16 repos red at once for
   pre-existing debt. The non-fork path was never broken and must stay
   enforcing, so nothing green today can turn red.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "lint-hooks.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")

FORK_STEP = "Run lefthook pre-commit on changed fork files"
ALL_FILES_STEP = "Run lefthook pre-commit across all files"


def extract_run_block(step_name: str) -> str:
    """Return the ``run: |`` body of *step_name*, dedented to column 0."""
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


def strip_comments(script: str) -> str:
    """Drop whole-line comments so prose about `-z` cannot satisfy a `-z` assertion."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def run_fork_step(
    *, hooks_exit: int, enforce: str, repo: Path
) -> subprocess.CompletedProcess[str]:
    """Execute the fork step's real script with a stubbed `mise` on PATH.

    The stub stands in for `mise exec -- lefthook ...` and exits *hooks_exit*,
    which is the only thing the warn/enforce decision keys on.
    """
    bin_dir = repo / ".stubbin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "mise"
    stub.write_text(f'#!/bin/sh\ncat >/dev/null\nexit {hooks_exit}\n', encoding="utf-8")
    stub.chmod(0o755)

    script = extract_run_block(FORK_STEP)
    with tempfile.NamedTemporaryFile() as github_output:
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": os.environ["HOME"],
            "GITHUB_OUTPUT": github_output.name,
            "MISE_ENV": "ci",
            "ENFORCE_FORK_HOOKS": enforce,
        }
        return subprocess.run(
            ["bash", "-c", script],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )


def make_repo(tmp: str) -> Path:
    """A single-commit git repo.

    One commit with no parents drives the script to its last-resort
    ``git ls-files -z`` branch, which exercises the real file-list plumbing
    (including NUL separation) without needing a synthetic merge commit.
    """
    repo = Path(tmp) / "consumer"
    repo.mkdir()
    run = lambda *args: subprocess.run(  # noqa: E731 - terse fixture helper
        args, cwd=repo, check=True, capture_output=True
    )
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.invalid")
    run("git", "config", "user.name", "t")
    (repo / "a.md").write_text("# a\n", encoding="utf-8")
    run("git", "add", "a.md")
    run("git", "commit", "-qm", "seed")
    return repo


class ForkFileListTests(unittest.TestCase):
    """Invariant 1 — the file list must be NUL-separated."""

    def test_every_file_list_producer_is_nul_separated(self) -> None:
        body = strip_comments(extract_run_block(FORK_STEP))
        producers = [
            line.strip()
            for line in body.splitlines()
            if ('git diff' in line or 'git ls-files' in line) and '"$files"' in line
        ]
        self.assertTrue(producers, "no file-list producers found — did the step move?")
        for producer in producers:
            with self.subTest(producer=producer):
                self.assertRegex(
                    producer,
                    r"git (diff|ls-files) -z\b",
                    "lefthook --files-from-stdin parses NUL, not newlines; a "
                    "producer without -z resolves zero paths and silently "
                    "disables every glob-scoped hook",
                )

    def test_file_list_is_rendered_for_the_log_via_nul_translation(self) -> None:
        body = strip_comments(extract_run_block(FORK_STEP))
        self.assertIn("tr '\\0' '\\n' < \"$files\"", body)

    def test_list_is_piped_to_lefthook_files_from_stdin(self) -> None:
        body = strip_comments(extract_run_block(FORK_STEP))
        self.assertIn(
            'lefthook run pre-commit --files-from-stdin < "$files"',
            body,
        )


class EnforcementPolicyTests(unittest.TestCase):
    """Invariant 2 — fork warn-only by default, all-files always enforcing."""

    def test_fork_step_defaults_to_warn_only(self) -> None:
        # Assert on the isolated declaration line, not the whole file: an
        # assertIn against WORKFLOW_TEXT dumps ~400 lines into the failure
        # message and buries the one thing that is wrong.
        declarations = [
            line.strip()
            for line in WORKFLOW_TEXT.splitlines()
            if line.strip().startswith("ENFORCE_FORK_HOOKS:")
        ]
        self.assertEqual(
            declarations,
            ["ENFORCE_FORK_HOOKS: ${{ vars.LINT_HOOKS_FORK_ENFORCE || 'false' }}"],
            "the default must be 'false' — the -z fix revives a vacuously "
            "passing gate across the whole fork cohort at once",
        )

    def test_all_files_step_has_no_enforcement_escape_hatch(self) -> None:
        # The non-fork path was never broken. It must not gain a warn-only mode,
        # or repos that are genuinely green today could start hiding regressions.
        lines = WORKFLOW_TEXT.splitlines()
        start = next(
            i for i, line in enumerate(lines) if line.strip() == f"- name: {ALL_FILES_STEP}"
        )
        end = next(
            i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("- name:")
        )
        block = "\n".join(lines[start:end])
        self.assertIn("--all-files", block)
        self.assertNotIn("ENFORCE_FORK_HOOKS", block)
        self.assertNotIn("::warning", block)


class ForkStepBehaviorTests(unittest.TestCase):
    """Execute the real extracted script; assert the decision it actually makes."""

    def test_passing_hooks_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_fork_step(hooks_exit=0, enforce="false", repo=make_repo(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fork-mode lefthook hooks passed", result.stdout)

    def test_failing_hooks_warn_but_do_not_fail_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_fork_step(hooks_exit=1, enforce="false", repo=make_repo(tmp))
        self.assertEqual(
            result.returncode,
            0,
            f"warn-only mode must not fail the job\nstderr: {result.stderr}",
        )
        self.assertIn("::warning title=", result.stdout)
        self.assertIn("LINT_HOOKS_FORK_ENFORCE=true", result.stdout)

    def test_failing_hooks_fail_the_job_when_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_fork_step(hooks_exit=1, enforce="true", repo=make_repo(tmp))
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::", result.stderr)

    def test_warning_message_is_a_single_annotation_line(self) -> None:
        # A `\`-continued string inside double quotes folds the YAML block's
        # indentation into the message; a multi-line annotation body is also not
        # rendered by GitHub. Both would make the warning unreadable.
        with tempfile.TemporaryDirectory() as tmp:
            result = run_fork_step(hooks_exit=1, enforce="false", repo=make_repo(tmp))
        annotations = [
            line for line in result.stdout.splitlines() if line.startswith("::warning")
        ]
        self.assertEqual(len(annotations), 1, result.stdout)
        self.assertNotIn("  ", annotations[0].split("::", 2)[-1])


if __name__ == "__main__":
    unittest.main()
