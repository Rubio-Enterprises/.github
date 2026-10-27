"""Contracts for the standards ADR-0033 native config model in the central workflows.

A repository answering `config_model: native` provisions its Shared Config
Checkout through the shared `standards-bootstrap` action in every workflow that
runs mise in the consumer: the lint-hooks, typecheck and build Gate Families and
the e2e and cloud-setup-smoke reusables. Every other repository keeps the
rendered model's mise-action step. The action's own behaviour is covered by
test_standards_bootstrap_action.py.
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
from test_standards_bootstrap_action import action_inputs

WORKFLOWS = ROOT / ".github" / "workflows"
LINT = (WORKFLOWS / "lint-hooks.yml").read_text(encoding="utf-8")
TYPECHECK = (WORKFLOWS / "typecheck-ts.yml").read_text(encoding="utf-8")
BUILD = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
E2E = (WORKFLOWS / "e2e.yml").read_text(encoding="utf-8")
SMOKE = (WORKFLOWS / "cloud-setup-smoke.yml").read_text(encoding="utf-8")
GATES = (WORKFLOWS / "standards-gates.yml").read_text(encoding="utf-8")
TEMPLATE = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")

BOOTSTRAP_STEP = "Bootstrap the Shared Config Checkout (native config model)"
ACTION_REF = re.compile(
    r"uses: (Rubio-Enterprises/\.github/actions/standards-bootstrap@[0-9a-f]{40}) # v4\n"
)
# Step names the workflows carried before the shared action existed.
INLINE_STEPS = (
    "Require standards-reader credentials (native config model)",
    "Mint standards read token (native config model)",
    "Route the Shared Config Checkout clone through the App token",
    "Require the Shared Config Checkout (native config model)",
    "Remove the standards read route",
)
MISE_STEP_NAMES = {
    "lint-hooks": "Install consumer-repo runtimes (lefthook + pinned hook tools)",
    "typecheck-ts": "Install consumer-repo runtimes",
    "build": "Install consumer-repo runtimes",
    "e2e": "Install consumer-repo runtimes",
    "cloud-setup-smoke": "Install the consumer's pinned toolchain",
}
NATIVE_WORKFLOWS = {
    "lint-hooks": (LINT, "needs.detect.outputs.config_model"),
    "typecheck-ts": (TYPECHECK, "needs.route.outputs.config_model"),
    "build": (BUILD, "needs.detect.outputs.config_model"),
    "e2e": (E2E, "steps.config-model.outputs.config_model"),
    "cloud-setup-smoke": (SMOKE, "steps.probe.outputs.config_model"),
}
# Workflows a consumer can call, so org secrets arrive only when forwarded.
CALLABLE = ("typecheck-ts", "e2e", "cloud-setup-smoke")
# Workflows whose later steps keep the standards route: lint-hooks as before
# the action, cloud-setup-smoke for the setup chain.
KEEP_ROUTE = ("lint-hooks", "cloud-setup-smoke")
# The first step that runs repository code after the bootstrap.
REPOSITORY_CODE = {
    "lint-hooks": "Install JS deps (so lefthook.local.yml pnpm hooks resolve workspace packages)",
    "typecheck-ts": "Install JS deps",
    "build": "Require the build task",
    "e2e": "Install JS deps (lockfile-aware)",
    "cloud-setup-smoke": "Run the cloud setup chain under CLOUD_SETUP_SMOKE",
}
RUNNERS = dict(LIGHT_RUNNER='["small"]', HEAVY_RUNNER='["medium"]',
               MACOS_RUNNER='["macos"]', RUBY_RUNNER='["ruby"]')
PICK_STEPS = {
    "lint-hooks": (LINT, "Pick runner from the language facets"),
    "typecheck-ts": (TYPECHECK, "Pick the runner from the declared Workload Class"),
    "e2e": (E2E, "Read the config model"),
    "cloud-setup-smoke": (SMOKE, "Resolve whether this repo has a smoke-capable cloud-setup chain"),
}


def condition(text: str, name: str) -> str:
    """Return a step's `if:` expression with folded lines joined."""
    lines = extract_step(text, name).splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("if:"):
            value = stripped[3:].strip()
            if value not in (">-", ">", "|"):
                return value
            indent = len(line) - len(line.lstrip())
            folded = []
            for more in lines[index + 1:]:
                if more.strip() and len(more) - len(more.lstrip()) <= indent:
                    break
                folded.append(more.strip())
            return " ".join(folded)
    return ""


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
                (None, "native"),
                ("has_python: true\n", "native"),
                ("config_model: rendered\n", None),
                ("config_model: native\n", "native"),
                ('config_model: "native"\n', "native"),
                ("# config_model: native\n", "native"),
                ("config_model: Native\n", None),
            ):
                with self.subTest(workflow=name, answers=answers), \
                        tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    if answers is not None:
                        (repo / ".copier-answers.yml").write_text(answers)
                    result = run(script, repo, GITHUB_OUTPUT=str(repo / "out"), **RUNNERS)
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
                result = run(script, repo, GITHUB_OUTPUT=str(repo / "out"), **RUNNERS)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(outputs(repo / "out")["has_knowledgebase"], expected)


def native_guard(name: str, model: str, op: str) -> str:
    return "steps.probe.outputs.runnable == 'true'" if name == "cloud-setup-smoke" else ""


def with_inputs(step: str) -> dict[str, str]:
    block = step.split("\n        with:\n", 1)[1]
    return dict(re.findall(r"(?m)^          ([a-z-]+): (.*)$", block))


