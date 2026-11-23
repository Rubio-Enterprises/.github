"""Contracts for the consumer release workflow and its PR discovery boundary."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/release-please.yml").read_text()
SMALL = "${{ fromJSON(vars.RUNNERS || '{}').small || fromJSON('[\"ubuntu-slim\"]') }}"
DOCKER = (
    "${{ fromJSON(vars.RUNNERS || '{}').docker || fromJSON('[\"ubuntu-latest\"]') }}"
)


def job(name):
    return re.search(rf"(?ms)^  {name}:\n.*?(?=^  [\w-]+:|\Z)", TEXT)[0]


def lookup_script():
    block = (
        job("release").split("      - id: pr\n", 1)[1].split("        run: |\n", 1)[1]
    )
    return (
        "\n".join(
            line[10:] for line in block.splitlines() if line.startswith("          ")
        )
        + "\n"
    )


class ReleaseWorkflowTests(unittest.TestCase):
    def test_three_jobs_route_only_lock_to_docker(self):
        self.assertEqual(
            re.findall(r"^  ([\w-]+):$", TEXT.split("jobs:\n")[1], re.M),
            ["release", "lock", "arm"],
        )
        for name, route in [("release", SMALL), ("lock", DOCKER), ("arm", SMALL)]:
            self.assertIn(f"runs-on: {route}", job(name))
        self.assertNotIn("actions/checkout@", job("release"))
        self.assertIn("needs.release.outputs.has-lock == 'true'", job("lock"))
        self.assertIn("needs.release.outputs.number != ''", job("lock"))
        self.assertIn("needs: [release, lock]", job("arm"))
        self.assertIn("!cancelled()", job("arm"))
        self.assertIn("needs.release.result == 'success'", job("arm"))
        self.assertIn("needs.release.outputs.number != ''", job("arm"))
        self.assertIn(
            "needs.lock.result == 'success' || needs.lock.result == 'skipped'",
            job("arm"),
        )

    def test_outputs_permissions_and_pins_preserved(self):
        for output in ["release_created", "tag_name", "version", "prs_created"]:
            self.assertIn(
                f'{output}: {{ value: "${{{{ jobs.release.outputs.{output} }}}}" }}',
                TEXT,
            )
            self.assertIn(
                f"{output}: ${{{{ steps.release.outputs.{output} }}}}", job("release")
            )
        self.assertIn("permissions:\n  contents: read\n  packages: read", TEXT)
        self.assertIn(
            "googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7",
            TEXT,
        )
        for name in ["release", "lock", "arm"]:
            self.assertIn("- id: app-token", job(name))
        self.assertIn("permission-contents: write", job("release"))
        self.assertIn("permission-pull-requests: write", job("release"))
        self.assertIn("permission-contents: write", job("lock"))
        self.assertNotIn("permission-pull-requests:", job("lock"))
        self.assertIn("permission-contents: write", job("arm"))
        self.assertIn("permission-pull-requests: write", job("arm"))
        self.assertIn("just build-dev\n          just exec uv lock", job("lock"))
        self.assertIn("persist-credentials: false", job("lock"))
        self.assertIn(
            'gh pr merge "$PR" -R "$GITHUB_REPOSITORY" "$method" --auto', job("arm")
        )

    def lookup(self, action_pr="", responses=None, status="200", list_exit=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "responses").write_text(json.dumps(responses or []))
            (root / "gh").write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE'])
with (root / 'calls').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1:3] == ['pr', 'list']:
    if os.environ['LIST_EXIT'] != '0':
        print('PR lookup failed', file=sys.stderr)
        sys.exit(int(os.environ['LIST_EXIT']))
    responses = json.loads((root / 'responses').read_text())
    print(json.dumps(responses.pop(0) if responses else []))
    (root / 'responses').write_text(json.dumps(responses))
else:
    status = os.environ['STATUS']
    print('HTTP/2.0 ' + status + ' Status\\n\\n{}')
    sys.exit(0 if status == '200' else 1)
""")
            (root / "sleep").write_text('#!/bin/sh\necho sleep >>"$FIXTURE/calls"\n')
            for name in ["gh", "sleep"]:
                (root / name).chmod(0o755)
            script = root / "lookup.sh"
            script.write_text(lookup_script())
            result = subprocess.run(
                ["bash", "-e", str(script)],
                env={
                    **os.environ,
                    "PATH": f"{tmp}:{os.environ['PATH']}",
                    "FIXTURE": tmp,
                    "ACTION_PR": action_pr,
                    "STATUS": status,
                    "LIST_EXIT": str(list_exit),
                    "GITHUB_REPOSITORY": "owner/repo",
                    "GITHUB_OUTPUT": str(root / "output"),
                },
                capture_output=True,
                text=True,
            )
            output = (root / "output").read_text() if (root / "output").exists() else ""
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            return result.returncode, output, calls, result.stderr

    def test_action_pr_is_preferred_and_head_ref_is_used_for_contents(self):
        code, out, calls, err = self.lookup(
            json.dumps(
                {"number": 22, "headBranchName": "release-please--branches--main"}
            )
        )
        self.assertEqual(code, 0, err)
        self.assertIn(
            "number=22\nbranch=release-please--branches--main\nhas-lock=true\n", out
        )
        self.assertNotIn('"list"', calls)
        self.assertIn("repos/owner/repo/contents/uv.lock", calls)
        self.assertIn("ref=release-please--branches--main", calls)
        self.assertIn("ACTION_PR: ${{ steps.release.outputs.pr }}", job("release"))

    def test_fallback_retries_and_missing_lock_is_not_failure(self):
        code, out, calls, err = self.lookup(
            responses=[[], [], [{"number": 23, "headRefName": "release/branch"}]],
            status="404",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("number=23\nbranch=release/branch\nhas-lock=false\n", out)
        self.assertEqual(calls.count('"list"'), 3)
        self.assertEqual(calls.count("sleep"), 2)

    def test_no_pr_is_bounded_and_does_not_probe_contents(self):
        code, out, calls, err = self.lookup()
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        self.assertEqual(calls.count('"list"'), 3)
        self.assertNotIn("contents/uv.lock", calls)

    def test_existing_pr_fallback_does_not_sleep(self):
        code, out, calls, err = self.lookup(
            responses=[[{"number": 24, "headRefName": "release/branch"}]]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("number=24\n", out)
        self.assertIn("has-lock=true\n", out)
        self.assertEqual(calls.count('"list"'), 1)
        self.assertNotIn("sleep", calls)

    def test_malformed_action_output_fails_closed(self):
        code, out, calls, err = self.lookup(action_pr="{malformed")
        self.assertNotEqual(code, 0)
        self.assertTrue(err)
        self.assertEqual(out, "")
        self.assertEqual(calls, "")

    def test_null_action_output_falls_through_to_label_lookup(self):
        code, out, calls, err = self.lookup(
            action_pr="null",
            responses=[[{"number": 25, "headRefName": "release/branch"}]],
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "number=25\nbranch=release/branch\nhas-lock=true\n")
        self.assertEqual(calls.count('"list"'), 1)
        self.assertIn("contents/uv.lock", calls)
        self.assertNotIn("sleep", calls)

    def test_failing_label_lookup_fails_without_retry(self):
        code, out, calls, err = self.lookup(list_exit=17)
        self.assertEqual(code, 17)
        self.assertIn("PR lookup failed", err)
        self.assertEqual(out, "")
        self.assertEqual(calls.count('"list"'), 1)
        self.assertNotIn("sleep", calls)
        self.assertNotIn("contents/uv.lock", calls)

    def test_contents_errors_fail_closed(self):
        code, _, _, _ = self.lookup(
            json.dumps({"number": 22, "headBranchName": "release"}), status="403"
        )
        self.assertNotEqual(code, 0)
