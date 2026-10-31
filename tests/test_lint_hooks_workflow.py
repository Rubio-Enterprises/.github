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
            'lefthook run pre-commit --no-stage-fixed --files-from-stdin < "$files"',
            body,
        )


class NpmDependenciesTests(unittest.TestCase):
    def test_npm_install_is_frozen_facet_gated_and_skips_image_or_pnpm_paths(self):
        name = "Install locked npm dependencies for repository-owned hooks"
        block = WORKFLOW_TEXT.split(f"- name: {name}", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("needs.detect.outputs.image_mode == ''", block)
        self.assertIn("^has_(javascript|typescript):", block)
        self.assertIn("hashFiles('package-lock.json') != ''", block)
        self.assertIn("hashFiles('pnpm-lock.yaml') == ''", block)
        self.assertIn("npm ci", extract_run_block(name))
        self.assertNotIn("ignore-scripts", block)
        self.assertNotIn("continue-on-error", block)
        self.assertNotIn("cache", block)
        for step in (ALL_FILES_STEP, FORK_STEP):
            self.assertLess(WORKFLOW_TEXT.index(f"- name: {name}"),
                            WORKFLOW_TEXT.index(f"- name: {step}"))

    def test_js_dependency_facet_is_declared_not_inferred_from_a_manifest(self):
        body = extract_run_block("Install locked npm dependencies for repository-owned hooks")
        for answers, expected in [
            ("has_typescript: true\n", "true"),
            ("has_javascript: true\n", "true"),
            ("has_typescript: false\nhas_javascript: false\n", "false"),
            ('has_typescript: "true"\n', "false"),
            ("", "false"),
        ]:
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                (repo / ".copier-answers.yml").write_text(answers)
                (repo / "package.json").write_text('{}')
                output = repo / "output"
                npm = repo / "npm"
                npm.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$NPM_TRACE"\n')
                npm.chmod(0o755)
                env = {**os.environ, "NPM_TRACE": str(output),
                       "PATH": f"{repo}:{os.environ['PATH']}"}
                result = subprocess.run(["bash", "-c", body], cwd=repo, env=env,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.exists(), expected == "true")
                if output.exists():
                    self.assertEqual(output.read_text(), "ci\n")

    def test_npm_install_failure_fails_the_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".copier-answers.yml").write_text("has_typescript: true\n")
            npm = repo / "npm"
            npm.write_text("#!/bin/sh\nexit 7\n")
            npm.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", extract_run_block("Install locked npm dependencies for repository-owned hooks")],
                cwd=repo, env={**os.environ, "PATH": f"{repo}:{os.environ['PATH']}"},
                text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 7)

    def test_existing_pnpm_install_policy_is_unchanged(self):
        block = WORKFLOW_TEXT.split("- name: Install JS deps (so lefthook.local.yml pnpm hooks resolve workspace packages)", 1)[1]
        block = block.split("\n      - name:", 1)[0]
        self.assertIn("needs.detect.outputs.image_mode == ''", block)
        self.assertIn("hashFiles('pnpm-lock.yaml') != ''", block)
        self.assertIn("pnpm install --frozen-lockfile --network-concurrency=8 --child-concurrency=2", block)
        self.assertNotIn("ignore-scripts", block)


class HookToolDownloadTests(unittest.TestCase):
    def test_on_demand_hook_tools_receive_the_repo_token(self) -> None:
        for step in (ALL_FILES_STEP, FORK_STEP):
            with self.subTest(step=step):
                start = WORKFLOW_TEXT.index(f"- name: {step}")
                end = WORKFLOW_TEXT.find("\n      - name:", start)
                block = WORKFLOW_TEXT[start:end if end != -1 else None]
                self.assertIn("MISE_GITHUB_TOKEN: ${{ github.token }}", block)


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
        conditions = [line.strip() for line in lines[start:run_index] if line.strip().startswith("if:")]
        self.assertEqual(conditions, ["if: needs.detect.outputs.image_mode == ''"])

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
        self.assertIn("--all-files --no-stage-fixed", block)
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


class InjectionContractTests(unittest.TestCase):
    """lint-hooks is an injected Required Governance Workflow (standards#627)."""

    def test_triggers_cover_pull_requests_and_merge_queues(self) -> None:
        on_block = WORKFLOW_TEXT.split('"on":', 1)[1].split("permissions:", 1)[0]
        for trigger in ("pull_request:", "merge_group:"):
            self.assertIn(f"  {trigger}", on_block)
        # Not a reusable any more: only the injected path may run it.
        self.assertNotIn("workflow_call", on_block)

class KnowledgebaseRemoteTests(unittest.TestCase):
    def test_public_framework_needs_no_token_or_url_route(self) -> None:
        for retired in (
            "Detect the Knowledgebase Framework remote",
            "Mint knowledgebase-framework read token",
            "Route the Knowledgebase Framework remote through the App token",
            "knowledgebase-remote",
        ):
            self.assertNotIn(retired, WORKFLOW_TEXT)

    def test_full_knowledgebase_check_remains_enabled(self) -> None:
        check = WORKFLOW_TEXT.split("- name: Run the full knowledgebase check", 1)[1].split("- name:", 1)[0]
        self.assertIn("needs.detect.outputs.has_knowledgebase == 'true'", check)
        self.assertIn("lefthook run kb-check", check)


class PrivateModuleRetirementTests(unittest.TestCase):
    def test_lint_hooks_never_mints_or_forwards_private_module_auth(self) -> None:
        for retired in (
            "PRIVATE_MODULES_READER", "GO_PRIVATE_MODULE_REPOS",
            "go-private-token", "Route private Go module",
        ):
            self.assertNotIn(retired, WORKFLOW_TEXT)

    def test_cloud_smoke_accepts_legacy_names_without_reading_them(self) -> None:
        smoke = (ROOT / ".github/workflows/cloud-setup-smoke.yml").read_text()
        for name in ("PRIVATE_MODULES_READER_APP_ID", "PRIVATE_MODULES_READER_APP_PRIVATE_KEY"):
            self.assertIn(f"      {name}:\n", smoke)
            self.assertNotIn(f"secrets.{name}", smoke)
        self.assertNotIn("private-modules-token", smoke)
        self.assertNotIn("HAS_PRIVATE_MODULES_APP", smoke)
        self.assertNotIn("          GH_PAT:", smoke)
        self.assertIn("secrets.STANDARDS_READER_APP_ID", smoke)

    def test_standards_reader_mints_only_for_standards(self) -> None:
        # The standards-bootstrap action mints inside itself, so read its scope
        # from the action: the native path's token is the action's.
        action = (ROOT / "actions" / "standards-bootstrap" / "action.yml").read_text()
        steps = WORKFLOW_TEXT.split("- name: ")[1:]
        scopes = sorted(
            (action if "/actions/standards-bootstrap@" in step else step)
            .split("repositories:", 1)[1].split("\n", 1)[0].strip()
            for step in steps
            if "secrets.STANDARDS_READER_APP_ID" in step
        )
        self.assertEqual(scopes, ["standards", "standards"])
