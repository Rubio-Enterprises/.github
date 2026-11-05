from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from test_ruby_workflows import extract_step

TEXT = (Path(__file__).resolve().parents[1] / ".github/workflows/e2e.yml").read_text()


def run_body(name):
    step = extract_step(TEXT, name)
    return textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n        timeout-minutes:", 1)[0].split("\n        shell:", 1)[0])


class E2E(unittest.TestCase):
    def test_routes_through_the_shared_image_router(self):
        self.assertIn("workflows/project-route.yml@main", TEXT)
        self.assertIn("fromJSON(needs.route.outputs.runner)", TEXT)
        self.assertNotIn("mise", TEXT)
        self.assertNotIn("npm run", TEXT)
        self.assertIn("persist-credentials: false", TEXT)

    def test_script_and_hosted_paths_execute_the_same_suite(self):
        for image in ("true", "false"):
            with self.subTest(image=image), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "bin").mkdir()
                for name in ("setup", "e2e"):
                    p = root / "bin" / f"{name}.sh"
                    p.write_text(f"#!/bin/sh\necho {name} >> calls\n")
                    p.chmod(0o755)
                just = root / "bin/just"
                just.write_text('#!/bin/sh\necho "just $*" >> calls\n')
                just.chmod(0o755)
                env = {**os.environ, "CI_IMAGE": image, "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
                for step in ("Install repository dependencies", "Run E2E suite"):
                    result = subprocess.run(["bash", "-euc", run_body(step)], cwd=root, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                expected = ["setup", "e2e"] if image == "true" else ["just build", "just setup", "just e2e"]
                self.assertEqual((root / "calls").read_text().splitlines(), expected)

    def test_junit_rejects_missing_empty_skipped_failed_and_bad_xml(self):
        body = run_body("Require executed E2E tests")
        reports = [None, "bad xml", "<testsuites/>", '<testsuite><testcase><skipped/></testcase></testsuite>',
                   '<testsuite><testcase><failure/></testcase></testsuite>', '<testsuite><testcase/></testsuite>']
        for xml in reports:
            with self.subTest(xml=xml), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "reports/e2e").mkdir(parents=True)
                if xml is not None: (root / "reports/e2e/junit.xml").write_text(xml)
                result = subprocess.run(["bash", "-euc", body], cwd=root, env={**os.environ, "SUITE_OUTCOME": "success"}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, xml == reports[-1], result.stderr)
