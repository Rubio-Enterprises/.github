"""Behavioral tests for the gates/wf-v1 publisher."""

from __future__ import annotations

import importlib.util
import inspect
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "plumbing_ref.py"
SPEC = importlib.util.spec_from_file_location("plumbing_ref", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"could not load {MODULE_PATH}")
plumbing_ref = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plumbing_ref)

GATE_WORKFLOWS = {
    "gate-audit": ".github/workflows/audit.yml",
    "gate-lint-format": ".github/workflows/lint-format.yml",
    "gate-secret-scan": ".github/workflows/secret-scan.yml",
    "gate-pr-title": ".github/workflows/pr-title.yml",
    "gate-typescript": ".github/workflows/typecheck-ts.yml",
    "gate-python-tests": ".github/workflows/test-py.yml",
    "gate-rust-tests": ".github/workflows/rust-test.yml",
}
REQUEST_PATH = Path(".github/plumbing-ref/publication-request.json")
MANIFEST_PATH = Path(".github/plumbing-ref/gate-family-workflows.json")
LIVE_REF = "refs/tags/gates/wf-v1"


def run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        text=True,
        capture_output=True,
    )


class GitFixture:
    def __init__(self, root: Path) -> None:
        self.remote = root / "remote.git"
        self.repo = root / "repo"
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)],
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True,
            text=True,
            capture_output=True,
        )
        run_git(self.repo, "config", "user.name", "Publisher Test")
        run_git(self.repo, "config", "user.email", "publisher@example.invalid")
        run_git(self.repo, "remote", "add", "origin", str(self.remote))

    def commit(self, files: Mapping[str, str | None], message: str) -> str:
        for relative_path, content in files.items():
            path = self.repo / relative_path
            if content is None:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        run_git(self.repo, "add", "--all")
        run_git(self.repo, "commit", "-m", message)
        return run_git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def create_floor(self) -> str:
        files = {path: f"name: {family}\n" for family, path in GATE_WORKFLOWS.items()}
        files[str(MANIFEST_PATH)] = json.dumps(GATE_WORKFLOWS, indent=2) + "\n"
        return self.commit(files, "add gate workflow floor")

    def write_request(self, expected: str, target: str, reason: str = "Publish candidate") -> str:
        request = {
            "expected_current_sha": expected,
            "target_sha": target,
            "reason": reason,
            "references": ["https://github.com/example/repo/pull/1"],
        }
        files = {str(REQUEST_PATH): json.dumps(request, indent=2) + "\n"}
        validator_path = self.repo / ".github" / "scripts" / "plumbing_ref.py"
        if not validator_path.exists():
            files[".github/scripts/plumbing_ref.py"] = "# protected validator\n"
        return self.commit(files, "update publication request")

    def push_main(self) -> None:
        run_git(self.repo, "push", "--set-upstream", "origin", "main")

    def set_live_ref(self, sha: str) -> None:
        run_git(self.repo, "push", "origin", f"{sha}:{LIVE_REF}")

    def live_sha(self) -> str:
        output = run_git(self.repo, "ls-remote", "--refs", "origin", LIVE_REF).stdout
        return output.split()[0]


