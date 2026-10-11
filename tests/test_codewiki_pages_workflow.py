"""Contracts for the reusable CodeWiki Pages workflow (codewiki-pages.yml)."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/codewiki-pages.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
LIGHT_ROUTE = "${{ fromJSON(vars.RUNNERS || '{}').light || fromJSON('[\"ubuntu-slim\"]') }}"


def job_block(job: str) -> str:
    """Return the text of one top-level job (two-space indented key)."""
    lines = TEXT.splitlines()
    start = lines.index(f"  {job}:")
    end = next(
        (i for i in range(start + 1, len(lines))
         if re.match(r"^  [A-Za-z0-9_-]+:$", lines[i]) or re.match(r"^\S", lines[i])),
        len(lines),
    )
    return "\n".join(lines[start:end])


def stage_script() -> str:
    lines = TEXT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "- name: Stage CodeWiki site")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2 :] if line.strip() else "")
    return "\n".join(body) + "\n"


class WorkflowShapeTests(unittest.TestCase):
    def test_is_workflow_call_only_with_contract_inputs(self) -> None:
        on_block = TEXT.split('"on":\n', 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("  workflow_call:\n", on_block)
        self.assertNotRegex(on_block, r"(?m)^  (pull_request|push|merge_group|workflow_dispatch):")
        self.assertRegex(on_block, r"(?ms)^      path:\n.*?type: string\n\s+default: docs/codewiki\n")
        self.assertRegex(on_block, r"(?ms)^      timeout-minutes:\n.*?type: number\n\s+default: 15$")

    def test_least_privilege_per_job(self) -> None:
        self.assertIn("\npermissions:\n  contents: read\njobs:\n", TEXT)
        build, deploy = job_block("build"), job_block("deploy")
        self.assertIn("    permissions:\n      contents: read\n", build)
        self.assertNotIn("pages: write", build)
        self.assertNotIn("id-token", build)
        self.assertIn("    permissions:\n      pages: write\n      id-token: write\n", deploy)
        self.assertNotIn("contents:", deploy)

    def test_deploy_uses_github_pages_environment_and_safe_concurrency(self) -> None:
        deploy = job_block("deploy")
        self.assertIn("    needs: build\n", deploy)
        self.assertIn(
            "    environment:\n      name: github-pages\n"
            "      url: ${{ steps.deployment.outputs.page_url }}\n",
            deploy,
        )
        self.assertIn("    concurrency:\n      group: pages\n      cancel-in-progress: false\n", deploy)
        # A github.workflow-derived group resolves to the caller's name and deadlocks.
        self.assertNotIn("${{ github.workflow", TEXT)

    def test_jobs_use_light_route_and_input_timeout(self) -> None:
        for job in ("build", "deploy"):
            block = job_block(job)
            self.assertIn(f"    runs-on: {LIGHT_ROUTE}\n", block)
            self.assertIn("    timeout-minutes: ${{ inputs.timeout-minutes }}\n", block)

    def test_actions_are_sha_pinned_with_version_comments(self) -> None:
        uses = re.findall(r"^\s*(?:- )?uses: (\S+)(.*)$", TEXT, re.MULTILINE)
        self.assertEqual(
            {ref.split("@")[0] for ref, _ in uses},
            {"actions/checkout", "actions/upload-pages-artifact", "actions/deploy-pages"},
        )
        for ref, comment in uses:
            self.assertRegex(ref, r"@[0-9a-f]{40}$")
            self.assertRegex(comment, r"^ # v\d+\.\d+\.\d+$")

    def test_checkout_pin_matches_repository_pin(self) -> None:
        checkout = re.search(r"actions/checkout@([0-9a-f]{40})", TEXT).group(1)
        other = (ROOT / ".github/workflows/e2e.yml").read_text(encoding="utf-8")
        self.assertIn(f"actions/checkout@{checkout}", other)

    def test_upload_publishes_the_staged_dir_not_the_input_path(self) -> None:
        build = job_block("build")
        self.assertIn("          path: ${{ steps.stage.outputs.dir }}\n", build)
        self.assertNotIn("path: ${{ inputs.path }}", build)
        # The input reaches the shell through env, never by interpolation.
        self.assertNotIn("${{ inputs.path }}", stage_script())


class StageScriptTests(unittest.TestCase):
    def stage(self, files: dict[str, str], path: str = "docs/codewiki",
              links: dict[str, str] | None = None) -> tuple[int, str, Path, Path]:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)], check=False)
        repo, runner_temp = tmp / "repo", tmp / "runner"
        repo.mkdir()
        runner_temp.mkdir()
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for rel, dest in (links or {}).items():
            (repo / rel).symlink_to(dest)
        output = tmp / "github-output"
        result = subprocess.run(
            ["bash", "-c", stage_script()],
            cwd=repo,
            env={**os.environ, "CODEWIKI_PATH": path, "RUNNER_TEMP": str(runner_temp),
                 "GITHUB_OUTPUT": str(output)},
            capture_output=True, text=True, check=False,
        )
        out = output.read_text(encoding="utf-8") if output.exists() else ""
        return result.returncode, result.stderr + out, runner_temp / "codewiki-pages", output

    def test_stages_site_without_temp_state(self) -> None:
        code, out, stage, _ = self.stage({
            "docs/codewiki/index.html": "<html></html>",
            "docs/codewiki/overview.md": "# Overview\n",
            "docs/codewiki/Core/sub.md": "# Sub\n",
            "docs/codewiki/metadata.json": "{}",
            "docs/codewiki/temp/dependency_graphs/graph.json": "{}",
            "docs/codewiki/temp/artifact_index.json": "{}",
            "src/app.py": "secret = 1\n",
        })
        self.assertEqual(code, 0, out)
        self.assertIn(f"dir={stage}\n", out)
        staged = sorted(str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file())
        self.assertEqual(staged, ["Core/sub.md", "index.html", "metadata.json", "overview.md"])

    def test_only_top_level_temp_is_excluded(self) -> None:
        code, out, stage, _ = self.stage({
            "docs/codewiki/index.html": "x",
            "docs/codewiki/Temp.md": "x",
            "docs/codewiki/Core/temp/page.md": "x",
        })
        self.assertEqual(code, 0, out)
        self.assertTrue((stage / "Temp.md").is_file())
        self.assertTrue((stage / "Core/temp/page.md").is_file())

    def test_missing_viewer_fails_clearly(self) -> None:
        code, out, _, output = self.stage({"docs/codewiki/overview.md": "# Overview\n"})
        self.assertEqual(code, 1)
        self.assertIn("docs/codewiki/index.html' is missing", out)
        self.assertIn("--github-pages", out)
        self.assertFalse(output.exists())

    def test_missing_directory_fails_clearly(self) -> None:
        code, out, _, _ = self.stage({"README.md": "x"})
        self.assertEqual(code, 1)
        self.assertIn("'docs/codewiki' does not exist", out)

    def test_custom_path_and_trailing_slash(self) -> None:
        code, out, stage, _ = self.stage({"site/wiki/index.html": "x"}, path="site/wiki/")
        self.assertEqual(code, 0, out)
        self.assertTrue((stage / "index.html").is_file())

    def test_rejects_paths_outside_the_repository(self) -> None:
        for path in ("/etc", "..", "../x", "docs/../..", "docs/../x", ""):
            with self.subTest(path=path):
                code, out, _, _ = self.stage({"docs/codewiki/index.html": "x"}, path=path)
                self.assertEqual(code, 1)
                self.assertIn("repository-relative directory", out)

    def test_rejects_symlinks_that_would_be_dereferenced(self) -> None:
        code, out, _, output = self.stage(
            {"docs/codewiki/index.html": "x", ".env": "TOKEN=x"},
            links={"docs/codewiki/leak": "../../.env"},
        )
        self.assertEqual(code, 1)
        self.assertIn("symlinks are not publishable", out)
        self.assertIn("docs/codewiki/leak", out)
        self.assertFalse(output.exists())

    def test_symlink_inside_temp_is_dropped_with_temp(self) -> None:
        code, out, _, _ = self.stage(
            {"docs/codewiki/index.html": "x", "docs/codewiki/temp/a.json": "{}"},
            links={"docs/codewiki/temp/link": "a.json"},
        )
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