class NativeBootstrapContractTests(unittest.TestCase):
    def test_every_workflow_calls_one_pinned_action_for_native_repositories(self) -> None:
        refs = set()
        for name, (text, model) in NATIVE_WORKFLOWS.items():
            with self.subTest(workflow=name):
                step = extract_step(text, BOOTSTRAP_STEP)
                match = ACTION_REF.search(step)
                self.assertIsNotNone(match, step)
                refs.add(match.group(1))
                self.assertEqual(condition(text, BOOTSTRAP_STEP), native_guard(name, model, "=="))
                self.assertEqual(len(ACTION_REF.findall(text)), 1)
        # One ref, so landing the action rewrites a single SHA everywhere.
        self.assertEqual(len(refs), 1, refs)

    def test_action_inputs(self) -> None:
        declared = set(action_inputs())
        for name, (text, model) in NATIVE_WORKFLOWS.items():
            with self.subTest(workflow=name):
                given = with_inputs(extract_step(text, BOOTSTRAP_STEP))
                self.assertLessEqual(set(given), declared)
                expected = {
                    "config-model": f"${{{{ {model} }}}}",
                    "app-id": "${{ secrets.STANDARDS_READER_APP_ID }}",
                    "private-key": "${{ secrets.STANDARDS_READER_APP_PRIVATE_KEY }}",
                    "mise-version": "${{ steps.mise-version.outputs.version }}",
                    "cache-key-prefix": "mise-v1-uvpy${{ steps.uv-python.outputs.version }}",
                }
                if name in KEEP_ROUTE:
                    expected["keep-route"] = '"true"'
                self.assertEqual(given, expected)

    def test_no_inline_native_setup_remains(self) -> None:
        for name, (text, _) in NATIVE_WORKFLOWS.items():
            for step in INLINE_STEPS:
                with self.subTest(workflow=name, step=step):
                    self.assertNotIn(f"- name: {step}\n", text)
            self.assertNotIn("bootstrap_args", text)

    def test_bootstrap_precedes_repository_code(self) -> None:
        for name, later in REPOSITORY_CODE.items():
            text, _ = NATIVE_WORKFLOWS[name]
            with self.subTest(workflow=name):
                self.assertLess(text.index(f"- name: {BOOTSTRAP_STEP}\n"),
                                text.index(f"- name: {later}\n"))

    def test_lint_hooks_mints_its_own_route_only_for_rendered_repositories(self) -> None:
        for step in ("Mint standards read token (lefthook remotes fetch)",
                     "Route the lefthook standards remote through the App token"):
            with self.subTest(step=step):
                self.assertNotIn(f"- name: {step}\n", LINT)


class CallerCredentialTests(unittest.TestCase):
    """A called workflow sees org secrets only when its caller forwards them."""

    def test_callable_workflows_declare_the_optional_reader_secrets(self) -> None:
        for name in CALLABLE:
            text, _ = NATIVE_WORKFLOWS[name]
            call = text.split("workflow_call:", 1)[1].split("permissions:", 1)[0]
            for secret in ("STANDARDS_READER_APP_ID", "STANDARDS_READER_APP_PRIVATE_KEY"):
                with self.subTest(workflow=name, secret=secret):
                    self.assertRegex(
                        call, rf"(?m)^      {secret}:\n(?:        .*\n)*?        required: false$"
                    )


class ReusableOrderingTests(unittest.TestCase):
    def test_config_model_is_known_before_any_native_step(self) -> None:
        for text, reader in ((E2E, "Read the config model"),
                             (SMOKE, "Resolve whether this repo has a smoke-capable cloud-setup chain")):
            with self.subTest(reader=reader):
                self.assertLess(text.index(f"- name: {reader}"), text.index(f"- name: {BOOTSTRAP_STEP}"))

    def test_smoke_probe_reports_the_model_even_without_a_chain(self) -> None:
        script = extract_run_block(SMOKE, PICK_STEPS["cloud-setup-smoke"][1])
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".copier-answers.yml").write_text("config_model: native\n")
            result = run(script, repo, GITHUB_OUTPUT=str(repo / "out"))
            self.assertEqual(result.returncode, 0, result.stderr)
            out = outputs(repo / "out")
        self.assertEqual((out["config_model"], out["runnable"]), ("native", "false"))


class LintHooksExecutionTests(unittest.TestCase):
    def test_commit_msg_smoke_step_is_retired(self) -> None:
        self.assertNotIn("commit-msg", LINT)
        self.assertNotIn("smoke-test commit-msg hook", LINT)

    def test_full_knowledgebase_check_runs_before_the_rewrite_guard(self) -> None:
        step = extract_step(LINT, "Run the full knowledgebase check")
        self.assertEqual(
            condition(LINT, "Run the full knowledgebase check"),
            "needs.detect.outputs.has_knowledgebase == 'true'",
        )
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
        # Danger and gate-pr-conventions.sh reject a relative PR_TEMPLATE_PATH;
        # the runner's RUNNER_TEMP is absolute, so the path is too.
        self.assertTrue(Path(out["path"]).is_absolute())
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

    def test_channel_reaches_the_gates_that_check_it(self) -> None:
        # MD-12 compares the Shared Config Checkout's ref with STANDARDS_CHANNEL.
        for name in ("Gate — audit", "lint-config", "Gate — PR conventions"):
            with self.subTest(step=name):
                self.assertIn(
                    "STANDARDS_CHANNEL: ${{ steps.selection.outputs.channel }}",
                    extract_step(GATES, name),
                )

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