class PublicationRequestTests(unittest.TestCase):
    def test_checked_in_request_contract_remains_publishable(self) -> None:
        request = plumbing_ref.load_request(ROOT / REQUEST_PATH)
        manifest = plumbing_ref.load_manifest(ROOT / MANIFEST_PATH)
        schema = json.loads(
            (ROOT / ".github" / "plumbing-ref" / "publication-request.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            set(request),
            {"expected_current_sha", "target_sha", "reason", "references"},
        )
        self.assertRegex(request["expected_current_sha"], r"^[0-9a-f]{40}$")
        self.assertRegex(request["target_sha"], r"^[0-9a-f]{40}$")
        self.assertTrue(request["reason"].strip())
        self.assertEqual(manifest, GATE_WORKFLOWS)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            {"expected_current_sha", "target_sha", "reason"},
        )

    def test_request_rejects_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "expected_current_sha": "1" * 40,
                        "target_sha": "2" * 40,
                        "reason": "Publish a reviewed candidate.",
                        "references": ["https://github.com/example/repo/pull/1"],
                        "unexpected": True,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(plumbing_ref.PolicyError):
                plumbing_ref.load_request(request_path)

    def test_request_and_manifest_reject_duplicate_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            request_path.write_text(
                '{"expected_current_sha":"1111111111111111111111111111111111111111",'
                '"target_sha":"2222222222222222222222222222222222222222",'
                '"target_sha":"3333333333333333333333333333333333333333",'
                '"reason":"ambiguous target"}',
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                '{"gate-audit":".github/workflows/audit.yml",'
                '"gate-audit":".github/workflows/other.yml"}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "duplicate field"):
                plumbing_ref.load_request(request_path)
            with self.assertRaisesRegex(plumbing_ref.PolicyError, "duplicate field"):
                plumbing_ref.load_manifest(manifest_path)

    def test_request_and_manifest_reject_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            manifest_path = root / "manifest.json"
            request_path.write_bytes(b"\xff")
            manifest_path.write_bytes(b"\xff")

            with self.assertRaises(plumbing_ref.PolicyError):
                plumbing_ref.load_request(request_path)
            with self.assertRaises(plumbing_ref.PolicyError):
                plumbing_ref.load_manifest(manifest_path)

    def test_request_rejects_malformed_policy_fields(self) -> None:
        valid = {
            "expected_current_sha": "1" * 40,
            "target_sha": "2" * 40,
            "reason": "Publish a reviewed candidate.",
            "references": ["https://github.com/example/repo/pull/1"],
        }
        invalid_documents = (
            {**valid, "expected_current_sha": "A" * 40},
            {**valid, "expected_current_sha": "0" * 40},
            {**valid, "target_sha": "2" * 39},
            {**valid, "reason": " \n\t"},
            {**valid, "reason": "invalid surrogate: \ud800"},
            {**valid, "reason": "invalid control: \x00"},
            {**valid, "references": ["http://github.com/example/repo/pull/1"]},
            {**valid, "references": ["https://github.com/example/repo/has space"]},
            {**valid, "references": ["https://github.com/example/repo/has\nnewline"]},
            {**valid, "references": ["https://[invalid-host"]},
            {**valid, "references": ["https://github.com:not-a-port/reference"]},
            {**valid, "references": ["/relative/reference"]},
            {**valid, "references": "https://github.com/example/repo/pull/1"},
        )
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            for document in invalid_documents:
                with self.subTest(document=document):
                    request_path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(plumbing_ref.PolicyError):
                        plumbing_ref.load_request(request_path)

    def test_summary_escapes_untrusted_request_text(self) -> None:
        summary = plumbing_ref.render_summary(
            {
                "result": "validated",
                "direction": "forward",
                "mutation_attempted": False,
                "expected_current_sha": "1" * 40,
                "observed_current_sha": "1" * 40,
                "target_sha": "2" * 40,
                "reason": "<script>alert('x')</script>\n| forged | row |",
                "references": ["https://example.com/?value=<unsafe>"],
                "changed_files": [str(REQUEST_PATH)],
                "required_workflows": sorted(GATE_WORKFLOWS.values()),
                "affected_workflows": [GATE_WORKFLOWS["gate-rust-tests"]],
                "final_observed_sha": "1" * 40,
            }
        )

        self.assertNotIn("<script>", summary)
        self.assertIn("&lt;script&gt;", summary)
        self.assertNotIn("| forged | row |", summary)


class CliTests(unittest.TestCase):
    def test_forward_cli_validates_and_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)
            summary_path = Path(directory) / "summary.md"

            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "forward",
                    "--repository",
                    str(fixture.repo),
                    "--remote",
                    "origin",
                    "--request",
                    str(fixture.repo / REQUEST_PATH),
                    "--manifest",
                    str(fixture.repo / MANIFEST_PATH),
                    "--before-sha",
                    before,
                    "--after-sha",
                    after,
                    "--mode",
                    "validate",
                    "--run-attempt",
                    "1",
                    "--summary-file",
                    str(summary_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["result"], "validated")
            self.assertIn("Plumbing Ref operation", summary_path.read_text(encoding="utf-8"))

    def test_forward_cli_failure_writes_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            request = {
                "expected_current_sha": expected,
                "target_sha": expected,
                "reason": "Invalid non-bootstrap no-op.",
            }
            after = fixture.commit(
                {str(REQUEST_PATH): json.dumps(request, indent=2) + "\n"},
                "request invalid no-op",
            )
            fixture.push_main()
            fixture.set_live_ref(expected)
            summary_path = Path(directory) / "failure-summary.md"

            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "forward",
                    "--repository",
                    str(fixture.repo),
                    "--remote",
                    "origin",
                    "--request",
                    str(fixture.repo / REQUEST_PATH),
                    "--manifest",
                    str(fixture.repo / MANIFEST_PATH),
                    "--before-sha",
                    before,
                    "--after-sha",
                    after,
                    "--mode",
                    "validate",
                    "--run-attempt",
                    "1",
                    "--summary-file",
                    str(summary_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("failed", summary_path.read_text(encoding="utf-8"))

    def test_missing_required_workflow_failure_reports_affected_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            missing_path = GATE_WORKFLOWS["gate-python-tests"]
            target = fixture.commit({missing_path: None}, "remove required Python gate")
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)
            summary_path = Path(directory) / "missing-workflow-summary.md"

            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "forward",
                    "--repository",
                    str(fixture.repo),
                    "--remote",
                    "origin",
                    "--request",
                    str(fixture.repo / REQUEST_PATH),
                    "--manifest",
                    str(fixture.repo / MANIFEST_PATH),
                    "--before-sha",
                    before,
                    "--after-sha",
                    after,
                    "--mode",
                    "validate",
                    "--run-attempt",
                    "1",
                    "--summary-file",
                    str(summary_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            summary = summary_path.read_text(encoding="utf-8")
            affected_section = summary.split("### Affected Gate Family workflow files", 1)[1]
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(missing_path, affected_section)
            self.assertIn("required Gate Family workflow is not a blob", summary)


class ForwardPublicationTests(unittest.TestCase):
    def test_forward_publication_updates_exact_live_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)

            report = plumbing_ref.execute_forward(
                repository=fixture.repo,
                remote="origin",
                request_path=fixture.repo / REQUEST_PATH,
                manifest_path=fixture.repo / MANIFEST_PATH,
                before_sha=before,
                after_sha=after,
                mode="publish",
                run_attempt=1,
            )

            self.assertEqual(report["result"], "published")
            self.assertTrue(report["mutation_attempted"])
            self.assertEqual(report["final_observed_sha"], target)
            self.assertEqual(fixture.live_sha(), target)
            self.assertEqual(
                report["affected_workflows"],
                [GATE_WORKFLOWS["gate-rust-tests"]],
            )

    def test_bootstrap_allows_mixed_change_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            live = fixture.create_floor()
            request = {
                "expected_current_sha": live,
                "target_sha": live,
                "reason": "Bootstrap the publisher without moving the ref.",
            }
            after = fixture.commit(
                {
                    str(REQUEST_PATH): json.dumps(request, indent=2) + "\n",
                    ".github/scripts/plumbing_ref.py": "# bootstrap implementation\n",
                },
                "bootstrap publisher",
            )
            fixture.push_main()
            fixture.set_live_ref(live)

            report = plumbing_ref.execute_forward(
                repository=fixture.repo,
                remote="origin",
                request_path=fixture.repo / REQUEST_PATH,
                manifest_path=fixture.repo / MANIFEST_PATH,
                before_sha=live,
                after_sha=after,
                mode="publish",
                run_attempt=1,
            )

            self.assertEqual(report["result"], "bootstrap-noop")
            self.assertFalse(report["mutation_attempted"])
            self.assertEqual(fixture.live_sha(), live)

    def test_readding_deleted_request_cannot_reenter_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            live = fixture.create_floor()
            fixture.commit(
                {
                    str(REQUEST_PATH): json.dumps(
                        {
                            "expected_current_sha": live,
                            "target_sha": live,
                            "reason": "Bootstrap publisher",
                        },
                        indent=2,
                    )
                    + "\n",
                    ".github/scripts/plumbing_ref.py": "# protected validator\n",
                },
                "bootstrap publisher",
            )
            before = fixture.commit({str(REQUEST_PATH): None}, "delete publication request")
            after = fixture.commit(
                {
                    str(REQUEST_PATH): json.dumps(
                        {
                            "expected_current_sha": live,
                            "target_sha": live,
                            "reason": "Attempt bootstrap reentry",
                        },
                        indent=2,
                    )
                    + "\n",
                    "README.md": "unrelated change\n",
                },
                "readd request with unrelated change",
            )
            fixture.push_main()
            fixture.set_live_ref(live)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "incomplete publisher state"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_readding_all_deleted_control_files_cannot_reenter_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            live = fixture.create_floor()
            fixture.write_request(live, live, "Bootstrap publisher")
            before = fixture.commit(
                {
                    str(REQUEST_PATH): None,
                    ".github/scripts/plumbing_ref.py": None,
                },
                "delete publisher control files",
            )
            after = fixture.commit(
                {
                    str(REQUEST_PATH): json.dumps(
                        {
                            "expected_current_sha": live,
                            "target_sha": live,
                            "reason": "Attempt bootstrap reentry",
                        },
                        indent=2,
                    )
                    + "\n",
                    ".github/scripts/plumbing_ref.py": "# replacement validator\n",
                    "README.md": "unrelated change\n",
                },
                "readd all publisher control files",
            )
            fixture.push_main()
            fixture.set_live_ref(live)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "bootstrap already completed"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_forward_rejects_stale_expected_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-audit"]: "name: candidate audit gate\n"},
                "change audit gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(target)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "live ref mismatch"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_forward_rejects_target_not_reachable_from_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-audit"]: "name: off-main audit gate\n"},
                "create off-main candidate",
            )
            run_git(fixture.repo, "branch", "candidate", target)
            run_git(fixture.repo, "reset", "--hard", expected)
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "reachable"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_forward_rejects_missing_gate_family_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            missing_path = GATE_WORKFLOWS["gate-python-tests"]
            target = fixture.commit({missing_path: None}, "remove Python gate")
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)

            with self.assertRaises(plumbing_ref.PolicyError):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_forward_rejects_annotated_live_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            run_git(
                fixture.repo,
                "tag",
                "--annotate",
                "gates/wf-v1",
                expected,
                "--message",
                "annotated live tag",
            )
            run_git(fixture.repo, "push", "origin", LIVE_REF)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "lightweight"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_publish_rerun_is_noop_only_when_target_is_already_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)

            plumbing_ref.execute_forward(
                repository=fixture.repo,
                remote="origin",
                request_path=fixture.repo / REQUEST_PATH,
                manifest_path=fixture.repo / MANIFEST_PATH,
                before_sha=before,
                after_sha=after,
                mode="publish",
                run_attempt=1,
            )
            report = plumbing_ref.execute_forward(
                repository=fixture.repo,
                remote="origin",
                request_path=fixture.repo / REQUEST_PATH,
                manifest_path=fixture.repo / MANIFEST_PATH,
                before_sha=before,
                after_sha=after,
                mode="publish",
                run_attempt=2,
            )

            self.assertEqual(report["result"], "already-live-noop")
            self.assertFalse(report["mutation_attempted"])
            self.assertEqual(fixture.live_sha(), target)

    def test_publish_rerun_rejects_when_target_is_not_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "reruns never mutate"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="publish",
                    run_attempt=2,
                )
            self.assertEqual(fixture.live_sha(), expected)

    def test_post_bootstrap_rejects_mixed_changed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-audit"]: "name: candidate audit gate\n"},
                "change audit gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            request = {
                "expected_current_sha": expected,
                "target_sha": target,
                "reason": "Publish candidate",
            }
            after = fixture.commit(
                {
                    str(REQUEST_PATH): json.dumps(request, indent=2) + "\n",
                    "README.md": "unrelated change\n",
                },
                "mix publication request with unrelated change",
            )
            fixture.push_main()
            fixture.set_live_ref(expected)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "may change only"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="validate",
                    run_attempt=1,
                )

    def test_pull_request_scope_ignores_changes_added_to_base_after_branching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-audit"]: "name: candidate audit gate\n"},
                "change audit gate",
            )
            fixture.write_request(expected, expected, "Bootstrap publisher")
            run_git(fixture.repo, "switch", "-c", "publication-request")
            request_head = fixture.write_request(expected, target)
            run_git(fixture.repo, "switch", "main")
            advanced_base = fixture.commit(
                {"README.md": "independent protected-base change\n"},
                "advance protected base",
            )
            run_git(fixture.repo, "push", "origin", "main", "publication-request")
            fixture.set_live_ref(expected)
            run_git(fixture.repo, "switch", "publication-request")

            report = plumbing_ref.execute_forward(
                repository=fixture.repo,
                remote="origin",
                request_path=fixture.repo / REQUEST_PATH,
                manifest_path=fixture.repo / MANIFEST_PATH,
                before_sha=advanced_base,
                after_sha=request_head,
                mode="validate",
                run_attempt=1,
                change_scope="merge-base",
            )

            self.assertEqual(report["changed_files"], [str(REQUEST_PATH)])
            self.assertEqual(report["result"], "validated")


