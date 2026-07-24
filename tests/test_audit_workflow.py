"""Behavioral contract tests for the Audit Required Governance Workflow."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "audit.yml"
WORKFLOW_TEXT = WORKFLOW.read_text(encoding="utf-8")


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

    def test_source_repository_noop_uses_direct_workflow_identity(self) -> None:
        audit_job = re.search(
            r"^  audit:\n(?P<body>.*?)^    runs-on:",
            WORKFLOW_TEXT,
            re.DOTALL | re.MULTILINE,
        )
        if audit_job is None:
            self.fail("audit job preamble is missing")

        body = audit_job.group("body")
        self.assertIn(
            "!startsWith(github.workflow_ref, "
            "'Rubio-Enterprises/.github/.github/workflows/audit.yml@')",
            body,
        )
        self.assertIn("github.repository != 'Rubio-Enterprises/.github'", body)
        self.assertNotIn("github.event_name", body)


if __name__ == "__main__":
    unittest.main()
