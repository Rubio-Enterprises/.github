import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / ".github/ISSUE_TEMPLATE"
FORMS = {
    "bug": ("Bug", "bug"),
    "enhancement": ("Enhancement", "enhancement"),
    "refactoring": ("Refactoring", "task"),
    "task": ("Task", "task"),
}
PROPOSAL_FIELDS = {
    "outcome": "Outcome",
    "problem": "Problem / motivation",
    "in-scope": "In scope",
    "out-of-scope": "Out of scope",
    "done-when": "Done when",
    "verification": "Verification",
    "alternatives": "Alternatives considered",
    "context": "Context links",
}
BUG_FIELDS = {
    "description": "Description",
    "steps": "Steps",
    "expected": "Expected behavior",
    "actual": "Actual behavior",
    "version": "Version / commit",
    "environment": "Environment",
    "reproduction": "Reproduction",
    "logs": "Logs",
    "context": "Additional context",
}


class IssueFormPathTests(unittest.TestCase):
    def test_complete_shared_set_uses_the_canonical_directory(self):
        self.assertEqual(
            {path.name for path in TEMPLATES.iterdir() if path.is_file()},
            {f"{name}.yml" for name in FORMS},
        )
        for alternate in ("ISSUE_TEMPLATE", "docs/ISSUE_TEMPLATE"):
            with self.subTest(path=alternate):
                self.assertFalse((ROOT / alternate).exists())


class IssueFormStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        yq = shutil.which("yq")
        if yq is None:
            raise unittest.SkipTest("yq v4 is required for local issue-form YAML checks")
        cls.forms = {}
        for name in FORMS:
            result = subprocess.run(
                [yq, "-o=json", ".", str(TEMPLATES / f"{name}.yml")],
                capture_output=True, text=True, check=True,
            )
            cls.forms[name] = json.loads(result.stdout)

    def test_metadata_sets_native_types_and_only_the_intake_label(self):
        for name, (display_name, issue_type) in FORMS.items():
            with self.subTest(form=name):
                form = self.forms[name]
                self.assertEqual(set(form), {"name", "description", "type", "labels", "body"})
                self.assertEqual(form["name"], display_name)
                self.assertTrue(form["description"].strip())
                self.assertEqual(form["type"], issue_type)
                self.assertEqual(form["labels"], ["needs-triage"])

    def test_fields_have_unique_ids_and_the_agreed_diagnostic_or_proposal_sections(self):
        for name, form in self.forms.items():
            with self.subTest(form=name):
                fields = [field for field in form["body"] if field["type"] != "markdown"]
                expected = BUG_FIELDS if name == "bug" else PROPOSAL_FIELDS
                ids = [field["id"] for field in fields]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(ids, list(expected))
                for field in fields:
                    self.assertRegex(field["id"], r"^[a-zA-Z0-9_-]+$")
                    self.assertEqual(field["attributes"]["label"], expected[field["id"]])

    def test_requiredness_keeps_bug_reproduction_optional_at_intake(self):
        for name, form in self.forms.items():
            optional = {"reproduction", "logs", "context"} if name == "bug" else {"context"}
            for field in form["body"]:
                if field["type"] == "markdown":
                    continue
                with self.subTest(form=name, field=field["id"]):
                    self.assertIs(field["validations"]["required"], field["id"] not in optional)
                    if name != "bug":
                        self.assertEqual(field["type"], "textarea")

    def test_examples_are_placeholders_not_answers_or_attestations(self):
        for name, form in self.forms.items():
            self.assertEqual(form["body"][0]["type"], "markdown")
            for field in form["body"]:
                with self.subTest(form=name, field=field.get("id", "intro")):
                    attributes = field["attributes"]
                    if field["type"] == "markdown":
                        self.assertTrue(attributes["value"].strip())
                        continue
                    self.assertIn(field["type"], {"input", "textarea"})
                    self.assertTrue(attributes["description"].strip())
                    self.assertTrue(attributes["placeholder"].strip())
                    self.assertNotIn("value", attributes)
                    self.assertNotIn("options", attributes)


if __name__ == "__main__":
    unittest.main()
