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

    def test_size_routes_use_canonical_keys_and_matching_hosted_fallbacks(self) -> None:
        expressions = []
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            for line in path.read_text().splitlines():
                if line.lstrip().startswith("#") or "fromJSON(vars.RUNNERS" not in line:
                    continue
                keys = re.findall(r"fromJSON\(vars.RUNNERS \|\| '\{\}'\)\.(\w+)", line)
                self.assertFalse({"light", "heavy"} & set(keys), (path, line))
                if "small" in keys or "medium" in keys:
                    expressions.append((keys, line))
        self.assertGreaterEqual(len(expressions), 16)
        for keys, expression in expressions:
            with self.subTest(expression=expression):
                route, hosted = ("small", "ubuntu-slim") if "small" in keys else (
                    "medium", "ubuntu-latest"
                )
                self.assertEqual(keys, [route])
                self.assertIn(f"""fromJSON('["{hosted}"]')""", expression)
