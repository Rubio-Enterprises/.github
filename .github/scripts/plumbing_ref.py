#!/usr/bin/env python3
"""Validate and publish the gates/wf-v1 Plumbing Ref."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LIVE_REF = "refs/tags/gates/wf-v1"
REMOTE_MAIN_REF = "refs/remotes/plumbing-ref/main"
LOCAL_LIVE_REF = "refs/plumbing-ref/live"
VALIDATOR_PATH = ".github/scripts/plumbing_ref.py"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED_REQUEST_FIELDS = {
    "expected_current_sha",
    "target_sha",
    "reason",
}
OPTIONAL_REQUEST_FIELDS = {"references"}


class PolicyError(RuntimeError):
    """Raised when a publication or rollback request violates policy."""


class OperationError(PolicyError):
    """Raised with the partial report for a failed operation."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = {
            **report,
            "result": "failed",
            "error": message,
        }


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        _validate_text(key, "JSON field name")
        if key in document:
            raise PolicyError(f"JSON object contains duplicate field: {key}")
        document[key] = value
    return document


def _validate_text(value: str, field: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PolicyError(f"{field} must contain valid Unicode text") from error
    if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value):
        raise PolicyError(f"{field} must not contain control characters")
    return value


def _validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA_PATTERN.fullmatch(value) is None:
        raise PolicyError(f"{field} must be exactly 40 lowercase hexadecimal characters")
    if value == "0" * 40:
        raise PolicyError(f"{field} must not be the all-zero object ID")
    return value


def _validate_references(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PolicyError("references must be an array")
    references: list[str] = []
    for reference in value:
        if not isinstance(reference, str):
            raise PolicyError("each reference must be a string")
        _validate_text(reference, "each reference")
        try:
            parsed = urlparse(reference)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as error:
            raise PolicyError(
                "each reference must be an absolute https URL with a valid host and port"
            ) from error
        if (
            parsed.scheme != "https"
            or not hostname
            or any(character.isspace() for character in reference)
        ):
            raise PolicyError("each reference must be an absolute https URL with a host")
        references.append(reference)
    return references


def load_request(path: Path) -> dict[str, Any]:
    """Load and strictly validate one Publication Request document."""

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_fields,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"could not read Publication Request: {error}") from error

    if not isinstance(document, dict):
        raise PolicyError("Publication Request must be a JSON object")

    keys = set(document)
    missing = REQUIRED_REQUEST_FIELDS - keys
    extra = keys - REQUIRED_REQUEST_FIELDS - OPTIONAL_REQUEST_FIELDS
    if missing:
        raise PolicyError(f"Publication Request is missing: {', '.join(sorted(missing))}")
    if extra:
        raise PolicyError(f"Publication Request has unknown fields: {', '.join(sorted(extra))}")

    expected_current_sha = _validate_sha(document["expected_current_sha"], "expected_current_sha")
    target_sha = _validate_sha(document["target_sha"], "target_sha")
    reason = document["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyError("reason must contain at least one non-whitespace character")
    _validate_text(reason, "reason")

    return {
        "expected_current_sha": expected_current_sha,
        "target_sha": target_sha,
        "reason": reason,
        "references": _validate_references(document.get("references")),
    }


def load_manifest(path: Path) -> dict[str, str]:
    """Load the public Gate Family workflow manifest."""

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_fields,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"could not read Gate Family manifest: {error}") from error
    if not isinstance(document, dict) or not document:
        raise PolicyError("Gate Family manifest must be a nonempty JSON object")
    manifest: dict[str, str] = {}
    for family, workflow_path in document.items():
        _validate_text(family, "Gate Family name")
        if not family.startswith("gate-"):
            raise PolicyError("Gate Family manifest keys must start with gate-")
        if not isinstance(workflow_path, str):
            raise PolicyError(f"Gate Family {family} must map to a string path")
        _validate_text(workflow_path, f"Gate Family {family} workflow path")
        if not workflow_path.startswith(".github/workflows/") or not workflow_path.endswith(".yml"):
            raise PolicyError(f"Gate Family {family} must map to a .github/workflows/*.yml path")
        manifest[family] = workflow_path
    if len(set(manifest.values())) != len(manifest):
        raise PolicyError("Gate Family workflow paths must be unique")
    return manifest


