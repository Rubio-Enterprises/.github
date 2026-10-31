from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MainDeliveryTests(unittest.TestCase):
    def test_retired_publication_entrypoints_and_configuration_stay_absent(self):
        for path in (
            ".github/plumbing-ref",
            ".github/scripts/plumbing_ref.py",
            ".github/workflows/plumbing-ref-publish.yml",
            ".github/workflows/plumbing-ref-rollback.yml",
            ".github/workflows/floating-tag-floor-check.yml",
            "release-please-config.json",
            ".release-please-manifest.json",
        ):
            with self.subTest(path=path):
                self.assertFalse((ROOT / path).exists())

    def test_release_please_workflow_is_only_the_consumer_reusable(self):
        # The file is the reusable that consumers call; it must never release this repository.
        workflow = (ROOT / ".github/workflows/release-please.yml").read_text()
        triggers = workflow.split('"on":', 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_call:", triggers)
        for event in ("push:", "pull_request:", "schedule:", "workflow_dispatch:", "release:"):
            self.assertNotIn(event, triggers)

    def test_workflow_validation_does_not_invoke_retired_publication_code(self):
        validation = (ROOT / ".github/workflows/workflow-validation.yml").read_text()
        self.assertIn("run: actionlint", validation)
        self.assertNotIn("plumbing_ref", validation)
        self.assertNotIn("plumbing-ref", validation)


if __name__ == "__main__":
    unittest.main()
