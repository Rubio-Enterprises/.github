"""Behavioral contract tests for the Audit Required Governance Workflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "audit.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")
SOURCE_REPOSITORY = "Rubio-Enterprises/.github"
DIRECT_WORKFLOW_PREFIX = f"{SOURCE_REPOSITORY}/.github/workflows/audit.yml@"
EXPECTED_NOOP_GUARD = (
    "!startsWith(github.workflow_ref, "
    f"'{DIRECT_WORKFLOW_PREFIX}') || "
    f"github.repository != '{SOURCE_REPOSITORY}'"
)


class AuditWorkflowContractTests(unittest.TestCase):
    def test_direct_required_workflow_events_include_pull_request_and_merge_group(
        self,
    ) -> None:
        triggers = re.search(
            r'^"on":\n(?P<body>.*?)^permissions:',
            WORKFLOW_TEXT,
            re.DOTALL | re.MULTILINE,
        )
        if triggers is None:
            self.fail("workflow trigger block is missing")

        body = triggers.group("body")
        self.assertRegex(body, r"(?m)^  workflow_call:\s*$")
        self.assertRegex(body, r"(?m)^  pull_request:\s*$")
        self.assertRegex(body, r"(?m)^  merge_group:\s*$")

    def test_source_repository_noop_guard_matches_the_complete_contract(self) -> None:
        guard = re.search(
            r"^    if: >-\n(?P<body>(?:^      .*\n)+)^    runs-on:",
            WORKFLOW_TEXT,
            re.MULTILINE,
        )
        if guard is None:
            self.fail("audit job if guard is missing")

        condition = " ".join(line.strip() for line in guard.group("body").splitlines())
        self.assertEqual(condition, EXPECTED_NOOP_GUARD)
        self.assertNotIn("github.event_name", condition)

    def test_source_repository_noop_guard_truth_table(self) -> None:
        cases = {
            "direct source pull request": (
                DIRECT_WORKFLOW_PREFIX + "refs/heads/main",
                SOURCE_REPOSITORY,
                False,
            ),
            "direct source merge group": (
                DIRECT_WORKFLOW_PREFIX + "refs/heads/main",
                SOURCE_REPOSITORY,
                False,
            ),
            "directly injected consumer workflow": (
                DIRECT_WORKFLOW_PREFIX + "refs/tags/gates/wf-v1",
                "Rubio-Enterprises/consumer",
                True,
            ),
            "reusable consumer call": (
                "Rubio-Enterprises/consumer/.github/workflows/standards.yml@refs/heads/main",
                "Rubio-Enterprises/consumer",
                True,
            ),
            "reusable call from another source workflow": (
                "Rubio-Enterprises/.github/.github/workflows/workflow-validation.yml@refs/heads/main",
                SOURCE_REPOSITORY,
                True,
            ),
        }

        for name, (workflow_ref, repository, expected) in cases.items():
            with self.subTest(name=name):
                should_run = (
                    not workflow_ref.startswith(DIRECT_WORKFLOW_PREFIX)
                    or repository != SOURCE_REPOSITORY
                )
                self.assertEqual(should_run, expected)


if __name__ == "__main__":
    unittest.main()
