"""Behavioral contracts for the composite PR-conventions sub-gate."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/standards-gates.yml"


def step(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text.split(f"      - name: {name}\n", 1)[1].split("\n      - name:", 1)[0]


def run_body(name: str) -> str:
    return "\n".join(line[10:] for line in step(name).split("        run: |\n", 1)[1].splitlines())


class PrConventionsTests(unittest.TestCase):
    def run_step(self, name: str, env: dict[str, str], scripts: dict[str, str] | None = None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, body in (scripts or {}).items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            output = root / "output"
            summary = root / "summary"
            result = subprocess.run(
                ["bash", "-c", run_body(name)], cwd=root,
                env={**os.environ, **env, "RUNNER_TEMP": str(root),
                     "STANDARDS_ROOT": str(root / ".standards"),
                     "PATH": f"{root}:{os.environ['PATH']}",
                     "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
                capture_output=True, text=True, check=False,
            )
            return result, output.read_text() if output.exists() else "", summary.read_text() if summary.exists() else ""

    def test_selection_reads_only_new_property_and_validates_boolean(self):
        for value, succeeds in [(True, True), (False, True), ("invalid", False)]:
            with self.subTest(value=value):
                properties = json.dumps([{"property_name": "gate-pr-conventions", "value": value}])
                result, output, _ = self.run_step(
                    "Resolve gate selection",
                    {"GITHUB_REPOSITORY": "Rubio-Enterprises/consumer", "WORKFLOW_REF": "published",
                     "PROPERTIES": properties},
                    {"gh": '#!/bin/sh\nprintf "%s\\n" "$PROPERTIES"\n'},
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                if succeeds:
                    self.assertIn(f"pr_conventions={str(value).lower()}\n", output)
                    self.assertIn(f"any_selected={str(value).lower()}\n", output)
        text = WORKFLOW.read_text()
        self.assertNotIn("gate-pr-title", text)
        self.assertNotIn("pr_title", text)
        self.assertNotIn("commitlint", text)

    def test_unpublished_source_run_disables_selection(self):
        for ref in ["refs/heads/feature", "refs/pull/1/merge"]:
            result, output, _ = self.run_step(
                "Resolve gate selection",
                {"GITHUB_REPOSITORY": "Rubio-Enterprises/.github",
                 "WORKFLOW_REF": f"Rubio-Enterprises/.github/.github/workflows/standards-gates.yml@{ref}",
                 "PROPERTIES": '[{"property_name":"gate-pr-conventions","value":true}]'},
                {"gh": '#!/bin/sh\nprintf "%s\\n" "$PROPERTIES"\n'},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pr_conventions=false\n", output)
            self.assertIn("any_selected=false\n", output)

    def test_ring_metadata_does_not_affect_gate_selection(self):
        baseline = None
        for ring in ("stable", "canary", "unknown", ""):
            properties = json.dumps([
                {"property_name": "gate-pr-conventions", "value": True},
                {"property_name": "ring", "value": ring},
            ])
            result, output, _ = self.run_step(
                "Resolve gate selection",
                {"GITHUB_REPOSITORY": "Rubio-Enterprises/consumer", "WORKFLOW_REF": "published",
                 "PROPERTIES": properties},
                {"gh": '#!/bin/sh\nprintf "%s\\n" "$PROPERTIES"\n'},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("channel=", output)
            if baseline is None:
                baseline = output
            self.assertEqual(output, baseline)

    def test_minimal_consumers_do_not_bootstrap_lint_tools(self):
        for answers in (None, "repo_type: governance_repo\n", "repo_type: pristine_fork\n"):
            scripts = {".copier-answers.yml": answers} if answers is not None else {}
            result, output, _ = self.run_step(
                "Determine event and repository applicability",
                {"AUDIT_SELECTED": "false", "LINT_SELECTED": "true",
                 "PR_CONVENTIONS_SELECTED": "false", "SECRET_SELECTED": "false",
                 "GITHUB_EVENT_NAME": "pull_request"}, scripts,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("lint_config=false\n", output)
            self.assertIn("setup_needed=false\n", output)
            self.assertIn("standards_tools=\n", output)

    def test_applicability_installs_only_node_and_skips_merge_queue(self):
        for event, applicable, tools in [("pull_request", "true", "node"), ("merge_group", "false", "")]:
            result, output, _ = self.run_step(
                "Determine event and repository applicability",
                {"AUDIT_SELECTED": "false", "LINT_SELECTED": "false",
                 "PR_CONVENTIONS_SELECTED": "true", "SECRET_SELECTED": "false",
                 "GITHUB_EVENT_NAME": event},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"pr_conventions={applicable}\n", output)
            self.assertIn(f"setup_needed={applicable}\n", output)
            self.assertIn(f"standards_tools={tools}\n", output)

    def test_guard_and_script_verdict_contract(self):
        cases = [
            ("false", "", "failure", None, True, "not-applicable"),
            ("true", "false", "failure", None, True, "not-applicable"),
            ("true", "", "success", None, False, ""),
            ("true", "true", "failure", None, False, ""),
            ("true", "true", "success", "pass", True, "pass"),
            ("true", "true", "success", "not-applicable", True, "not-applicable"),
            ("true", "true", "success", "failure", False, ""),
        ]
        for selected, applicable, setup, verdict, succeeds, expected in cases:
            with self.subTest(case=(selected, applicable, setup, verdict)):
                scripts = {}
                if verdict:
                    scripts[".standards/ci/gate-pr-conventions.sh"] = (
                        "exit 1\n" if verdict == "failure" else
                        f'echo "verdict={verdict}" >> "$GITHUB_OUTPUT"\n'
                    )
                result, output, _ = self.run_step(
                    "Gate — PR conventions",
                    {"SELECTED": selected, "APPLICABLE": applicable, "COMMON_SETUP_OUTCOME": setup},
                    scripts,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual(output, f"verdict={expected}\n" if expected else "")

    def test_final_verdict_fails_closed(self):
        for outcome, verdict, succeeds in [("success", "pass", True), ("success", "not-applicable", True),
                                            ("failure", "", False), ("success", "", False)]:
            env = {"COMMON_SETUP_OUTCOME": "success", "PR_CONVENTIONS_OUTCOME": outcome,
                   "PR_CONVENTIONS_VERDICT": verdict}
            for gate in ["AUDIT", "LINT", "SECRET"]:
                env[f"{gate}_OUTCOME"] = "success"
                env[f"{gate}_VERDICT"] = "not-applicable"
            result, _, summary = self.run_step("Governance gate verdict", env)
            self.assertEqual(result.returncode == 0, succeeds, result.stderr)
            self.assertIn("| pr-conventions |", summary)

    def test_token_and_mise_contract_are_explicit(self):
        text = WORKFLOW.read_text()
        self.assertIn("      issues: read", text)
        self.assertIn("      pull-requests: write", text)
        self.assertIn("standards ADR-0027", text)
        gate = step("Gate — PR conventions")
        for expected in ["id: gate_pr_conventions", "if: always()", "continue-on-error: true",
                         "GITHUB_TOKEN: ${{ github.token }}", "MISE_CEILING_PATHS: ${{ github.workspace }}",
                         "STANDARDS_SHA:"]:
            self.assertIn(expected, gate)
        self.assertIn('MISE_EXEC_AUTO_INSTALL: "false"', text)
        self.assertIn("add_shims_to_path: false", text)


if __name__ == "__main__":
    unittest.main()