class CasCompetitionTests(unittest.TestCase):
    def test_competing_exact_lease_publications_have_one_visible_loser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = GitFixture(root)
            expected = fixture.create_floor()
            target_a = fixture.commit(
                {GATE_WORKFLOWS["gate-audit"]: "name: candidate audit gate\n"},
                "change audit gate",
            )
            target_b = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            run_git(fixture.repo, "switch", "-c", "request-a")
            after_a = fixture.write_request(expected, target_a, "Publish candidate A")
            run_git(fixture.repo, "switch", "main")
            run_git(fixture.repo, "switch", "-c", "request-b")
            after_b = fixture.write_request(expected, target_b, "Publish candidate B")
            run_git(fixture.repo, "push", "origin", "main", "request-a", "request-b")
            fixture.set_live_ref(expected)

            operation_repos: list[Path] = []
            for branch in ("request-a", "request-b"):
                operation_repo = root / branch
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--branch",
                        branch,
                        str(fixture.remote),
                        str(operation_repo),
                    ],
                    check=True,
                    text=True,
                    capture_output=True,
                )
                operation_repos.append(operation_repo)

            def publish(repository: Path, after_sha: str) -> tuple[str, str]:
                try:
                    report = plumbing_ref.execute_forward(
                        repository=repository,
                        remote="origin",
                        request_path=repository / REQUEST_PATH,
                        manifest_path=repository / MANIFEST_PATH,
                        before_sha=before,
                        after_sha=after_sha,
                        mode="publish",
                        run_attempt=1,
                    )
                except plumbing_ref.PolicyError as error:
                    return ("error", str(error))
                return ("success", report["target_sha"])

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda args: publish(*args),
                        zip(operation_repos, (after_a, after_b), strict=True),
                    )
                )

            self.assertEqual([result[0] for result in results].count("success"), 1)
            self.assertEqual([result[0] for result in results].count("error"), 1)
            self.assertIn(fixture.live_sha(), {target_a, target_b})


