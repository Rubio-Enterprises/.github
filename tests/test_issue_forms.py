import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / ".github/ISSUE_TEMPLATE"
FORM_PATHS = sorted(path for path in TEMPLATES.glob("*.yml") if path.name != "config.yml")


class IssueFormPathTests(unittest.TestCase):
    def test_shared_forms_use_the_canonical_directory(self):
        self.assertTrue(FORM_PATHS, "expected at least one shared issue form")
        self.assertEqual({path for path in TEMPLATES.iterdir() if path.is_file()}, set(FORM_PATHS))
        for alternate in ("ISSUE_TEMPLATE", "docs/ISSUE_TEMPLATE"):
            with self.subTest(path=alternate):
                self.assertFalse((ROOT / alternate).exists())


class IssueFormStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.forms = {
            path.name: json.loads(subprocess.check_output(["yq", "-o=json", str(path)]))
            for path in FORM_PATHS
        }

    def test_metadata_describes_distinct_chooser_entries(self):
        names = []
        for name, form in self.forms.items():
            with self.subTest(form=name):
                self.assertEqual(set(form), {"name", "description", "type", "labels", "body"})
                for key in ("name", "description", "type"):
                    self.assertTrue(form[key].strip(), key)
                names.append(form["name"].strip().casefold())
                self.assertIsInstance(form["labels"], list)
                for label in form["labels"]:
                    self.assertTrue(label.strip())
                self.assertIsInstance(form["body"], list)
        self.assertEqual(len(names), len(set(names)), "duplicate issue form name")

    def test_fields_have_unique_ids_and_explicit_validations(self):
        for name, form in self.forms.items():
            with self.subTest(form=name):
                fields = [field for field in form["body"] if field["type"] != "markdown"]
                self.assertTrue(fields, "expected at least one answer field")
                ids = [field["id"] for field in fields]
                self.assertEqual(len(ids), len(set(ids)), "duplicate field ID")
                for field in fields:
                    with self.subTest(field=field["id"]):
                        self.assertRegex(field["id"], r"^[a-zA-Z0-9_-]+\Z")
                        self.assertTrue(field["attributes"]["label"].strip())
                        self.assertIsInstance(field["validations"]["required"], bool)

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