def _run_git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PolicyError(f"git {' '.join(arguments)} failed: {detail}")
    return completed


def _fetch_authoritative_refs(repository: Path, remote: str) -> None:
    _run_git(
        repository,
        "fetch",
        "--quiet",
        "--force",
        "--no-tags",
        remote,
        f"refs/heads/main:{REMOTE_MAIN_REF}",
        f"{LIVE_REF}:{LOCAL_LIVE_REF}",
    )


def _read_live_ref(repository: Path, remote: str) -> str:
    completed = _run_git(repository, "ls-remote", "--refs", remote, LIVE_REF)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise PolicyError(f"remote {LIVE_REF} must exist exactly once")
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != LIVE_REF:
        raise PolicyError(f"remote {LIVE_REF} returned malformed data")
    return _validate_sha(fields[0], "observed live ref")


def _object_type(repository: Path, revision: str) -> str:
    completed = _run_git(repository, "cat-file", "-t", revision)
    return completed.stdout.strip()


def _require_commit(repository: Path, sha: str, field: str) -> None:
    if _object_type(repository, sha) != "commit":
        raise PolicyError(f"{field} must resolve to a commit")


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    completed = _run_git(
        repository,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    if completed.returncode not in (0, 1):
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PolicyError(f"could not compare commit ancestry: {detail}")
    return completed.returncode == 0


def _path_exists_at(repository: Path, revision: str, path: str) -> bool:
    completed = _run_git(
        repository,
        "cat-file",
        "-e",
        f"{revision}:{path}",
        check=False,
    )
    return completed.returncode == 0


def _paths_have_history(repository: Path, revision: str, *paths: str) -> bool:
    completed = _run_git(
        repository,
        "rev-list",
        "--max-count=1",
        revision,
        "--",
        *paths,
    )
    return bool(completed.stdout.strip())


def _blob_at(repository: Path, revision: str, path: str) -> str | None:
    completed = _run_git(
        repository,
        "rev-parse",
        f"{revision}:{path}",
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _changed_paths(
    repository: Path,
    before_sha: str,
    after_sha: str,
    scope: str,
) -> list[str]:
    if scope == "direct":
        revision_range = f"{before_sha}..{after_sha}"
    elif scope == "merge-base":
        revision_range = f"{before_sha}...{after_sha}"
    else:
        raise PolicyError("change_scope must be direct or merge-base")
    completed = _run_git(
        repository,
        "diff",
        "--name-only",
        revision_range,
        "--",
    )
    return sorted(line for line in completed.stdout.splitlines() if line)


def _validate_required_workflows(
    repository: Path,
    target_sha: str,
    manifest: dict[str, str],
) -> None:
    for workflow_path in manifest.values():
        revision = f"{target_sha}:{workflow_path}"
        completed = _run_git(repository, "cat-file", "-t", revision, check=False)
        if completed.returncode != 0 or completed.stdout.strip() != "blob":
            raise PolicyError(
                f"required Gate Family workflow is not a blob at target: {workflow_path}"
            )


def _affected_workflows(
    repository: Path,
    expected_sha: str,
    target_sha: str,
    manifest: dict[str, str],
) -> list[str]:
    return sorted(
        workflow_path
        for workflow_path in manifest.values()
        if _blob_at(repository, expected_sha, workflow_path)
        != _blob_at(repository, target_sha, workflow_path)
    )


def push_reports_exact_update(output: str) -> bool:
    return any(line.startswith("+\t") for line in output.splitlines())


def _perform_cas(
    repository: Path,
    remote: str,
    expected_sha: str,
    target_sha: str,
    report: dict[str, Any],
) -> str:
    report["mutation_attempted"] = True
    completed = _run_git(
        repository,
        "push",
        "--porcelain",
        f"--force-with-lease={LIVE_REF}:{expected_sha}",
        remote,
        f"{target_sha}:{LIVE_REF}",
        check=False,
    )
    try:
        final_observed = _read_live_ref(repository, remote)
    except PolicyError as error:
        report["final_observed_sha"] = "unavailable"
        raise OperationError(
            f"post-write verification failed after git push: {error}",
            report,
        ) from error
    report["final_observed_sha"] = final_observed
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise OperationError(
            f"exact-lease update failed; final live ref is {final_observed}: {detail}",
            report,
        )
    if not push_reports_exact_update(completed.stdout):
        raise OperationError(
            "first-attempt push did not report an actual exact-lease update; "
            f"final live ref is {final_observed}",
            report,
        )
    if final_observed != target_sha:
        raise OperationError(
            f"post-write verification mismatch: expected {target_sha}, observed {final_observed}",
            report,
        )
    return final_observed


def execute_forward(
    *,
    repository: Path,
    remote: str,
    request_path: Path,
    manifest_path: Path,
    before_sha: str,
    after_sha: str,
    mode: str,
    run_attempt: int,
    change_scope: str = "direct",
) -> dict[str, Any]:
    """Validate or publish one forward Publication Request."""

    if mode not in {"validate", "publish"}:
        raise PolicyError("forward mode must be validate or publish")
    if run_attempt < 1:
        raise PolicyError("run_attempt must be at least 1")

    request = load_request(request_path)
    manifest = load_manifest(manifest_path)
    expected_sha = request["expected_current_sha"]
    target_sha = request["target_sha"]
    request_relative = request_path.relative_to(repository).as_posix()
    changed_paths = _changed_paths(repository, before_sha, after_sha, change_scope)
    request_existed = _path_exists_at(repository, before_sha, request_relative)
    validator_existed = _path_exists_at(repository, before_sha, VALIDATOR_PATH)
    if request_existed != validator_existed:
        raise PolicyError("before revision has incomplete publisher state")
    bootstrap = not request_existed
    if bootstrap and _paths_have_history(
        repository,
        before_sha,
        request_relative,
        VALIDATOR_PATH,
    ):
        raise PolicyError("publisher bootstrap already completed in before revision history")

    _fetch_authoritative_refs(repository, remote)
    observed_sha = _read_live_ref(repository, remote)
    if _object_type(repository, LOCAL_LIVE_REF) != "commit":
        raise PolicyError(f"remote {LIVE_REF} must be a lightweight commit ref")
    _require_commit(repository, expected_sha, "expected_current_sha")
    _require_commit(repository, target_sha, "target_sha")
    if not _is_ancestor(repository, target_sha, REMOTE_MAIN_REF):
        raise PolicyError("target_sha must be reachable from current remote main")

    if bootstrap:
        if expected_sha != target_sha or observed_sha != expected_sha:
            raise PolicyError("bootstrap requires expected_current_sha == target_sha == live ref")
        direction = "bootstrap"
    else:
        if changed_paths != [request_relative]:
            raise PolicyError(
                "post-bootstrap publication commits may change only " + request_relative
            )
        if expected_sha == target_sha or not _is_ancestor(repository, expected_sha, target_sha):
            raise PolicyError(
                "forward target_sha must be a strict descendant of expected_current_sha"
            )
        direction = "forward"

    affected = _affected_workflows(repository, expected_sha, target_sha, manifest)
    report: dict[str, Any] = {
        "result": "validated",
        "direction": direction,
        "mutation_attempted": False,
        "expected_current_sha": expected_sha,
        "observed_current_sha": observed_sha,
        "target_sha": target_sha,
        "reason": request["reason"],
        "references": request["references"],
        "changed_files": changed_paths,
        "required_workflows": sorted(manifest.values()),
        "affected_workflows": affected,
        "final_observed_sha": observed_sha,
    }
    try:
        _validate_required_workflows(repository, target_sha, manifest)
    except PolicyError as error:
        raise OperationError(str(error), report) from error

    if direction == "bootstrap":
        report["result"] = "bootstrap-noop"
        return report
    if mode == "validate":
        if observed_sha != expected_sha:
            raise OperationError(
                f"live ref mismatch: expected {expected_sha}, observed {observed_sha}",
                report,
            )
        return report
    if run_attempt > 1:
        if observed_sha == target_sha:
            report["result"] = "already-live-noop"
            return report
        raise OperationError(
            "reruns never mutate and the requested target is not live",
            report,
        )
    if observed_sha != expected_sha:
        raise OperationError(
            f"live ref mismatch: expected {expected_sha}, observed {observed_sha}",
            report,
        )

    report["final_observed_sha"] = _perform_cas(
        repository,
        remote,
        expected_sha,
        target_sha,
        report,
    )
    report["result"] = "published"
    return report


def execute_rollback(
    *,
    repository: Path,
    remote: str,
    manifest_path: Path,
    expected_current_sha: str,
    target_sha: str,
    reason: str,
    references: list[str],
    mode: str,
    run_attempt: int,
) -> dict[str, Any]:
    """Validate or publish one backward Plumbing Ref transition."""

    if mode not in {"validate", "publish"}:
        raise PolicyError("rollback mode must be validate or publish")
    if run_attempt < 1:
        raise PolicyError("run_attempt must be at least 1")
    expected_sha = _validate_sha(expected_current_sha, "expected_current_sha")
    target = _validate_sha(target_sha, "target_sha")
    if not isinstance(reason, str) or not reason.strip():
        raise PolicyError("reason must contain at least one non-whitespace character")
    _validate_text(reason, "reason")
    validated_references = _validate_references(references)
    manifest = load_manifest(manifest_path)

    _fetch_authoritative_refs(repository, remote)
    observed_sha = _read_live_ref(repository, remote)
    if _object_type(repository, LOCAL_LIVE_REF) != "commit":
        raise PolicyError(f"remote {LIVE_REF} must be a lightweight commit ref")
    _require_commit(repository, expected_sha, "expected_current_sha")
    _require_commit(repository, target, "target_sha")
    if not _is_ancestor(repository, target, REMOTE_MAIN_REF):
        raise PolicyError("target_sha must be reachable from current remote main")
    if expected_sha == target or not _is_ancestor(repository, target, expected_sha):
        raise PolicyError("rollback target_sha must be a strict ancestor of expected_current_sha")

    report: dict[str, Any] = {
        "result": "validated",
        "direction": "rollback",
        "mutation_attempted": False,
        "expected_current_sha": expected_sha,
        "observed_current_sha": observed_sha,
        "target_sha": target,
        "reason": reason,
        "references": validated_references,
        "changed_files": [],
        "required_workflows": sorted(manifest.values()),
        "affected_workflows": _affected_workflows(repository, expected_sha, target, manifest),
        "final_observed_sha": observed_sha,
    }
    try:
        _validate_required_workflows(repository, target, manifest)
    except PolicyError as error:
        raise OperationError(str(error), report) from error

    if run_attempt > 1 and observed_sha == target:
        report["result"] = "already-live-noop"
        return report
    if mode == "validate":
        if observed_sha != expected_sha:
            raise OperationError(
                f"live ref mismatch: expected {expected_sha}, observed {observed_sha}",
                report,
            )
        return report
    if run_attempt > 1:
        raise OperationError(
            "reruns never mutate and the requested target is not live",
            report,
        )
    if observed_sha != expected_sha:
        raise OperationError(
            f"live ref mismatch: expected {expected_sha}, observed {observed_sha}",
            report,
        )

    report["final_observed_sha"] = _perform_cas(
        repository,
        remote,
        expected_sha,
        target,
        report,
    )
    report["result"] = "published"
    return report


def _safe_summary_value(value: Any) -> str:
    return html.escape(str(value), quote=True).replace("|", "&#124;").replace("\n", "<br>")


def _summary_list(title: str, values: list[str], empty_message: str) -> list[str]:
    lines = [f"### {title}", ""]
    if values:
        lines.extend(f"- <code>{_safe_summary_value(value)}</code>" for value in values)
    else:
        lines.append(f"- {_safe_summary_value(empty_message)}")
    lines.append("")
    return lines


def render_summary(report: dict[str, Any]) -> str:
    """Render a safe GitHub Actions job summary for an operation report."""

    mutation = "yes" if report.get("mutation_attempted") else "no"
    lines = [
        "## Plumbing Ref operation",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Result | {_safe_summary_value(report.get('result', 'unknown'))} |",
        f"| Direction | {_safe_summary_value(report.get('direction', 'unknown'))} |",
        f"| Mutation attempted | {mutation} |",
        "| Expected current SHA | "
        f"<code>{_safe_summary_value(report.get('expected_current_sha', 'unavailable'))}</code> |",
        "| Observed current SHA | "
        f"<code>{_safe_summary_value(report.get('observed_current_sha', 'unavailable'))}</code> |",
        "| Requested target SHA | "
        f"<code>{_safe_summary_value(report.get('target_sha', 'unavailable'))}</code> |",
        "| Final observed ref | "
        f"<code>{_safe_summary_value(report.get('final_observed_sha', 'unavailable'))}</code> |",
        f"| Reason | {_safe_summary_value(report.get('reason', 'unavailable'))} |",
    ]
    if report.get("error"):
        lines.append(f"| Error | {_safe_summary_value(report['error'])} |")
    lines.append("")
    lines.extend(
        _summary_list(
            "References",
            list(report.get("references", [])),
            "No references supplied.",
        )
    )
    lines.extend(
        _summary_list(
            "Merged changed files",
            list(report.get("changed_files", [])),
            "Manual dispatch / not applicable.",
        )
    )
    lines.extend(
        _summary_list(
            "Required Gate Family workflow files",
            list(report.get("required_workflows", [])),
            "Unavailable.",
        )
    )
    lines.extend(
        _summary_list(
            "Affected Gate Family workflow files",
            list(report.get("affected_workflows", [])),
            "No Gate Family workflow blob changed.",
        )
    )
    return "\n".join(lines) + "\n"


def write_summary(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as summary_file:
        summary_file.write(render_summary(report))


def _resolve_path(repository: Path, path: str) -> Path:
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else repository / candidate).resolve()


def _failure_report(
    *,
    direction: str,
    repository: Path,
    remote: str,
    error: PolicyError,
    expected_sha: str = "unavailable",
    target_sha: str = "unavailable",
    reason: str = "unavailable",
    references: list[str] | None = None,
    changed_files: list[str] | None = None,
    required_workflows: list[str] | None = None,
    affected_workflows: list[str] | None = None,
) -> dict[str, Any]:
    try:
        observed_sha = _read_live_ref(repository, remote)
    except PolicyError:
        observed_sha = "unavailable"
    error_text = str(error)
    return {
        "result": "failed",
        "direction": direction,
        "mutation_attempted": False,
        "expected_current_sha": expected_sha,
        "observed_current_sha": observed_sha,
        "target_sha": target_sha,
        "reason": reason,
        "references": references or [],
        "changed_files": changed_files or [],
        "required_workflows": required_workflows or [],
        "affected_workflows": affected_workflows or [],
        "final_observed_sha": observed_sha,
        "error": error_text,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    forward = subparsers.add_parser("forward", help="validate or publish a request")
    forward.add_argument("--repository", default=".")
    forward.add_argument("--remote", default="origin")
    forward.add_argument("--request", default=".github/plumbing-ref/publication-request.json")
    forward.add_argument("--manifest", default=".github/plumbing-ref/gate-family-workflows.json")
    forward.add_argument("--before-sha", required=True)
    forward.add_argument("--after-sha", required=True)
    forward.add_argument("--mode", choices=("validate", "publish"), required=True)
    forward.add_argument("--run-attempt", type=int, default=1)
    forward.add_argument("--change-scope", choices=("direct", "merge-base"), default="direct")
    forward.add_argument("--summary-file")

    rollback = subparsers.add_parser("rollback", help="validate or publish a rollback")
    rollback.add_argument("--repository", default=".")
    rollback.add_argument("--remote", default="origin")
    rollback.add_argument("--manifest", default=".github/plumbing-ref/gate-family-workflows.json")
    rollback.add_argument("--expected-current-sha", required=True)
    rollback.add_argument("--target-sha", required=True)
    rollback.add_argument("--reason", required=True)
    rollback.add_argument("--reference", action="append", default=[])
    rollback.add_argument("--mode", choices=("validate", "publish"), required=True)
    rollback.add_argument("--run-attempt", type=int, default=1)
    rollback.add_argument("--summary-file")
    return parser


def _summary_path(value: str | None) -> Path | None:
    candidate = value or os.environ.get("GITHUB_STEP_SUMMARY")
    return Path(candidate) if candidate else None


def main(arguments: list[str] | None = None) -> int:
    args = _build_parser().parse_args(arguments)
    repository = Path(args.repository).resolve()
    summary_path = _summary_path(args.summary_file)

    if args.command == "forward":
        request_path = _resolve_path(repository, args.request)
        manifest_path = _resolve_path(repository, args.manifest)
        request: dict[str, Any] = {}
        manifest: dict[str, str] = {}
        changed_files: list[str] = []
        direction = "forward"
        try:
            request = load_request(request_path)
            manifest = load_manifest(manifest_path)
            changed_files = _changed_paths(
                repository,
                args.before_sha,
                args.after_sha,
                args.change_scope,
            )
            request_relative = request_path.relative_to(repository).as_posix()
            if not _path_exists_at(repository, args.before_sha, request_relative):
                direction = "bootstrap"
            report = execute_forward(
                repository=repository,
                remote=args.remote,
                request_path=request_path,
                manifest_path=manifest_path,
                before_sha=args.before_sha,
                after_sha=args.after_sha,
                mode=args.mode,
                run_attempt=args.run_attempt,
                change_scope=args.change_scope,
            )
        except OperationError as error:
            report = error.report
            write_summary(summary_path, report)
            print(f"error: {error}", file=sys.stderr)
            return 1
        except PolicyError as error:
            report = _failure_report(
                direction=direction,
                repository=repository,
                remote=args.remote,
                error=error,
                expected_sha=request.get("expected_current_sha", "unavailable"),
                target_sha=request.get("target_sha", "unavailable"),
                reason=request.get("reason", "unavailable"),
                references=request.get("references", []),
                changed_files=changed_files,
                required_workflows=sorted(manifest.values()),
            )
            write_summary(summary_path, report)
            print(f"error: {error}", file=sys.stderr)
            return 1
    else:
        manifest_path = _resolve_path(repository, args.manifest)
        manifest: dict[str, str] = {}
        try:
            manifest = load_manifest(manifest_path)
            report = execute_rollback(
                repository=repository,
                remote=args.remote,
                manifest_path=manifest_path,
                expected_current_sha=args.expected_current_sha,
                target_sha=args.target_sha,
                reason=args.reason,
                references=args.reference,
                mode=args.mode,
                run_attempt=args.run_attempt,
            )
        except OperationError as error:
            report = error.report
            write_summary(summary_path, report)
            print(f"error: {error}", file=sys.stderr)
            return 1
        except PolicyError as error:
            report = _failure_report(
                direction="rollback",
                repository=repository,
                remote=args.remote,
                error=error,
                expected_sha=args.expected_current_sha,
                target_sha=args.target_sha,
                reason=args.reason,
                references=args.reference,
                required_workflows=sorted(manifest.values()),
            )
            write_summary(summary_path, report)
            print(f"error: {error}", file=sys.stderr)
            return 1

    write_summary(summary_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