class CasVerificationTests(unittest.TestCase):
    def test_push_porcelain_requires_an_actual_forced_update(self) -> None:
        self.assertTrue(plumbing_ref.push_reports_exact_update("+\told:new\tforced update\n"))
        self.assertFalse(plumbing_ref.push_reports_exact_update("=\told:new\t[up to date]\n"))
        self.assertFalse(plumbing_ref.push_reports_exact_update("Everything up-to-date\n"))

    def test_post_write_remote_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)
            hook = fixture.remote / "hooks" / "post-receive"
            hook.write_text(
                "#!/bin/sh\n"
                "while read -r old new ref; do\n"
                f'  [ "$ref" != "{LIVE_REF}" ] || '
                f"git update-ref {LIVE_REF} {expected}\n"
                "done\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "mismatch"):
                plumbing_ref.execute_forward(
                    repository=fixture.repo,
                    remote="origin",
                    request_path=fixture.repo / REQUEST_PATH,
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    before_sha=before,
                    after_sha=after,
                    mode="publish",
                    run_attempt=1,
                )
            self.assertEqual(fixture.live_sha(), expected)

    def test_rejected_push_remains_failure_when_target_becomes_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)
            hook = fixture.remote / "hooks" / "pre-receive"
            hook.write_text(
                "#!/bin/sh\necho 'rejecting simulated losing push' >&2\nexit 1\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)

            with mock.patch.object(
                plumbing_ref,
                "_read_live_ref",
                side_effect=(expected, target),
            ):
                with self.assertRaisesRegex(plumbing_ref.PolicyError, "exact-lease update failed"):
                    plumbing_ref.execute_forward(
                        repository=fixture.repo,
                        remote="origin",
                        request_path=fixture.repo / REQUEST_PATH,
                        manifest_path=fixture.repo / MANIFEST_PATH,
                        before_sha=before,
                        after_sha=after,
                        mode="publish",
                        run_attempt=1,
                    )
            self.assertEqual(fixture.live_sha(), expected)

    def test_exact_lease_failure_summary_keeps_operation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)
            hook = fixture.remote / "hooks" / "pre-receive"
            hook.write_text(
                "#!/bin/sh\necho 'rejecting simulated losing push' >&2\nexit 1\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)
            summary_path = Path(directory) / "lease-failure-summary.md"

            completed = subprocess.run(
                [
                    "python3",
                    str(MODULE_PATH),
                    "forward",
                    "--repository",
                    str(fixture.repo),
                    "--remote",
                    "origin",
                    "--request",
                    str(fixture.repo / REQUEST_PATH),
                    "--manifest",
                    str(fixture.repo / MANIFEST_PATH),
                    "--before-sha",
                    before,
                    "--after-sha",
                    after,
                    "--mode",
                    "publish",
                    "--run-attempt",
                    "1",
                    "--summary-file",
                    str(summary_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            summary = summary_path.read_text(encoding="utf-8")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Mutation attempted | yes", summary)
            affected_section = summary.split("### Affected Gate Family workflow files", 1)[1]
            self.assertIn(GATE_WORKFLOWS["gate-rust-tests"], affected_section)
            self.assertIn("exact-lease update failed", summary)

    def test_failed_post_write_reread_keeps_mutation_and_affected_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: candidate rust gate\n"},
                "change rust gate",
            )
            before = fixture.write_request(expected, expected, "Bootstrap publisher")
            after = fixture.write_request(expected, target)
            fixture.push_main()
            fixture.set_live_ref(expected)
            summary_path = Path(directory) / "reread-failure-summary.md"
            arguments = [
                "forward",
                "--repository",
                str(fixture.repo),
                "--remote",
                "origin",
                "--request",
                str(fixture.repo / REQUEST_PATH),
                "--manifest",
                str(fixture.repo / MANIFEST_PATH),
                "--before-sha",
                before,
                "--after-sha",
                after,
                "--mode",
                "publish",
                "--run-attempt",
                "1",
                "--summary-file",
                str(summary_path),
            ]

            with (
                mock.patch.object(
                    plumbing_ref,
                    "_read_live_ref",
                    side_effect=(expected, plumbing_ref.PolicyError("transient remote read error")),
                ),
                mock.patch.object(sys, "stderr", io.StringIO()),
            ):
                result = plumbing_ref.main(arguments)

            summary = summary_path.read_text(encoding="utf-8")
            self.assertEqual(result, 1)
            self.assertEqual(fixture.live_sha(), target)
            self.assertIn("Mutation attempted | yes", summary)
            affected_section = summary.split("### Affected Gate Family workflow files", 1)[1]
            self.assertIn(GATE_WORKFLOWS["gate-rust-tests"], affected_section)
            self.assertIn("post-write verification failed", summary)


class RollbackPublicationTests(unittest.TestCase):
    def test_rollback_updates_exact_live_ref_to_older_main_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            target = fixture.create_floor()
            expected = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: broken rust gate\n"},
                "publish broken rust gate",
            )
            fixture.push_main()
            fixture.set_live_ref(expected)

            report = plumbing_ref.execute_rollback(
                repository=fixture.repo,
                remote="origin",
                manifest_path=fixture.repo / MANIFEST_PATH,
                expected_current_sha=expected,
                target_sha=target,
                reason="Roll back the broken Rust gate.",
                references=["https://github.com/example/repo/issues/1"],
                mode="publish",
                run_attempt=1,
            )

            self.assertEqual(report["result"], "published")
            self.assertEqual(report["direction"], "rollback")
            self.assertTrue(report["mutation_attempted"])
            self.assertEqual(fixture.live_sha(), target)

    def test_rollback_rerun_is_noop_only_when_target_is_already_live(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            target = fixture.create_floor()
            expected = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: broken rust gate\n"},
                "publish broken rust gate",
            )
            fixture.push_main()
            fixture.set_live_ref(expected)

            plumbing_ref.execute_rollback(
                repository=fixture.repo,
                remote="origin",
                manifest_path=fixture.repo / MANIFEST_PATH,
                expected_current_sha=expected,
                target_sha=target,
                reason="Roll back the broken Rust gate.",
                references=[],
                mode="publish",
                run_attempt=1,
            )
            validation = plumbing_ref.execute_rollback(
                repository=fixture.repo,
                remote="origin",
                manifest_path=fixture.repo / MANIFEST_PATH,
                expected_current_sha=expected,
                target_sha=target,
                reason="Roll back the broken Rust gate.",
                references=[],
                mode="validate",
                run_attempt=2,
            )
            report = plumbing_ref.execute_rollback(
                repository=fixture.repo,
                remote="origin",
                manifest_path=fixture.repo / MANIFEST_PATH,
                expected_current_sha=expected,
                target_sha=target,
                reason="Roll back the broken Rust gate.",
                references=[],
                mode="publish",
                run_attempt=2,
            )

            self.assertEqual(validation["result"], "already-live-noop")
            self.assertFalse(validation["mutation_attempted"])
            self.assertEqual(report["result"], "already-live-noop")
            self.assertFalse(report["mutation_attempted"])

    def test_rollback_rejects_forward_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = GitFixture(Path(directory))
            expected = fixture.create_floor()
            target = fixture.commit(
                {GATE_WORKFLOWS["gate-rust-tests"]: "name: newer rust gate\n"},
                "create newer rust gate",
            )
            fixture.push_main()
            fixture.set_live_ref(expected)

            with self.assertRaisesRegex(plumbing_ref.PolicyError, "strict ancestor"):
                plumbing_ref.execute_rollback(
                    repository=fixture.repo,
                    remote="origin",
                    manifest_path=fixture.repo / MANIFEST_PATH,
                    expected_current_sha=expected,
                    target_sha=target,
                    reason="This is not a rollback.",
                    references=[],
                    mode="validate",
                    run_attempt=1,
                )


class WorkflowContractTests(unittest.TestCase):
    def test_normal_publisher_uses_base_validator_and_narrow_permissions(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "plumbing-ref-publish.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("pull_request:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("permissions: {}", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertGreaterEqual(workflow.count(".base-validator"), 4)
        self.assertGreaterEqual(workflow.count("git cat-file -e"), 2)
        self.assertGreaterEqual(workflow.count("git rev-list --max-count=1"), 2)
        self.assertIn("protected validator is missing", workflow)
        self.assertIn("publisher bootstrap already completed", workflow)
        self.assertIn("--change-scope merge-base", workflow)
        self.assertIn("--change-scope direct", workflow)
        self.assertNotIn("concurrency:", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("pull_request.head.ref", workflow)

    def test_rollback_is_validation_first_and_requires_explicit_write_mode(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "plumbing-ref-rollback.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("default: validate-only", workflow)
        self.assertIn("- validate-only", workflow)
        self.assertIn("- write", workflow)
        self.assertIn("if: inputs.write_mode == 'write'", workflow)
        self.assertIn("needs: [validate]", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertGreaterEqual(workflow.count("RUN_ATTEMPT: ${{ github.run_attempt }}"), 2)
        self.assertNotIn("--run-attempt 1", workflow)
        self.assertNotIn("concurrency:", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_exact_lease_is_the_only_ref_mutation_primitive(self) -> None:
        mutation_source = inspect.getsource(plumbing_ref._perform_cas)
        self.assertIn(
            'f"--force-with-lease={LIVE_REF}:{expected_sha}"',
            mutation_source,
        )
        self.assertNotIn('"--force"', mutation_source)
        self.assertNotIn("force=true", mutation_source)
        self.assertNotIn("update-ref", mutation_source)
        self.assertNotIn("refs/tags/gates/wf-v1^{}", mutation_source)


if __name__ == "__main__":
    unittest.main()
