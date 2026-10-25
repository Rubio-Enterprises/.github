"""Contracts and shell execution tests for the Dev Container image reusable."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/devcontainer-image.yml").read_text(encoding="utf-8")


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
        self.assertNotIn("secrets:", TEXT)

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

    def test_pins_checkout_and_registry_hygiene(self) -> None:
        for ref, comment in re.findall(r"uses: (\S+)(.*)", TEXT):
            self.assertRegex(ref, r"@[a-f0-9]{40}$")
            self.assertRegex(comment, r" # v[0-9]")
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

    def test_build_loads_local_image_and_never_pushes(self) -> None:
        cli = self.root / "devcontainer-cli/node_modules/.bin/devcontainer"
        self.stub(cli, 'printf "%s\\0" "$@" > "$CAPTURE"\n')
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

    def configuration(self, build: dict | None) -> None:
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
                str(self.root / "workspace"),
            ])

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
                                ("--exit-code", "1"), ("--config", ""), ("--ignorefile", "")):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertEqual(args[-1], self.env["IMAGE"] + ":sha-" + "a" * 40 + "-arm64")
            template = args[args.index("--template") + 1]
            for field in (".Target", ".StartLine", ".EndLine", ".RuleID", ".Severity"):
                self.assertIn(field, template)
            for field in (".Match", ".Code", ".Content", ".Text"):
                self.assertNotIn(field, template)
            policy = (self.root / "trivy-secret.yaml").read_text()
            paths = [line.strip()[2:] for line in policy.splitlines() if line.startswith("  - ")]
            self.assertEqual(len(paths), 41)
            for path in paths:
                self.assertRegex(path, r"^mnt/devcontainer-cache/pnpm-store/v11/files/[0-9a-f]{2}/[0-9a-f]{126}$")
            self.assertIn("exclude-block:", policy)
            self.assertNotIn("disable-rules", policy)
            self.assertNotIn("node_modules", policy)
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

    def test_source_and_dependency_credentials_fail_without_leaking(self) -> None:
        names = ("app/credentials.env", "node_modules/pkg/credentials.env", ".venv/lib/pkg/credentials.env")
        for name in names:
            self.put(name, "TOKEN=" + self.token + "\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for name in names:
            self.assertIn(name, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_only_exact_sdk_prose_is_excluded_not_other_credentials(self) -> None:
        name = "root/.cache/uv/archive-v0/random/anthropic/resources/beta/agents/agents.py"
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

    def test_only_listed_pnpm_object_paths_are_excluded(self) -> None:
        paths = [line.strip()[2:] for line in self.policy.splitlines() if line.startswith("  - ")]
        for path in paths:
            self.put(path, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        neighbor = "mnt/devcontainer-cache/pnpm-store/v11/files/ff/" + "0" * 126
        self.put(neighbor, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(neighbor, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
