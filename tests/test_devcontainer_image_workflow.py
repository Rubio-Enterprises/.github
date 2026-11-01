"""Contracts and shell execution tests for the Dev Container image reusable."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/devcontainer-image.yml").read_text(encoding="utf-8")

DEFAULT_SKIP_PATTERNS = (
    "**/.git/**",
    "**/node_modules/**",
    "**/go.mod",
    "**/go.sum",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/Pipfile.lock",
    "**/Gemfile.lock",
    "**/*.jpg",
    "**/*.png",
    "**/*.gif",
    "**/*.doc",
    "**/*.pdf",
    "**/*.bin",
    "**/*.svg",
    "**/*.socket",
    "**/*.deb",
    "**/*.rpm",
    "**/*.zip",
    "**/*.gz",
    "**/*.gzip",
    "**/*.tar",
)
CACHE_SKIP_PATTERNS = (
    "**/pnpm-store/**",
    "**/_cacache/**",
    "**/.cache/uv/**",
    "**/.cache/pip/**",
    "**/.cache/mise/npm/aube/store/**",
    "**/.cache/mise/downloads-cas/**",
)

ROOTED_PUBLIC_CACHE_SKIP_PATTERNS = (
    "usr/local/share/mise/installs/**",
    "home/*/.local/share/mise/installs/**",
    "root/.local/share/mise/installs/**",
    "mnt/devcontainer-cache/mise/installs/**",
    "var/cache/devcontainer/gomod/**",
    "home/*/.cache/gomod/**",
    "mnt/devcontainer-cache/gomod/**",
    "home/*/go/pkg/mod/**",
    "root/go/pkg/mod/**",
    "home/*/.bun/install/cache/**",
    "root/.bun/install/cache/**",
    "var/cache/devcontainer/bun/**",
    "mnt/devcontainer-cache/bun/**",
    "home/*/.local/share/pnpm/store/**",
    "root/.local/share/pnpm/store/**",
    "var/cache/devcontainer/uv/**",
    "mnt/devcontainer-cache/uv/**",
    "home/*/.cache/cargo/registry/**",
    "home/*/.local/share/rustup/toolchains/**",
    "home/*/.local/share/uv/python/*-*-*/**",
)


RUBY_PUBLIC_CACHE_SKIP_PATTERNS = (
    "usr/local/bundle/gems/**",
    "usr/local/bundle/cache/*.gem",
    "usr/local/bundle/*/*/gems/**",
    "usr/local/bundle/*/*/cache/*.gem",
    "home/*/.gem/gems/**",
    "home/*/.gem/cache/*.gem",
    "root/.gem/gems/**",
    "root/.gem/cache/*.gem",
    "home/*/.gem/*/*/gems/**",
    "home/*/.gem/*/*/cache/*.gem",
    "root/.gem/*/*/gems/**",
    "root/.gem/*/*/cache/*.gem",
    "home/*/.local/share/gem/*/*/gems/**",
    "home/*/.local/share/gem/*/*/cache/*.gem",
    "root/.local/share/gem/*/*/gems/**",
    "root/.local/share/gem/*/*/cache/*.gem",
    "home/*/.cache/*/bundle/*/*/gems/**",
    "home/*/.cache/*/bundle/*/*/cache/*.gem",
    "root/.cache/*/bundle/*/*/gems/**",
    "root/.cache/*/bundle/*/*/cache/*.gem",
    "workspaces/*/.bundle/*/*/gems/**",
    "workspaces/*/.bundle/*/*/cache/*.gem",
)


def script(name: str) -> str:
    lines = TEXT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2:] if line.strip() else "")
    return "\n".join(body) + "\n"


def job(name: str) -> str:
    return re.search(rf"(?ms)^  {name}:\n.*?(?=^  [a-z]+:|\Z)", TEXT).group()


class WorkflowTests(unittest.TestCase):
    def test_only_unprivileged_caller_events_are_accepted(self) -> None:
        self.assertIn('  workflow_call:\n', TEXT)
        self.assertNotIn("pull_request_target", TEXT)
        self.assertNotIn("workflow_run", TEXT)
        prepare = job("prepare")
        self.assertIn("github.event_name == 'pull_request'", prepare)
        self.assertIn('fromJSON(\'["push","schedule","workflow_dispatch"]\')', prepare)
        self.assertIn("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", prepare)
        self.assertIn("PUBLISH: ${{ github.event_name != 'pull_request' }}", prepare)

    def test_pr_token_cannot_write_packages(self) -> None:
        validate = job("validate")
        self.assertIn("if: needs.prepare.outputs.publish == 'false'", validate)
        self.assertIn("      contents: read\n      packages: read", validate)
        self.assertNotIn("packages: write", validate)
        self.assertIn("if: needs.prepare.outputs.publish == 'true'", job("build"))
        self.assertIn("      packages: write", job("build"))
        for name in ("merge", "prune"):
            self.assertIn("      packages: write", job(name))
            self.assertNotIn("contents:", job(name))
        self.assertIn("permissions: {}", TEXT)

    def test_native_bootstrap_secrets_are_optional_and_stop_at_the_action(self) -> None:
        interface = TEXT.split("permissions: {}", 1)[0]
        for secret in ("STANDARDS_READER_APP_ID", "STANDARDS_READER_APP_PRIVATE_KEY"):
            self.assertIn(f"      {secret}:\n", interface)
            block = interface.split(f"      {secret}:\n", 1)[1].split("\n      STANDARDS_", 1)[0]
            self.assertIn("        required: false", block)
            self.assertEqual(TEXT.count(f"secrets.{secret}"), 1)
        steps = job("validate")
        checkout = steps.index("- name: Check out the caller")
        bootstrap = steps.index("- name: Provision native shared config")
        build = steps.index("- name: Build the architecture image")
        self.assertLess(checkout, bootstrap)
        self.assertLess(bootstrap, build)
        bootstrap_step = steps[bootstrap:steps.index("- name: Verify native Docker architecture")]
        self.assertIn("uses: Rubio-Enterprises/.github/actions/standards-bootstrap@main\n", bootstrap_step)
        self.assertIn("bootstrap-parts: repos", bootstrap_step)
        self.assertNotIn("keep-route", bootstrap_step)
        pin = steps.index("- name: Require the image build pin")
        self.assertLess(bootstrap, pin)
        self.assertLess(pin, build)
        self.assertNotIn("--secret", script("Build the architecture image"))
        self.assertNotIn("STANDARDS_READER", script("Build the architecture image"))
        self.assertIn("steps: *build-steps", job("build"))

    def test_bootstrap_has_no_metadata_selector(self) -> None:
        self.assertNotIn("config-model:", TEXT)
        self.assertNotIn("config_model", TEXT)

    def test_native_routes_and_shared_steps(self) -> None:
        self.assertIn(".docker || fromJSON('[\"ubuntu-latest\"]')", TEXT)
        self.assertIn(".arm || fromJSON('[\"ubuntu-24.04-arm\"]')", TEXT)
        self.assertIn("strategy: &architectures", job("validate"))
        self.assertIn("strategy: *architectures", job("build"))
        self.assertIn("steps: *build-steps", job("build"))
        self.assertIn("needs: [prepare, build]", job("merge"))
        self.assertIn("needs: [prepare, merge]", job("prune"))
        self.assertIn("cancel-in-progress: false", TEXT)
        self.assertNotIn("group: ${{ github.workflow", TEXT)

    def assert_pinned_actions(self, text: str) -> None:
        for ref, comment in re.findall(r"uses: (\S+)(.*)", text):
            if ref in {"Rubio-Enterprises/.github/actions/standards-bootstrap@main",
                       "Rubio-Enterprises/.github/actions/repository-image@main",
                       "Rubio-Enterprises/.github/.github/workflows/repository-image.yml@main"}:
                continue
            self.assertRegex(ref, r"@[a-f0-9]{40}$")
            self.assertRegex(comment, r" # v[0-9]")

    def test_action_pin_exception_is_exact(self) -> None:
        self.assert_pinned_actions("uses: Rubio-Enterprises/.github/actions/standards-bootstrap@main")
        self.assert_pinned_actions("uses: actions/checkout@" + "a" * 40 + " # v7")
        for ref in (
            "Rubio-Enterprises/.github/actions/standards-bootstrap@stable",
            "Rubio-Enterprises/.github/actions/standards-bootstrap@v4",
            "Rubio-Enterprises/.github/actions/other@main",
            "Rubio-Enterprises/.github-private/actions/standards-bootstrap@main",
            "other/.github/actions/standards-bootstrap@main",
            "actions/checkout@main", "docker/login-action@v4",
        ):
            with self.subTest(ref=ref), self.assertRaises(AssertionError):
                self.assert_pinned_actions(f"uses: {ref}")

    def test_pins_checkout_and_registry_hygiene(self) -> None:
        self.assert_pinned_actions(TEXT)
        self.assertIn("persist-credentials: false", TEXT)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", TEXT)
        self.assertIn("@devcontainers/cli@0.89.0", TEXT)
        self.assertIn("--ignore-scripts", TEXT)
        self.assertIn('BUILDX_NO_DEFAULT_ATTESTATIONS: "1"', TEXT)
        self.assertIn('delete-only-untagged-versions: "true"', TEXT)
        self.assertIn("min-versions-to-keep: 5", TEXT)
        for name in (
            "Resolve image and publication policy", "Verify native Docker architecture",
            "Check the Dockerfile without building", "Install checksum-pinned Trivy",
            "Build the architecture image", "Scan the local image for secrets",
            "Push the scanned architecture image", "Publish multi-architecture tags and cache aliases",
        ):
            self.assertNotIn("${{", script(name))

    def test_check_scan_and_push_order_is_fail_closed_on_both_paths(self) -> None:
        steps = job("validate")
        names = (
            "Check the Dockerfile without building", "Build the architecture image",
            "Scan the local image for secrets", "Push the scanned architecture image",
        )
        positions = [steps.index(f"- name: {name}") for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("continue-on-error", TEXT)
        self.assertNotIn("--push", script("Build the architecture image"))
        self.assertIn("--output type=docker", script("Build the architecture image"))
        self.assertIn("if: needs.prepare.outputs.publish == 'true'", steps[positions[-1]:])
        self.assertNotIn("if:", steps[positions[-2]:positions[-1]])
        self.assertIn("steps: *build-steps", job("build"))

    def test_trivy_archives_and_hashes_are_pinned_without_mutable_installer(self) -> None:
        install = script("Install checksum-pinned Trivy")
        self.assertIn("version=0.76.1", install)
        for checksum in (
            "c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0",
            "e174dff2a6dba5e3ae41f8d0537a6324405bd08132497340304c6b20e0f16a1f",
        ):
            self.assertIn(checksum, install)
        self.assertLess(install.index("sha256sum --check --strict"), install.index("tar -xzf"))
        self.assertNotIn("latest", install)
        self.assertNotIn("checksums.txt", install)


class ShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {
            **os.environ,
            "RUNNER_TEMP": str(self.root),
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises",
            "GITHUB_REPOSITORY": "Rubio-Enterprises/Bookmark-Scraper",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_WORKSPACE": str(self.root / "workspace"),
            "IMAGE_INPUT": "",
            "CONFIG": ".github/.devcontainer/devcontainer.json",
            "PUBLISH": "true",
            "STANDARDS_PIN": "a" * 40,
            "IMAGE": "ghcr.io/rubio-enterprises/bookmark-scraper/devcontainer",
            "ARCH": "arm64",
            "CAPTURE": str(self.root / "capture"),
        }

    def run_script(self, name: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script(name)], env={**self.env, **env},
            capture_output=True, text=True, check=False,
        )

    def stub(self, path: Path, source: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/bash\nset -euo pipefail\n" + source, encoding="utf-8")
        path.chmod(0o755)

    def test_default_image_is_lowercase_and_package_keeps_nested_path(self) -> None:
        result = self.run_script("Resolve image and publication policy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            Path(self.env["GITHUB_OUTPUT"]).read_text(),
            "image=ghcr.io/rubio-enterprises/bookmark-scraper/devcontainer\n"
            "package=bookmark-scraper/devcontainer\npublish=true\n",
        )

    def test_custom_image_is_normalized_and_pr_is_not_publishable(self) -> None:
        result = self.run_script(
            "Resolve image and publication policy",
            IMAGE_INPUT="GHCR.IO/Rubio-Enterprises/Other--Repo/devcontainer", PUBLISH="false",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = Path(self.env["GITHUB_OUTPUT"]).read_text()
        self.assertIn("image=ghcr.io/rubio-enterprises/other--repo/devcontainer\n", output)
        self.assertIn("publish=false\n", output)

    def test_rejects_registry_tags_foreign_owner_and_output_injection(self) -> None:
        for image in (
            "docker.io/rubio-enterprises/repo", "ghcr.io/other/repo/devcontainer",
            self.env["IMAGE"] + ":main", self.env["IMAGE"] + "@sha256:abc",
            self.env["IMAGE"] + "\npublish=true", self.env["IMAGE"] + '$(touch injected)',
        ):
            with self.subTest(image=image):
                self.assertNotEqual(self.run_script(
                    "Resolve image and publication policy", IMAGE_INPUT=image,
                ).returncode, 0)

    def test_rejects_absolute_and_parent_config_paths(self) -> None:
        for config in ("", "/etc/passwd", "..", "../x", ".github/../x", "x/.."):
            with self.subTest(config=config):
                self.assertNotEqual(self.run_script(
                    "Resolve image and publication policy", CONFIG=config,
                ).returncode, 0)

    def test_image_build_pin_is_required_and_strict(self) -> None:
        self.assertEqual(self.run_script("Require the image build pin").returncode, 0)
        for pin in ("", "main", "A" * 40, "a" * 39, "g" * 40, "a" * 40 + "\nother=value"):
            with self.subTest(pin=pin):
                result = self.run_script("Require the image build pin", STANDARDS_PIN=pin)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("STANDARDS_PIN", result.stderr)
        del self.env["STANDARDS_PIN"]
        self.assertNotEqual(self.run_script("Require the image build pin").returncode, 0)

    def test_build_loads_local_image_and_never_pushes(self) -> None:
        cli = self.root / "devcontainer-cli/node_modules/.bin/devcontainer"
        self.stub(cli, 'printf "%s\\0" "$@" > "$CAPTURE"\nprintf "%s" "$STANDARDS_PIN" > "$RUNNER_TEMP/build-pin"\n')
        config = "path with spaces/$(not-a-command).json"
        for publish in ("true", "false"):
            with self.subTest(publish=publish):
                result = self.run_script("Build the architecture image", PUBLISH=publish, CONFIG=config)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = Path(self.env["CAPTURE"]).read_bytes().decode().split("\0")[:-1]
                self.assertEqual(args[0], "build")
                self.assertEqual(args[args.index("--config") + 1], config)
                self.assertEqual(args[args.index("--platform") + 1], "linux/arm64")
                self.assertEqual(args[args.index("--cache-from") + 1], self.env["IMAGE"] + ":main-arm64")
                self.assertEqual(args[args.index("--image-name") + 1], self.env["IMAGE"] + ":sha-" + "a" * 40 + "-arm64")
                self.assertIn("org.opencontainers.image.source=https://github.com/Rubio-Enterprises/Bookmark-Scraper", args)
                self.assertNotIn("--push", args)
                self.assertEqual(args[args.index("--output") + 1], "type=docker")
                self.assertEqual((self.root / "build-pin").read_text(), self.env["STANDARDS_PIN"])

    def configuration(self, build: dict | None) -> None:
        if build is not None:
            build.setdefault("args", {}).setdefault("STANDARDS_PIN", self.env["STANDARDS_PIN"])
        configuration = {"build": build} if build is not None else {"image": "base:tag"}
        (self.root / "resolved.json").write_text(json.dumps({"configuration": configuration}))
        self.stub(
            self.root / "devcontainer-cli/node_modules/.bin/devcontainer",
            'test "$1" = read-configuration\ncat "$RUNNER_TEMP/resolved.json"\n',
        )

    def docker_capture(self) -> None:
        self.stub(self.root / "docker", 'printf "%s\\0" "$@" > "$CAPTURE"\nexit "${DOCKER_STATUS:-0}"\n')
        self.env["PATH"] = str(self.root) + os.pathsep + os.environ["PATH"]

    def test_docker_check_resolves_both_paths_relative_to_config(self) -> None:
        self.configuration({
            "dockerfile": "Docker file", "context": "../..", "target": "dependencies",
            "args": {"VERSION": "value with spaces;$(not-a-command)"},
        })
        self.docker_capture()
        for arch in ("amd64", "arm64"):
            result = self.run_script("Check the Dockerfile without building", ARCH=arch)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = Path(self.env["CAPTURE"]).read_bytes().decode().split("\0")[:-1]
            self.assertEqual(args, [
                "build", "--check", "--platform", f"linux/{arch}", "--file",
                str(self.root / "workspace/.github/.devcontainer/Docker file"),
                "--target", "dependencies", "--build-arg", "VERSION=value with spaces;$(not-a-command)",
                "--build-arg", "STANDARDS_PIN=" + "a" * 40, str(self.root / "workspace"),
            ])

    def test_docker_check_passes_named_context_options_without_shell_evaluation(self) -> None:
        options = ["--build-context", "standards=/path with spaces/$(not-a-command)"]
        self.configuration({"dockerfile": "Dockerfile", "options": options})
        self.docker_capture()
        result = self.run_script("Check the Dockerfile without building")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = Path(self.env["CAPTURE"]).read_bytes().decode().split("\0")[:-1]
        self.assertEqual(args[-3:-1], options)

    def test_docker_check_rejects_a_missing_or_different_build_pin(self) -> None:
        self.docker_capture()
        for pin in ("", "b" * 40, "${localEnv:STANDARDS_PIN}"):
            self.configuration({"dockerfile": "Dockerfile", "args": {"STANDARDS_PIN": pin}})
            result = self.run_script("Check the Dockerfile without building")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(Path(self.env["CAPTURE"]).exists())
        self.configuration({"dockerfile": "Dockerfile"})
        data = json.loads((self.root / "resolved.json").read_text())
        del data["configuration"]["build"]["args"]["STANDARDS_PIN"]
        (self.root / "resolved.json").write_text(json.dumps(data))
        self.assertNotEqual(self.run_script("Check the Dockerfile without building").returncode, 0)
        self.assertFalse(Path(self.env["CAPTURE"]).exists())

    def test_docker_check_defaults_context_and_skips_image_only_config(self) -> None:
        self.docker_capture()
        self.configuration({"dockerfile": "Dockerfile"})
        result = self.run_script("Check the Dockerfile without building")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = Path(self.env["CAPTURE"]).read_bytes().decode().split("\0")[:-1]
        self.assertEqual(args[-1], str(self.root / "workspace/.github/.devcontainer"))
        Path(self.env["CAPTURE"]).unlink()
        self.configuration(None)
        result = self.run_script("Check the Dockerfile without building")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped", result.stdout)
        self.assertFalse(Path(self.env["CAPTURE"]).exists())

    def test_docker_check_violation_and_parse_failure_fail_the_step(self) -> None:
        self.configuration({"dockerfile": "Dockerfile"})
        self.docker_capture()
        result = self.run_script("Check the Dockerfile without building", DOCKER_STATUS="1")
        self.assertEqual(result.returncode, 1, result.stderr)
        (self.root / "resolved.json").write_text("not JSON")
        result = self.run_script("Check the Dockerfile without building")
        self.assertNotEqual(result.returncode, 0)

    def test_trivy_install_verifies_before_extracting_and_rejects_bad_arch(self) -> None:
        self.stub(self.root / "curl", 'printf "%s\\0" "$@" > "$CAPTURE"\n')
        self.stub(self.root / "sha256sum", 'cat > "$RUNNER_TEMP/checksum-input"\nexit "${CHECKSUM_STATUS:-0}"\n')
        self.stub(self.root / "tar", 'touch "$RUNNER_TEMP/extracted"\n')
        self.env["PATH"] = str(self.root) + os.pathsep + os.environ["PATH"]
        for arch, platform in (("amd64", "64bit"), ("arm64", "ARM64")):
            result = self.run_script("Install checksum-pinned Trivy", ARCH=arch)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"trivy_0.76.1_Linux-{platform}.tar.gz", Path(self.env["CAPTURE"]).read_text())
            self.assertTrue((self.root / "extracted").exists())
            (self.root / "extracted").unlink()
        result = self.run_script("Install checksum-pinned Trivy", CHECKSUM_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "extracted").exists())
        result = self.run_script("Install checksum-pinned Trivy", ARCH="invalid")
        self.assertNotEqual(result.returncode, 0)

    def test_scan_uses_local_image_redacted_template_and_ignores_caller_policy(self) -> None:
        self.stub(
            self.root / "trivy/trivy",
            'printf "%s\\0" "$@" > "$CAPTURE"\nexit "${SCAN_STATUS:-0}"\n',
        )
        for status in (0, 1, 2):
            result = self.run_script("Scan the local image for secrets", SCAN_STATUS=str(status))
            self.assertEqual(result.returncode, status, result.stderr)
            self.assertRegex(result.stdout, rf"Secret scan \(arm64\): \d+ seconds \(exit {status}\)")
            args = Path(self.env["CAPTURE"]).read_bytes().decode().split("\0")[:-1]
            for flag, value in (("--image-src", "docker"), ("--scanners", "secret"),
                                ("--image-config-scanners", "secret"), ("--exit-code", "1"),
                                ("--config", ""), ("--ignorefile", "")):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertEqual(args[-1], self.env["IMAGE"] + ":sha-" + "a" * 40 + "-arm64")
            template = args[args.index("--template") + 1]
            for field in (".Target", ".StartLine", ".EndLine", ".RuleID", ".Severity"):
                self.assertIn(field, template)
            for field in (".Match", ".Code", ".Content", ".Text"):
                self.assertNotIn(field, template)
            policy = (self.root / "trivy-secret.yaml").read_text()
            paths = [json.loads(line.strip()[2:]) for line in policy.splitlines() if line.startswith("  - ")]
            self.assertEqual(paths, list(DEFAULT_SKIP_PATTERNS + CACHE_SKIP_PATTERNS + ROOTED_PUBLIC_CACHE_SKIP_PATTERNS + RUBY_PUBLIC_CACHE_SKIP_PATTERNS))
            self.assertNotIn("/files/", policy)
            self.assertIn("exclude-block:", policy)
            self.assertNotIn("disable-rules", policy)
            self.assertNotIn(".venv", policy)
            self.assertNotIn("--skip-dirs", args)
            self.assertNotIn("--skip-files", args)

    def test_push_targets_only_the_scanned_local_tag_and_propagates_failure(self) -> None:
        self.docker_capture()
        result = self.run_script("Push the scanned architecture image")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = Path(self.env["CAPTURE"]).read_bytes().decode().split("\0")[:-1]
        self.assertEqual(args, ["push", self.env["IMAGE"] + ":sha-" + "a" * 40 + "-arm64"])
        result = self.run_script("Push the scanned architecture image", DOCKER_STATUS="1")
        self.assertEqual(result.returncode, 1)

    def test_failed_scan_prevents_any_push(self) -> None:
        self.stub(self.root / "devcontainer-cli/node_modules/.bin/devcontainer", "exit 0\n")
        self.stub(self.root / "trivy/trivy", 'exit "$SCAN_STATUS"\n')
        self.docker_capture()
        names = ("Build the architecture image", "Scan the local image for secrets",
                 "Push the scanned architecture image")
        pipeline = "\n".join(f"(\n{script(name)})" for name in names)
        for status in (1, 2, 0):
            result = subprocess.run(
                ["bash", "-e", "-c", pipeline], env={**self.env, "SCAN_STATUS": str(status)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, status, result.stderr)
            self.assertEqual(Path(self.env["CAPTURE"]).exists(), status == 0)

    def test_wrong_native_architecture_fails_before_building(self) -> None:
        self.stub(self.root / "docker", 'printf "%s\\n" "$DOCKER_ARCH"\n')
        for requested, actual, success in (
            ("amd64", "x86_64", True), ("amd64", "amd64", True),
            ("arm64", "aarch64", True), ("arm64", "arm64", True),
            ("arm64", "x86_64", False), ("amd64", "aarch64", False),
        ):
            with self.subTest(requested=requested, actual=actual):
                result = self.run_script(
                    "Verify native Docker architecture", ARCH=requested, DOCKER_ARCH=actual,
                    PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                )
                self.assertEqual(result.returncode == 0, success, result.stderr)

    def test_merge_references_tagged_children_and_updates_both_caches(self) -> None:
        self.stub(self.root / "docker", 'printf "%s " "$@" >> "$CAPTURE"; printf "\\n" >> "$CAPTURE"\n')
        result = self.run_script(
            "Publish multi-architecture tags and cache aliases",
            PATH=str(self.root) + os.pathsep + os.environ["PATH"],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = Path(self.env["CAPTURE"]).read_text().splitlines()
        image, sha = self.env["IMAGE"], self.env["GITHUB_SHA"]
        self.assertEqual(len(calls), 5)
        self.assertIn(f"--tag {image}:main --tag {image}:sha-{sha}", calls[0])
        self.assertIn(f"{image}:sha-{sha}-amd64 {image}:sha-{sha}-arm64", calls[0])
        for arch, call in zip(("amd64", "arm64"), calls[1:3]):
            self.assertIn(f"--prefer-index=false --tag {image}:main-{arch} {image}:sha-{sha}-{arch}", call)


@unittest.skipUnless(os.environ.get("TRIVY_TEST_BINARY"), "set TRIVY_TEST_BINARY to a verified Trivy 0.76.1 binary")
class RealTrivyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root / "image"
        self.fixture.mkdir()
        body = script("Scan the local image for secrets")
        self.policy = body.split("<<'TRIVY'\n", 1)[1].split("\nTRIVY", 1)[0] + "\n"
        self.config = self.root / "policy.yaml"
        self.config.write_text(self.policy)
        self.template = next(line[len("template='"):-1] for line in body.splitlines() if line.startswith("template='"))
        self.token = "ghp_" + "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0hJ3lM6"

    def scan(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            os.environ["TRIVY_TEST_BINARY"], "fs", "--scanners", "secret", "--exit-code", "1",
            "--config", "", "--ignorefile", "", "--secret-config", str(self.config),
            "--cache-dir", str(self.root / "cache"), "--disable-telemetry", "--skip-version-check",
            "--format", "template", "--template", self.template, str(self.fixture),
        ], capture_output=True, text=True, check=False)

    def put(self, name: str, content: str) -> None:
        path = self.fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_first_party_and_uncached_venv_credentials_fail_without_leaking(self) -> None:
        names = ("app/credentials.env", ".venv/lib/python3.13/site-packages/pkg/credentials.env")
        for name in names:
            self.put(name, "TOKEN=" + self.token + "\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for name in names:
            self.assertIn(name, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_only_exact_sdk_prose_is_excluded_not_other_credentials(self) -> None:
        name = ".venv/lib/python3.12/site-packages/anthropic/resources/beta/agents/agents.py"
        prose = '\n'.join((
            'sending `{"type": "service_account"}` stores the default.',
            'send null or `{"type": "service_account"}` to restore the default.',
            'Always present; `{"type": "service_account"}` when runs carry no customer-named',
        ))
        self.put(name, prose)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.put(name, prose + '\n{"type": "service_account"}\nTOKEN=' + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("gcp-service-account", result.stdout)
        self.assertIn("github-pat", result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_package_caches_are_excluded_but_first_party_source_is_scanned(self) -> None:
        paths = (
            "mnt/devcontainer-cache/pnpm-store/v11/files/ff/" + "0" * 126,
            "mnt/devcontainer-cache/npm/_cacache/content-v2/sha512/fixture",
            "root/.cache/uv/archive-v0/fixture/pkg/credentials.env",
            "root/.cache/pip/http-v2/fixture",
            "root/.cache/mise/npm/aube/store/v1/files/fixture",
            "root/.cache/mise/downloads-cas/blobs/fixture",
        )
        self.assertEqual(len(paths), len(CACHE_SKIP_PATTERNS))
        for path in paths:
            self.put(path, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = "app/credentials.env"
        self.put(source, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(source, result.stdout)
        for path in paths:
            self.assertNotIn(path, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_repository_image_home_caches_exclude_packages_not_nearby_config(self) -> None:
        from test_repository_image_cache import CONFIG_SAMPLES, PACKAGE_SUFFIXES, cache_environment
        environment = cache_environment("Dockerfile.v2")
        for name, suffix in PACKAGE_SUFFIXES.items():
            self.put((environment[name] + suffix).lstrip("/"), "TOKEN=" + self.token)
        self.put((environment["UV_PYTHON_INSTALL_DIR"] + "/pypy-3.11.13-linux-x86_64-gnu/lib/fixture.py").lstrip("/"), self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in CONFIG_SAMPLES:
            self.put(path, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for path in CONFIG_SAMPLES:
            self.assertIn(path, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    @unittest.skipUnless(os.environ.get("PUBLIC_OPENSSL_SOURCE"), "PUBLIC_OPENSSL_SOURCE must name the verified public crate source")
    def test_public_cargo_fixture_is_skipped_without_hiding_first_party_keys(self) -> None:
        from test_repository_image_cache import cache_environment
        data = Path(os.environ["PUBLIC_OPENSSL_SOURCE"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "f5a2f708afdd360135caa7c7984d639003dec6b012534b5c538fc9ca452aaaaa")
        home = cache_environment("Dockerfile.v2").get("CARGO_HOME", "/home/vscode/.cargo")
        public = (home + "/registry/src/index.crates.io-1949cf8c6b5b557f/openssl-0.10.81/src/pkey_ctx.rs").lstrip("/")
        self.put(public, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = "workspaces/project/src/pkey_ctx.rs"
        self.put(source, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(source, result.stdout)
        self.assertNotIn(public, result.stdout)
        self.assertIn("private-key (HIGH)", result.stdout)
        self.assertNotIn("-----BEGIN ", result.stdout + result.stderr)

    def public_ruby_gem_fixtures(self) -> dict[str, str]:
        prefixes = (
            "usr/local/bundle/gems/", "usr/local/bundle/cache/",
            "usr/local/bundle/ruby/3.4.0/gems/", "usr/local/bundle/ruby/3.4.0/cache/",
            "home/vscode/.gem/gems/", "home/vscode/.gem/cache/",
            "root/.gem/gems/", "root/.gem/cache/",
            "home/vscode/.gem/ruby/3.4.0/gems/", "home/vscode/.gem/ruby/3.4.0/cache/",
            "root/.gem/ruby/3.4.0/gems/", "root/.gem/ruby/3.4.0/cache/",
            "home/vscode/.local/share/gem/ruby/3.4.0/gems/", "home/vscode/.local/share/gem/ruby/3.4.0/cache/",
            "root/.local/share/gem/ruby/3.4.0/gems/", "root/.local/share/gem/ruby/3.4.0/cache/",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/gems/",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/cache/",
            "root/.cache/app/bundle/ruby/3.4.0/gems/",
            "root/.cache/app/bundle/ruby/3.4.0/cache/",
            "workspaces/project/.bundle/ruby/3.4.0/gems/", "workspaces/project/.bundle/ruby/3.4.0/cache/",
        )
        fixtures = {(prefix + "public-package.gem" if prefix.endswith("/cache/") else
                     prefix + "public-package/lib/credential.rb"): "TOKEN=" + self.token for prefix in prefixes}
        self.assertEqual(len(fixtures), len(RUBY_PUBLIC_CACHE_SKIP_PATTERNS))
        return fixtures

    @unittest.skipUnless(os.environ.get("PUBLIC_RUBY_GEM_CLIENT"), "PUBLIC_RUBY_GEM_CLIENT must name the verified public SDK source")
    def test_public_gem_documentation_is_skipped_without_hiding_first_party_jwts(self) -> None:
        data = Path(os.environ["PUBLIC_RUBY_GEM_CLIENT"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "d7ad9595238c4f05b573ba7ab67d34dc56154caef5c7b9adffca66a6e7934651")
        public = "home/vscode/.cache/app/bundle/ruby/3.4.0/gems/aws-sdk-core-3.261.0/lib/aws-sdk-ssooidc/client.rb"
        self.put(public, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = "workspaces/project/src/client.rb"
        self.put(source, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(source, result.stdout)
        self.assertNotIn(public, result.stdout)
        self.assertEqual(result.stdout.count("jwt-token (MEDIUM)"), 4)

    def public_cache_fixtures(self) -> dict[str, str]:
        private_key = "-----BEGIN " + "PRIVATE KEY-----\n" + "YWFh" * 16 + "\n-----END PRIVATE KEY-----\n"
        stripe = "sk_live_" + "aB3dE6gH9jK2mN5pQ8sT1vW4"
        go = "go/1.26.4/src/crypto/x509/platform_root_key.pem"
        module = "golang.org/toolchain@v0.0.1-go1.26.4.linux-arm64/src/crypto/x509/platform_root_key.pem"
        bun = "bun-types@1.3.14/docs/runtime/secrets.mdx"
        return {
            "usr/local/share/mise/installs/" + go: private_key,
            "home/vscode/.local/share/mise/installs/" + go: private_key,
            "root/.local/share/mise/installs/" + go: private_key,
            "mnt/devcontainer-cache/mise/installs/" + go: private_key,
            "var/cache/devcontainer/gomod/" + module: private_key,
            "home/vscode/.cache/gomod/" + module: private_key,
            "home/vscode/.cache/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/pkg/src/fixture.rs": private_key,
            "home/vscode/.local/share/rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/fixture.pem": private_key,
            "home/vscode/.local/share/uv/python/cpython-3.14.6-linux-x86_64-gnu/lib/fixture.py": private_key,
            "mnt/devcontainer-cache/gomod/" + module: private_key,
            "home/vscode/go/pkg/mod/" + module: private_key,
            "root/go/pkg/mod/" + module: private_key,
            "home/vscode/.bun/install/cache/" + bun: stripe,
            "root/.bun/install/cache/" + bun: stripe,
            "var/cache/devcontainer/bun/" + bun: stripe,
            "mnt/devcontainer-cache/bun/" + bun: stripe,
            "home/vscode/.local/share/pnpm/store/v10/files/fixture": self.token,
            "root/.local/share/pnpm/store/v10/files/fixture": self.token,
            "var/cache/devcontainer/uv/archive-v0/pkg/credentials.env": self.token,
            "mnt/devcontainer-cache/uv/archive-v0/pkg/credentials.env": self.token,
            **self.public_ruby_gem_fixtures(),
        }

    def test_rooted_public_toolchain_and_package_caches_are_excluded(self) -> None:
        fixtures = self.public_cache_fixtures()
        self.assertEqual(len(fixtures), len(ROOTED_PUBLIC_CACHE_SKIP_PATTERNS + RUBY_PUBLIC_CACHE_SKIP_PATTERNS))
        for name, content in fixtures.items():
            self.put(name, content)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_rooted_cache_exclusions_keep_home_config_source_and_nonpackage_caches(self) -> None:
        for name, content in self.public_cache_fixtures().items():
            self.put(name, content)
        from test_repository_image_cache import CONFIG_SAMPLES
        protected = CONFIG_SAMPLES + (
            "home/vscode/.npmrc", "home/vscode/.config/mise/config.toml",
            "home/vscode/.cache/npm/.npmrc", "home/vscode/.cache/mise/config.toml",
            "home/vscode/.cache/credentials.env",
            "workspaces/project/home/vscode/.cache/gomod/credentials.env",
            "home/vscode/.bun/bunfig.toml", "home/vscode/.npm/_logs/credentials.env",
            "usr/local/share/mise/config.toml", "var/cache/devcontainer/turbo/credentials.env",
            "workspaces/project/src/credentials.ts",
            "workspaces/project/usr/local/share/mise/installs/go/credentials.env",
            "workspaces/project/var/cache/devcontainer/gomod/credentials.env",
            "workspaces/project/home/vscode/.bun/install/cache/credentials.env",
            "home/vscode/.gem/credentials", "root/.gem/credentials",
            "home/vscode/.bundle/config", "root/.bundle/config", "usr/local/bundle/config",
            "home/vscode/.gemrc", "home/vscode/.config/gem/credentials",
            "home/vscode/.gem/ruby/3.4.0/config", "usr/local/bundle/ruby/3.4.0/config",
            "home/vscode/.cache/app/bundle/install.log",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/config",
            "workspaces/project/.bundle/config", "workspaces/project/src/credentials.rb",
            "workspaces/project/.bundle/ruby/3.4.0/config",
            "workspaces/project/home/vscode/.gem/ruby/3.4.0/gems/pkg/credentials.rb",
            "workspaces/project/home/vscode/.cache/app/bundle/ruby/3.4.0/gems/pkg/credentials.rb",
            "workspaces/project/usr/local/bundle/gems/pkg/credentials.rb",
        )
        for name in protected:
            self.put(name, "TOKEN=" + self.token)
        fixtures = self.public_cache_fixtures()
        self.put("home/vscode/.ssh/id_rsa", fixtures["usr/local/share/mise/installs/go/1.26.4/src/crypto/x509/platform_root_key.pem"])
        self.put("workspaces/project/src/payment.ts", fixtures["home/vscode/.bun/install/cache/bun-types@1.3.14/docs/runtime/secrets.mdx"])
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for name in (*protected, "home/vscode/.ssh/id_rsa", "workspaces/project/src/payment.ts"):
            self.assertIn(name, result.stdout)
        for finding in ["github-pat (CRITICAL)", "private-key (HIGH)", "stripe-secret-token (CRITICAL)"]:
            self.assertIn(finding, result.stdout)
        for name in self.public_cache_fixtures():
            self.assertNotIn(name, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_package_git_cache_configs_still_detect_credentials(self) -> None:
        paths = (
            "home/vscode/.cache/cargo/git/db/private-repository/config",
            "usr/local/bundle/cache/bundler/git/private-repository/config",
            "home/vscode/.gem/ruby/3.4.0/cache/bundler/git/private-repository/config",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/cache/bundler/git/private-repository/config",
            "home/vscode/.bundle/cache/git/private-repository/config",
            "root/.bundle/cache/git/private-repository/config",
            "workspaces/project/.bundle/ruby/3.4.0/cache/bundler/git/private-repository/config",
        )
        protected = {name: '[remote "origin"]\nurl = https://' + self.token + '@github.com/org/repository.git\n' for name in paths}
        for name, content in protected.items():
            self.put(name, content)
        for result in (self.scan(), self.scan_image([], [], protected)):
            self.assertEqual(result.returncode, 1, result.stderr)
            for name in protected:
                self.assertIn(name, result.stdout)
            self.assertIn("github-pat (CRITICAL)", result.stdout)
            self.assertNotIn(self.token, result.stdout + result.stderr)

    def scan_image(self, environment: list[str], history: list[dict], files: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode="w") as contents:
            for name, content in {**self.public_cache_fixtures(), **(files or {})}.items():
                data = content.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                contents.addfile(member, io.BytesIO(data))
        image_config = json.dumps({
            "architecture": "arm64", "os": "linux",
            "config": {"Env": environment}, "history": history + [{"created_by": "COPY fixture /"}],
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer.getvalue()).hexdigest()]},
        }).encode()
        config_name = hashlib.sha256(image_config).hexdigest() + ".json"
        archive = self.root / "fixture-image.tar"
        entries = {config_name: image_config, "layer.tar": layer.getvalue(), "manifest.json": json.dumps([
            {"Config": config_name, "RepoTags": ["cache-fixture:latest"], "Layers": ["layer.tar"]}
        ]).encode()}
        with tarfile.open(archive, "w") as contents:
            for name, data in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                contents.addfile(member, io.BytesIO(data))
        return subprocess.run([
            os.environ["TRIVY_TEST_BINARY"], "image", "--input", str(archive),
            "--scanners", "secret", "--image-config-scanners", "secret", "--exit-code", "1",
            "--config", "", "--ignorefile", "", "--secret-config", str(self.config),
            "--cache-dir", str(self.root / "image-cache"), "--disable-telemetry", "--skip-version-check",
            "--format", "template", "--template", self.template,
        ], capture_output=True, text=True, check=False)

    def test_public_cache_skips_never_hide_image_environment_or_history(self) -> None:
        result = self.scan_image([], [])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for environment, history in [(["TOKEN=" + self.token], []),
                                     ([], [{"created_by": "RUN export TOKEN=" + self.token, "empty_layer": True}])]:
            result = self.scan_image(environment, history)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("github-pat", result.stdout)
            self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_default_dependency_git_and_lockfile_paths_are_excluded(self) -> None:
        paths = (
            "node_modules/pkg/credentials.env", ".git/config",
            "pnpm-lock.yaml", "package-lock.json",
        )
        for path in paths:
            self.put(path, json.dumps({"TOKEN": self.token}))
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.token, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
