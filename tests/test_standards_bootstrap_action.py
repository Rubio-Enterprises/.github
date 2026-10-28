"""The pin provisioner must fail before setup, including when mise skips a declaration."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import ROOT, extract_run_block, extract_step

ACTION = (ROOT / "actions" / "standards-bootstrap" / "action.yml").read_text(encoding="utf-8")
PREREQUISITES = "Check the native prerequisites"
MINT = "Mint the standards read token"
RESOLVE = "Resolve standards main once"
CHECKOUT = "Check out the Shared Config Directory"
UNROUTE = "Remove the standards read route"
VALIDATE = "Validate and export the standards pin"
INSTALL_MISE = "Install mise without loading repository config"
READY = "Require the Shared Config Directory before bootstrap"
BOOTSTRAP = "Install mise and bootstrap the Shared Config Checkout"
STEPS = [PREREQUISITES, MINT, RESOLVE, CHECKOUT, UNROUTE, VALIDATE, INSTALL_MISE, READY, BOOTSTRAP]
URL = "https://github.com/Rubio-Enterprises/standards.git"
ROUTE_KEY = "url.https://x-access-token:ghs_secret@github.com/Rubio-Enterprises/standards.insteadof"
BASHES = ["bash"] + (["/bin/bash"] if Path("/bin/bash").exists() else [])


def action_inputs() -> dict[str, str]:
    block = ACTION.split("\ninputs:\n", 1)[1].split("\nruns:\n", 1)[0]
    inputs: dict[str, str] = {}
    for match in re.finditer(r"(?m)^  ([a-z-]+):\n((?:    .*\n|\s*\n)*)", block + "\n"):
        default = re.search(r"(?m)^    default: (.*)$", match.group(2))
        inputs[match.group(1)] = default.group(1).strip('"') if default else ""
    return inputs


def run(name: str, cwd: Path, *, shell: str = "bash", **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shell, "-c", extract_run_block(ACTION, name)], cwd=cwd, text=True,
        capture_output=True, check=False, env={**os.environ, **env},
    )


def outputs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


class InterfaceTests(unittest.TestCase):
    def test_inputs_and_defaults(self) -> None:
        self.assertEqual(action_inputs(), {
            "app-id": "", "private-key": "", "mise-version": "",
            "cache-key-prefix": "mise-v1", "bootstrap-parts": "repos,tools",
        })
        for token in ("config_model", "config-model", "keep-route", "standards_channel"):
            self.assertNotIn(token, ACTION)

    def test_steps_run_in_order_without_a_model_guard(self) -> None:
        self.assertEqual(re.findall(r"(?m)^    - name: (.*)$", ACTION), STEPS)
        self.assertEqual(re.findall(r"(?m)^      if: (.*)$", ACTION), ["always()"])

    def test_nested_actions_are_pinned_by_digest(self) -> None:
        uses = re.findall(r"(?m)^\s+uses: (\S+)(.*)$", ACTION)
        self.assertEqual(len(uses), 4)
        for ref, comment in uses:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"^[\w-]+/[\w.-]+@[0-9a-f]{40}$")
                self.assertRegex(comment, r"^ # v\d")

    def test_token_is_read_only_and_scoped_to_standards(self) -> None:
        step = extract_step(ACTION, MINT)
        for line in ("owner: Rubio-Enterprises", "repositories: standards\n",
                     "permission-contents: read", "app-id: ${{ inputs.app-id }}",
                     "private-key: ${{ inputs.private-key }}"):
            self.assertIn(line, step + "\n")

    def test_checkout_uses_the_resolved_commit_and_full_history(self) -> None:
        step = extract_step(ACTION, CHECKOUT)
        for line in ("uses: actions/checkout@", "repository: Rubio-Enterprises/standards",
                     "ref: ${{ steps.pin.outputs.sha }}", "path: .standards-config",
                     "fetch-depth: 0", "persist-credentials: false",
                     "token: ${{ steps.token.outputs.token }}"):
            self.assertIn(line, step)
        self.assertIn("working-directory: ${{ runner.temp }}", extract_step(ACTION, RESOLVE))
        self.assertNotIn("insteadOf", ACTION)

    def test_mise_is_installed_from_a_neutral_directory(self) -> None:
        step = extract_step(ACTION, INSTALL_MISE)
        for line in ("install: false", "cache: false", "env: false",
                     "working_directory: ${{ runner.temp }}",
                     "version: ${{ steps.prerequisites.outputs.mise_version }}"):
            self.assertIn(line, step)
        self.assertNotIn("bootstrap: true", step)

    def test_both_bootstrap_modes_run_after_readiness_without_the_reader_token(self) -> None:
        step = extract_step(ACTION, BOOTSTRAP)
        for line in ("bootstrap: true", "bootstrap_args: --only ${{ inputs.bootstrap-parts }}",
                     "cache: ${{ inputs.bootstrap-parts != 'repos' }}",
                     "env: ${{ inputs.bootstrap-parts != 'repos' }}"):
            self.assertIn(line, step)
        for name in STEPS[STEPS.index(VALIDATE):]:
            self.assertNotIn("steps.token.outputs.token", extract_step(ACTION, name))


class PrerequisiteTests(unittest.TestCase):
    def check(self, *, app_id: str = "1", key: str = "k", version: str = "",
              installed: str | None = None, shell: str = "bash", parts: str = "repos,tools"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "mise-data"
            if installed is not None:
                binary = data / "bin" / "mise"
                binary.parent.mkdir(parents=True)
                report = json.dumps({"version": f"{installed} linux-x64 (2026-10-01)"})
                binary.write_text(f"#!/bin/sh\n[ \"$*\" = 'version --json' ] || exit 2\necho '{report}'\n")
                binary.chmod(0o755)
            result = run(PREREQUISITES, root, shell=shell, APP_ID=app_id, APP_PRIVATE_KEY=key,
                         MISE_VERSION=version, MISE_DATA_DIR=str(data), BOOTSTRAP_PARTS=parts,
                         GITHUB_OUTPUT=str(root / "out"))
            return result, outputs(root / "out")

    def test_bootstrap_parts_accepts_only_the_two_supported_modes(self) -> None:
        for parts in ("repos", "repos,tools", "", "tools", "repos --skip tools", "repos; echo injected"):
            for shell in BASHES:
                with self.subTest(parts=parts, shell=shell):
                    result, out = self.check(parts=parts, shell=shell)
                    valid = parts in ("repos", "repos,tools")
                    self.assertEqual(result.returncode == 0, valid, result.stderr)
                    if not valid:
                        self.assertIn("bootstrap-parts must be repos or repos,tools", result.stderr)
                        self.assertEqual(out, {})

    def test_missing_credentials_fail_with_a_clear_error(self) -> None:
        for app_id, key in (("", "k"), ("1", ""), ("", "")):
            with self.subTest(app_id=app_id, key=key):
                result, out = self.check(app_id=app_id, key=key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("a reusable workflow's caller must forward them", result.stderr)
                self.assertEqual(out, {})

    def test_mise_version_must_meet_the_native_floor(self) -> None:
        for version, installed, expected in (
            ("2026.10.19", None, "2026.10.19"), ("2026.11.3", "2026.9.11", "2026.11.3"),
            ("2027.1.0", None, "2027.1.0"), ("", None, "2026.10.19"),
            ("", "2026.11.3", "2026.11.3"), ("", "2026.10.19", "2026.10.19"),
            ("2026.10.18", None, None), ("2026.9.30", None, None), ("2025.12.99", None, None),
            ("", "2026.10.5", None), ("latest", None, None), ("v2026.10.19", None, None),
        ):
            for shell in BASHES:
                with self.subTest(version=version, installed=installed, shell=shell):
                    result, out = self.check(version=version, installed=installed, shell=shell)
                    self.assertEqual(result.returncode == 0, expected is not None, result.stderr)
                    self.assertEqual(out, {"mise_version": expected} if expected else {})


class ResolutionTests(unittest.TestCase):
    def test_only_a_successful_full_lowercase_sha_is_exported(self) -> None:
        for sha in ("a" * 40, "", "A" * 40, "a" * 39, "main", "a" * 40 + "\nOTHER=value"):
            for code in (0, 1):
                for shell in BASHES:
                    with self.subTest(sha=sha, code=code, shell=shell), tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        gh = root / "gh"
                        gh.write_text(
                            '#!/bin/sh\nprintf "%s\\n" "$*" > "$ARGS"\n'
                            'printf "%s\\n" "$SHA"\nexit "$CODE"\n'
                        )
                        gh.chmod(0o755)
                        result = run(RESOLVE, root, shell=shell, SHA=sha, CODE=str(code),
                                     ARGS=str(root / "args"), PATH=f"{root}:{os.environ['PATH']}",
                                     GITHUB_OUTPUT=str(root / "out"))
                        valid = code == 0 and bool(re.fullmatch("[0-9a-f]{40}", sha))
                        self.assertEqual(result.returncode == 0, valid, result.stderr)
                        self.assertEqual(outputs(root / "out"), {"sha": sha} if valid else {})
                        self.assertEqual((root / "args").read_text().strip(),
                                         "api repos/Rubio-Enterprises/standards/commits/main --jq .sha")


class CheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / ".standards-config"
        self.repo.mkdir()
        self.env = {
            "HOME": str(self.root), "XDG_CONFIG_HOME": str(self.root / "xdg"),
            "GIT_CONFIG_GLOBAL": str(self.root / ".gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "GITHUB_ENV": str(self.root / "env"), "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
        }
        self.git("init", "-q", "-b", "main")
        base = self.repo / "config" / "lefthook" / "base.yml"
        base.parent.mkdir(parents=True)
        base.write_text("{}\n")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "base")
        self.first = self.git("rev-parse", "HEAD").strip()
        self.git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "--allow-empty", "-qm", "next")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("remote", "add", "origin", URL)

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, env={**os.environ, **self.env}, text=True,
            stderr=subprocess.PIPE,
        )

    def test_clean_checkout_exports_the_independent_pin(self) -> None:
        for shell in BASHES:
            with self.subTest(shell=shell):
                result = run(VALIDATE, self.root, shell=shell, STANDARDS_PIN=self.sha, **self.env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(outputs(self.root / "env"), {"STANDARDS_PIN": self.sha})
                self.assertIn(f"standards at `{self.sha}`", (self.root / "summary").read_text())

    def test_checkout_url_is_normalized_to_the_canonical_credential_free_origin(self) -> None:
        self.git("remote", "set-url", "origin", URL.removesuffix(".git"))
        result = run(VALIDATE, self.root, STANDARDS_PIN=self.sha, **self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("config", "--get", "remote.origin.url").strip(), URL)

    def test_wrong_empty_or_malformed_pin_fails_without_export(self) -> None:
        for pin in ("", self.first, "1" * 40, "A" * 40, "main", "-x", self.sha + "\nOTHER=value"):
            for shell in BASHES:
                with self.subTest(pin=pin, shell=shell):
                    result = run(VALIDATE, self.root, shell=shell, STANDARDS_PIN=pin, **self.env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(outputs(self.root / "env"), {})

    def test_wrong_or_credential_bearing_origin_is_not_repaired(self) -> None:
        for url in ("https://example.invalid/standards.git", URL.replace("https://", "https://token@")):
            with self.subTest(url=url):
                self.git("remote", "set-url", "origin", url)
                result = run(VALIDATE, self.root, STANDARDS_PIN=self.sha, **self.env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs(self.root / "env"), {})

    def test_dirty_tracked_or_untracked_tree_fails(self) -> None:
        for path in (self.repo / "config/lefthook/base.yml", self.repo / "unexpected"):
            with self.subTest(path=path):
                path.write_text("dirty\n")
                result = run(VALIDATE, self.root, STANDARDS_PIN=self.sha, **self.env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs(self.root / "env"), {})
                if path.name == "unexpected":
                    path.unlink()
                else:
                    self.git("restore", str(path.relative_to(self.repo)))

    def test_shallow_checkout_fails(self) -> None:
        (self.repo / ".git" / "shallow").write_text(self.sha + "\n")
        result = run(VALIDATE, self.root, STANDARDS_PIN=self.sha, **self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outputs(self.root / "env"), {})

    def test_missing_checkout_does_not_validate_the_parent_repository(self) -> None:
        shutil.rmtree(self.repo)
        result = run(VALIDATE, self.root, STANDARDS_PIN=self.sha, **self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("real Git checkout", result.stderr)


class RouteTests(unittest.TestCase):
    def test_cleanup_is_unconditional_and_preserves_other_routes(self) -> None:
        for shell in BASHES:
            for routed in (True, False):
                with self.subTest(shell=shell, routed=routed), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    env = {"HOME": tmp, "XDG_CONFIG_HOME": str(root / "xdg"),
                           "GIT_CONFIG_GLOBAL": str(root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
                    keys = {"url.git@github.com:.insteadOf": "gh:",
                            "url.https://x-access-token:ghs_go@github.com/Rubio-Enterprises/scaleset.insteadOf":
                                "https://github.com/Rubio-Enterprises/scaleset"}
                    if routed:
                        keys[ROUTE_KEY] = "https://github.com/Rubio-Enterprises/standards"
                    for key, value in keys.items():
                        subprocess.run(["git", "config", "--global", key, value],
                                       env={**os.environ, **env}, check=True)
                    result = run(UNROUTE, root, shell=shell, **env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    remaining = (root / "gitconfig").read_text()
                    self.assertNotIn("ghs_secret", remaining)
                    self.assertIn("ghs_go", remaining)
                    self.assertIn("git@github.com:", remaining)


class ReadinessTests(unittest.TestCase):
    def test_status_must_report_the_configured_pin_not_just_exit_successfully(self) -> None:
        sha = "a" * 40
        good = {"path_raw": ".standards-config", "url": URL, "ref": sha,
                "current_sha": sha, "state": "current"}
        for entry in (good, None, {**good, "ref": "main"}, {**good, "current_sha": "b" * 40},
                      {**good, "state": "dirty"}, {**good, "path_raw": "other"},
                      {**good, "url": "https://example.invalid/standards.git"}):
            for code, checkout in ((0, True), (1, True), (0, False)):
                for shell in BASHES:
                    with self.subTest(entry=entry, code=code, checkout=checkout, shell=shell), \
                            tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        binary = root / "bin" / "mise"
                        binary.parent.mkdir()
                        binary.write_text(
                            '#!/bin/sh\n[ "$*" = "bootstrap repos status --missing --json" ] || exit 2\n'
                            'printf "%s\\n" "$STATUS"\nexit "$CODE"\n'
                        )
                        binary.chmod(0o755)
                        if checkout:
                            base = root / ".standards-config/config/lefthook/base.yml"
                            base.parent.mkdir(parents=True)
                            base.write_text("{}\n")
                        result = run(READY, root, shell=shell, STANDARDS_PIN=sha, CODE=str(code),
                                     STATUS=json.dumps({"repos": [entry] if entry else []}),
                                     PATH=f"{binary.parent}:{os.environ['PATH']}")
                        self.assertEqual(result.returncode == 0, entry == good and code == 0 and checkout,
                                         result.stderr)

    def test_invalid_pin_fails_before_mise_can_skip_the_declaration(self) -> None:
        for pin in ("", "-x", "A" * 40):
            with self.subTest(pin=pin), tempfile.TemporaryDirectory() as tmp:
                result = run(READY, Path(tmp), STANDARDS_PIN=pin)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("STANDARDS_PIN must be", result.stderr)


if __name__ == "__main__":
    unittest.main()
