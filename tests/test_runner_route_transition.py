from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import LINT_TEXT, ROOT, TYPECHECK_TEXT, extract_run_block, run_script


class RouteTransitionTests(unittest.TestCase):
    def test_all_answer_generations_select_the_same_size(self) -> None:
        for text, step, answer in (
            (LINT_TEXT, "Pick runner from the language facets", "lint_hooks_workload_class"),
            (TYPECHECK_TEXT, "Pick the runner from the declared Workload Class", "typecheck_workload_class"),
        ):
            script = extract_run_block(text, step)
            for value, expected in (
                ("small", "small"), ("light", "small"), ("glue", "small"),
                ("medium", "medium"), ("heavy", "medium"), ("glue-heavy", "medium"),
                ("'MEDIUM'", "medium"), ('"glue-heavy"', "medium"), ("", "small"),
                ("medium-extra", "small"),
            ):
                with self.subTest(answer=answer, value=value), tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    (repo / ".copier-answers.yml").write_text(f"{answer}: {value}\n")
                    result = run_script(
                        script, repo, LIGHT_RUNNER='["small"]', HEAVY_RUNNER='["medium"]',
                        MACOS_RUNNER='["macos"]', RUBY_RUNNER='["ruby"]',
                    )
                    self.assertEqual(json.loads(result["runner"]), [expected])

    def test_transitional_expressions_prefer_new_then_old_then_hosted(self) -> None:
        expressions = []
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            for line in path.read_text().splitlines():
                if not line.lstrip().startswith("#") and "fromJSON(vars.RUNNERS" in line:
                    if ".light ||" in line or ".heavy ||" in line:
                        expressions.append(line)
        self.assertTrue(expressions)
        for expression in expressions:
            with self.subTest(expression=expression):
                keys = re.findall(r"fromJSON\(vars.RUNNERS \|\| '\{\}'\)\.(\w+)", expression)
                old, new, hosted = ("light", "small", "ubuntu-slim") if "light" in keys else (
                    "heavy", "medium", "ubuntu-latest"
                )
                self.assertEqual(keys, [new, old])
                self.assertIn(f"""fromJSON('["{hosted}"]')""", expression)
                for routes, expected in (
                    ({}, [hosted]), ({old: ["old"]}, ["old"]),
                    ({old: ["old"], new: ["new"]}, ["new"]), ({new: ["new"]}, ["new"]),
                ):
                    actual = next((routes[key] for key in keys if routes.get(key)), [hosted])
                    self.assertEqual(actual, expected)
