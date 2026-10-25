"""Contracts for the standards ADR-0033 native config model in the Gate Families.

A repository answering `config_model: native` provisions its Shared Config
Checkout with `mise bootstrap --only repos,tools`; every other repository keeps
the rendered model's setup. The checkout must exist or the job fails.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import ROOT, extract_run_block, extract_step

WORKFLOWS = ROOT / ".github" / "workflows"
LINT = (WORKFLOWS / "lint-hooks.yml").read_text(encoding="utf-8")
TYPECHECK = (WORKFLOWS / "typecheck-ts.yml").read_text(encoding="utf-8")
BUILD = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
GATES = (WORKFLOWS / "standards-gates.yml").read_text(encoding="utf-8")
TEMPLATE = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")

READY_STEP = "Require the Shared Config Checkout (native config model)"
UNROUTE_STEP = "Remove the standards read route"
MISE_STEP_NAMES = {
    "lint-hooks": "Install consumer-repo runtimes (lefthook + pinned hook tools)",
    "typecheck-ts": "Install consumer-repo runtimes",
    "build": "Install consumer-repo runtimes",
}
NATIVE_WORKFLOWS = {
    "lint-hooks": (LINT, "needs.detect.outputs.config_model"),
    "typecheck-ts": (TYPECHECK, "needs.route.outputs.config_model"),
    "build": (BUILD, "needs.detect.outputs.config_model"),
}
PICK_STEPS = {
    "lint-hooks": (LINT, "Pick runner from the language facets"),
    "typecheck-ts": (TYPECHECK, "Pick the runner from the declared Workload Class"),
}


def run(script: str, cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script], cwd=cwd, text=True, capture_output=True, check=False,
        env={**os.environ, **env},
    )


def outputs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


class ConfigModelSelectionTests(unittest.TestCase):
    def test_only_an_explicit_native_answer_selects_the_native_model(self) -> None:
        for name, (text, step) in PICK_STEPS.items():
            script = extract_run_block(text, step)
            for answers, expected in (
                (None, "rendered"),
                ("has_python: true\n", "rendered"),
                ("config_model: rendered\n", "rendered"),
                ("config_model: native\n", "native"),
                ('config_model: "native"\n', "native"),
                ("# config_model: native\n", "rendered"),
                ("config_model: Native\n", None),
            ):
                with self.subTest(workflow=name, answers=answers), \
                        tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    if answers is not None:
                        (repo / ".copier-answers.yml").write_text(answers)
                    result = run(
                        script, repo, GITHUB_OUTPUT=str(repo / "out"),
                        LIGHT_RUNNER='["small"]', HEAVY_RUNNER='["medium"]',
                        MACOS_RUNNER='["macos"]', RUBY_RUNNER='["ruby"]',
                    )
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("unsupported config_model", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(outputs(repo / "out")["config_model"], expected)

    def test_lint_hooks_reads_the_knowledgebase_facet(self) -> None:
        script = extract_run_block(LINT, PICK_STEPS["lint-hooks"][1])
        for answers, expected in (
            ("has_knowledgebase: true\n", "true"),
            ("has_knowledgebase: false\n", "false"),
            ("has_knowledgebase: 'true'\n", "false"),
            ("", "false"),
        ):
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                (repo / ".copier-answers.yml").write_text(answers)
                result = run(
                    script, repo, GITHUB_OUTPUT=str(repo / "out"),
                    LIGHT_RUNNER='["small"]', HEAVY_RUNNER='["medium"]',
                    MACOS_RUNNER='["macos"]', RUBY_RUNNER='["ruby"]',
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(outputs(repo / "out")["has_knowledgebase"], expected)


class NativeBootstrapContractTests(unittest.TestCase):
    def test_mise_action_bootstraps_only_native_repositories(self) -> None:
        for name, (text, model) in NATIVE_WORKFLOWS.items():
            with self.subTest(workflow=name):
                step = extract_step(text, MISE_STEP_NAMES[name])
                self.assertIn(f"bootstrap: ${{{{ {model} == 'native' }}}}", step)
                self.assertIn(
                    f"bootstrap_args: ${{{{ {model} == 'native' && '--only repos,tools' || '' }}}}",
                    step,
                )
                self.assertIn('GIT_TERMINAL_PROMPT: "0"', step)
                self.assertNotIn("install_args", step)

    def test_standards_route_precedes_the_bootstrap(self) -> None:
        for name, (text, _) in NATIVE_WORKFLOWS.items():
            with self.subTest(workflow=name):
                route = text.index("url.\"https://x-access-token:${TOKEN}@github.com/${OWNER}/standards\"")
                self.assertLess(route, text.index(f"- name: {MISE_STEP_NAMES[name]}"))
                self.assertLess(
                    text.index(f"- name: {MISE_STEP_NAMES[name]}"),
                    text.index(f"- name: {READY_STEP}"),
                )

    def test_rendered_repositories_mint_no_extra_token_for_typecheck_or_build(self) -> None:
        for name in ("typecheck-ts", "build"):
            text, model = NATIVE_WORKFLOWS[name]
            for step in (
                "Mint standards read token (native config model)",
                "Route the Shared Config Checkout clone through the App token",
                READY_STEP,
            ):
                with self.subTest(workflow=name, step=step):
                    self.assertIn(f"if: {model} == 'native'", extract_step(text, step))
            self.assertIn(f"if: always() && {model} == 'native'", extract_step(text, UNROUTE_STEP))
            mint = extract_step(text, "Mint standards read token (native config model)")
            self.assertIn("repositories: standards\n", mint)
            self.assertIn("permission-contents: read", mint)

    def test_repository_code_runs_only_after_the_route_is_removed(self) -> None:
        for text, later in ((TYPECHECK, "Install JS deps"), (BUILD, "Require the build task")):
            self.assertLess(text.index(f"- name: {UNROUTE_STEP}"), text.index(f"- name: {later}"))


class SharedConfigReadinessTests(unittest.TestCase):
    def ready(self, *, status: int, checkout: bool):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            (bin_dir / "mise").write_text(
                "#!/bin/sh\n"
                '[ "$*" = "bootstrap repos status --missing" ] || exit 2\n'
                f"exit {status}\n"
            )
            (bin_dir / "mise").chmod(0o755)
            if checkout:
                shared = repo / ".standards-config"
                (shared / "config" / "lefthook").mkdir(parents=True)
                (shared / "config" / "lefthook" / "base.yml").write_text("{}\n")
                for args in (["init", "-q", "-b", "stable"],
                             ["-c", "user.email=t@e", "-c", "user.name=t",
                              "commit", "-q", "--allow-empty", "-m", "x"]):
                    subprocess.run(["git", *args], cwd=shared, check=True,
                                   capture_output=True)
            summary = repo / "summary"
            results = {
                name: run(extract_run_block(text, READY_STEP), repo,
                          PATH=f"{bin_dir}:{os.environ['PATH']}",
                          GITHUB_STEP_SUMMARY=str(summary))
                for name, (text, _) in NATIVE_WORKFLOWS.items()
            }
            return results, summary.read_text() if summary.exists() else ""

    def test_present_checkout_passes_and_records_its_revision(self) -> None:
        results, summary = self.ready(status=0, checkout=True)
        for name, result in results.items():
            with self.subTest(workflow=name):
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("standards `stable` at `", summary)

    def test_missing_or_stale_checkout_fails(self) -> None:
        for status, checkout, message in (
            (0, False, "shared config missing"),
            (1, True, ""),
        ):
            results, _ = self.ready(status=status, checkout=checkout)
            for name, result in results.items():
                with self.subTest(workflow=name, status=status, checkout=checkout):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)


class RouteRemovalTests(unittest.TestCase):
    def test_token_routes_are_removed_and_others_kept(self) -> None:
        for name in ("typecheck-ts", "build"):
            text, _ = NATIVE_WORKFLOWS[name]
            for routed, other in ((True, True), (True, False), (False, True), (False, False)):
                with self.subTest(workflow=name, routed=routed, other=other), \
                        tempfile.TemporaryDirectory() as tmp:
                    home = Path(tmp)
                    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "xdg"),
                           "GIT_CONFIG_NOSYSTEM": "1"}
                    config = ["git", "config", "--global"]
                    (home / ".gitconfig").write_text("[user]\n\tname = t\n")
                    if routed:
                        subprocess.run(
                            [*config, "url.https://x-access-token:ghs_secret@github.com/O/standards.insteadOf",
                             "https://github.com/O/standards"],
                            check=True, env={**os.environ, **env})
                    if other:
                        subprocess.run(
                            [*config, "url.git@github.com:.insteadOf", "gh:"],
                            check=True, env={**os.environ, **env})
                    result = run(extract_run_block(text, UNROUTE_STEP), home, **env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    remaining = (home / ".gitconfig").read_text()
                    self.assertNotIn("ghs_secret", remaining)
                    self.assertEqual("git@github.com:" in remaining, other)


class LintHooksExecutionTests(unittest.TestCase):
    def test_commit_msg_smoke_step_is_retired(self) -> None:
        self.assertNotIn("commit-msg", LINT)
        self.assertNotIn("smoke-test commit-msg hook", LINT)

    def test_full_knowledgebase_check_runs_before_the_rewrite_guard(self) -> None:
        step = extract_step(LINT, "Run the full knowledgebase check")
        self.assertIn("if: needs.detect.outputs.has_knowledgebase == 'true'", step)
        self.assertIn("run: mise exec -- lefthook run kb-check", step)
        self.assertNotIn("continue-on-error", step)
        positions = [
            LINT.index("- name: Run lefthook pre-commit across all files"),
            LINT.index("- name: Run lefthook pre-commit on changed fork files"),
            LINT.index("- name: Run the full knowledgebase check"),
            LINT.index("- name: Fail if a hook rewrote a tracked file"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_hook_runs_are_shared_by_both_config_models(self) -> None:
        for step in ("Run lefthook pre-commit across all files",
                     "Run lefthook pre-commit on changed fork files"):
            with self.subTest(step=step):
                self.assertNotIn("config_model", extract_step(LINT, step))
        self.assertIn("--all-files --no-stage-fixed",
                      extract_step(LINT, "Run lefthook pre-commit across all files"))


class OrganizationTemplateTests(unittest.TestCase):
    FETCH = "Fetch the organization PR template"

    def fetch(self, *, sha: str, body: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "bin" / "gh"
            gh.parent.mkdir()
            gh.write_text('#!/bin/sh\necho "$*" > "$ROOT/gh-args"\nprintf "%s" "$BODY"\n')
            gh.chmod(0o755)
            result = run(
                extract_run_block(GATES, self.FETCH), root,
                PATH=f"{gh.parent}:{os.environ['PATH']}", ROOT=str(root), BODY=body,
                TEMPLATE_SHA=sha, RUNNER_TEMP=str(root),
                GITHUB_OUTPUT=str(root / "out"), GITHUB_STEP_SUMMARY=str(root / "summary"),
            )
            out = outputs(root / "out")
            args = (root / "gh-args").read_text() if (root / "gh-args").exists() else ""
            fetched = Path(out["path"]).read_text() if "path" in out else None
            self.last = (out, args, fetched)
            return result, root

    def test_template_is_read_at_the_workflow_commit(self) -> None:
        sha = "a" * 40
        result, _ = self.fetch(sha=sha, body=TEMPLATE)
        self.assertEqual(result.returncode, 0, result.stderr)
        out, args, fetched = self.last
        self.assertIn(
            f"/repos/Rubio-Enterprises/.github/contents/.github/PULL_REQUEST_TEMPLATE.md?ref={sha}",
            args,
        )
        self.assertEqual(fetched, TEMPLATE)
        self.assertTrue(out["path"].endswith("/org-pr-template/PULL_REQUEST_TEMPLATE.md"))

    def test_bad_revision_or_template_fails_closed(self) -> None:
        for sha, body in (("refs/tags/gates/wf-v1", TEMPLATE), ("a" * 40, "# no rules\n")):
            with self.subTest(sha=sha):
                result, _ = self.fetch(sha=sha, body=body)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("path", self.last[0])

    def test_gate_wiring(self) -> None:
        fetch = extract_step(GATES, self.FETCH)
        self.assertIn("if: steps.applicability.outputs.pr_conventions == 'true'", fetch)
        self.assertIn("TEMPLATE_SHA: ${{ github.workflow_sha }}", fetch)
        gate = extract_step(GATES, "Gate — PR conventions")
        self.assertIn("PR_TEMPLATE_PATH: ${{ steps.pr_template.outputs.path }}", gate)
        setup = extract_step(GATES, "Confirm common setup")
        self.assertIn("PR_TEMPLATE_OUTCOME: ${{ steps.pr_template.outcome }}", setup)
        self.assertIn('require_success "organization PR template fetch"', setup)

    def test_template_is_the_single_source_of_the_checked_rules(self) -> None:
        match = re.search(r"<!-- pr-conventions: (\{.*?\}) -->", TEMPLATE)
        self.assertIsNotNone(match)
        rules = json.loads(match.group(1))
        self.assertGreater(rules["maxWords"], 0)
        headings = re.findall(r"(?m)^## (.+?)\s*$", TEMPLATE)
        for heading in rules["required"]:
            self.assertIn(heading, headings)


class MiseFloorTests(unittest.TestCase):
    def test_every_fallback_meets_the_native_config_minimum(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for pin in re.findall(r'MISE_VERSION: "([0-9]{4}\.[0-9]+\.[0-9]+)"', path.read_text()):
                with self.subTest(workflow=path.name):
                    self.assertGreaterEqual(tuple(map(int, pin.split("."))), (2026, 10, 19))


if __name__ == "__main__":
    unittest.main()
