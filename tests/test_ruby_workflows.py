"""Focused contracts for Ruby routing and dependency setup in reusable workflows."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINT_WORKFLOW = ROOT / ".github" / "workflows" / "lint-hooks.yml"
SMOKE_WORKFLOW = ROOT / ".github" / "workflows" / "cloud-setup-smoke.yml"
LINT_TEXT = LINT_WORKFLOW.read_text(encoding="utf-8")
SMOKE_TEXT = SMOKE_WORKFLOW.read_text(encoding="utf-8")
RUBY_SETUP_PATTERN = re.compile(
    r"ruby/setup-ruby@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+"
)


def ruby_setup_pin(workflow_text: str) -> str:
    """Read the single versioned, digest-pinned Ruby action from a workflow."""
    pins = re.findall(r"^ *uses: (ruby/setup-ruby@.*)$", workflow_text, re.MULTILINE)
    if len(pins) != 1 or not RUBY_SETUP_PATTERN.fullmatch(pins[0]):
        raise AssertionError(f"expected exactly one versioned 40-hex Ruby pin, found {pins}")
    return pins[0]


def extract_run_block(workflow_text: str, step_name: str) -> str:
    """Extract and dedent the literal shell body for a named workflow step."""
    lines = workflow_text.splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    run_index = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    indent = len(lines[run_index]) - len(lines[run_index].lstrip()) + 2
    body: list[str] = []
    for line in lines[run_index + 1 :]:
        if line and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line else "")
    return "\n".join(body) + "\n"


def extract_step(workflow_text: str, step_name: str) -> str:
    """Extract one list-item step, excluding adjacent comments and steps."""
    lines = workflow_text.splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith(" " * indent + "- name:")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def run_script(script: str, repo: Path, **extra_env: str) -> dict[str, str]:
    """Run an extracted Actions shell body and parse its GITHUB_OUTPUT."""
    output = repo / "github-output"
    env = os.environ.copy()
    env.update(extra_env, GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


class LintRubyRoutingTests(unittest.TestCase):
    """Ruby is an explicit, capability-first route; old consumers do not move."""

    def test_ruby_action_is_consistent_across_expected_workflows(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        expected = {"lint-hooks.yml", "cloud-setup-smoke.yml", "testflight.yml"}
        actual = {
            path.name
            for path in workflow_dir.glob("*.yml")
            if "ruby/setup-ruby@" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(actual, expected)
        pins = {
            ruby_setup_pin((workflow_dir / name).read_text(encoding="utf-8"))
            for name in expected
        }
        self.assertEqual(len(pins), 1)


    script = extract_run_block(LINT_TEXT, "Pick runner from the language facets")
    runners = {
        "RUBY_RUNNER": '["ruby"]',
        "MACOS_RUNNER": '["macos"]',
        "GLUE_RUNNER": '["glue"]',
        "HEAVY_RUNNER": '["glue-heavy"]',
    }

    def route(self, answers: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".copier-answers.yml").write_text(
                textwrap.dedent(answers), encoding="utf-8"
            )
            return run_script(self.script, repo, **self.runners)

    def test_platform_capability_wins_and_keeps_ruby_setup_enabled(self) -> None:
        outputs = self.route(
            """
            has_ruby: true
            has_swift: true
            lint_hooks_workload_class: glue-heavy
            """
        )
        self.assertEqual(outputs["has_ruby"], "true")
        self.assertEqual(outputs["runner"], '["macos"]')

    def test_ruby_wins_over_resource_route(self) -> None:
        outputs = self.route(
            """
            has_ruby: true
            lint_hooks_workload_class: glue-heavy
            """
        )
        self.assertEqual(outputs["has_ruby"], "true")
        self.assertEqual(outputs["runner"], '["ruby"]')

    def test_non_ruby_routes_are_unchanged(self) -> None:
        cases = (
            ("has_ruby: false\nhas_swift: true\n", '["macos"]'),
            ("has_ruby: false\nlint_hooks_workload_class: glue-heavy\n", '["glue-heavy"]'),
            ("repo_type: private\n", '["glue"]'),
        )
        for answers, expected in cases:
            with self.subTest(answers=answers):
                outputs = self.route(answers)
                self.assertEqual(outputs["has_ruby"], "false")
                self.assertEqual(outputs["runner"], expected)

    def test_only_literal_true_enables_ruby(self) -> None:
        outputs = self.route("has_ruby: 'true'\n")
        self.assertEqual(outputs["has_ruby"], "false")
        self.assertEqual(outputs["runner"], '["glue"]')

    def test_lint_setup_is_pinned_locked_and_before_mise(self) -> None:
        step = extract_step(LINT_TEXT, "Install Ruby and locked bundle")
        self.assertIn("if: needs.detect.outputs.has_ruby == 'true'", step)
        self.assertIn(f"uses: {ruby_setup_pin(LINT_TEXT)}", step)
        self.assertIn("bundler-cache: true", step)
        self.assertNotIn("ruby-version", step)
        self.assertLess(
            LINT_TEXT.index("- name: Install Ruby and locked bundle"),
            LINT_TEXT.index("uses: jdx/mise-action@"),
        )
        self.assertIn(
            "RUBY_RUNNER: ${{ vars.RUNNER_RUBY || '[\"ubuntu-24.04-arm\"]' }}",
            LINT_TEXT,
        )


class CloudSmokeRubyTests(unittest.TestCase):
    """Cloud smoke installs the lockfile-backed bundle only for explicit Ruby."""

    script = extract_run_block(
        SMOKE_TEXT, "Resolve whether this repo has a smoke-capable cloud-setup chain"
    )

    def probe(self, answers: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".copier-answers.yml").write_text(answers, encoding="utf-8")
            core = repo / "scripts" / "common" / "cloud-setup.sh"
            core.parent.mkdir(parents=True)
            core.write_text("# CLOUD_SETUP_SMOKE\n", encoding="utf-8")
            (repo / "scripts" / "cloud-setup-shim.sh").write_text(
                "#!/usr/bin/env bash\n", encoding="utf-8"
            )
            return run_script(self.script, repo)

    def test_probe_exposes_only_explicit_ruby(self) -> None:
        self.assertEqual(self.probe("has_ruby: true\n")["has_ruby"], "true")
        self.assertEqual(self.probe("has_ruby: false\n")["has_ruby"], "false")
        self.assertEqual(self.probe("repo_type: private\n")["has_ruby"], "false")

    def test_setup_is_conditional_pinned_locked_and_before_core(self) -> None:
        step = extract_step(SMOKE_TEXT, "Install Ruby and locked bundle")
        self.assertIn("steps.probe.outputs.runnable == 'true'", step)
        self.assertIn("steps.probe.outputs.has_ruby == 'true'", step)
        self.assertIn(f"uses: {ruby_setup_pin(SMOKE_TEXT)}", step)
        self.assertIn("bundler-cache: true", step)
        self.assertNotIn("ruby-version", step)
        self.assertLess(
            SMOKE_TEXT.index("- name: Install Ruby and locked bundle"),
            SMOKE_TEXT.index("- name: Run the cloud setup chain"),
        )


if __name__ == "__main__":
    unittest.main()
