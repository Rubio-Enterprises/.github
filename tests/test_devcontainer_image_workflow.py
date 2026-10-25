"""Contracts and shell execution tests for the Dev Container image reusable."""

from __future__ import annotations

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
            "Build the architecture image", "Publish multi-architecture tags and cache aliases",
        ):
            self.assertNotIn("${{", script(name))


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

    def test_build_uses_cli_cache_label_and_push_only_for_publication(self) -> None:
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
                self.assertEqual("--push" in args, publish == "true")

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


if __name__ == "__main__":
    unittest.main()
