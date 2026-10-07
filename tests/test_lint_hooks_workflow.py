"""Behavioral contract tests for the lint-hooks reusable workflow.

Fork file lists must be NUL-separated or glob-scoped hooks silently check
nothing. Both fork and non-fork hooks must fail on command errors and tracked
formatter rewrites; a required status cannot report warnings as success.
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
REWRITE_STEP = "Fail if a hook rewrote a tracked file"


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
    *, hooks_exit: int, repo: Path, rewrite: bool = False
) -> subprocess.CompletedProcess[str]:
    """Execute the real fork step with a stubbed hook command on PATH."""
    bin_dir = repo / ".stubbin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "mise"
    mutation = 'printf "formatted\\n" > a.md\n' if rewrite else ""
    stub.write_text(
        f"#!/bin/sh\ncat >/dev/null\n{mutation}exit {hooks_exit}\n", encoding="utf-8"
    )
    stub.chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": os.environ["HOME"],
        "MISE_ENV": "ci",
    }
    return subprocess.run(
        ["bash", "-c", extract_run_block(FORK_STEP)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def run_rewrite_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", extract_run_block(REWRITE_STEP)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
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
    """Required hook status must not hide a fork failure."""

    def test_fork_step_has_no_warning_escape_hatch(self) -> None:
        self.assertNotIn("ENFORCE_FORK_HOOKS:", WORKFLOW_TEXT)
        self.assertNotIn("::warning", extract_run_block(FORK_STEP))

    def test_rewrite_guard_follows_both_execution_paths(self) -> None:
        for step in (ALL_FILES_STEP, FORK_STEP):
            self.assertLess(WORKFLOW_TEXT.index(f"- name: {step}"),
                            WORKFLOW_TEXT.index(f"- name: {REWRITE_STEP}"))

    def test_rewrite_guard_runs_for_forks(self) -> None:
        lines = WORKFLOW_TEXT.splitlines()
        start = next(
            i for i, line in enumerate(lines) if line.strip() == f"- name: {REWRITE_STEP}"
        )
        run_index = next(
            i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |"
        )
        self.assertNotIn("if:", "\n".join(lines[start:run_index]))

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
    """Exercise the fork step and formatter guard on disposable git trees."""

    def test_passing_hooks_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_fork_step(hooks_exit=0, repo=make_repo(tmp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fork-mode lefthook hooks passed", result.stdout)

    def test_failing_hooks_fail_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_fork_step(hooks_exit=1, repo=make_repo(tmp))
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::fork-mode lefthook hooks failed", result.stderr)

    def test_clean_repo_passes_rewrite_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            self.assertEqual(run_fork_step(hooks_exit=0, repo=repo).returncode, 0)
            guard = run_rewrite_guard(repo)
        self.assertEqual(guard.returncode, 0, guard.stderr)

    def test_formatter_rewrite_fails_the_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp)
            self.assertEqual(
                run_fork_step(hooks_exit=0, repo=repo, rewrite=True).returncode, 0
            )
            guard = run_rewrite_guard(repo)
        self.assertEqual(guard.returncode, 1)
        self.assertIn("::error title=lint-hooks::", guard.stdout)


if __name__ == "__main__":
    unittest.main()
