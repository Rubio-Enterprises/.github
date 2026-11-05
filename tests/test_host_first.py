from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "actions/repository-image"
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "bin").mkdir()
        (self.root / "build-service/docker-build").mkdir(parents=True)
        (self.root / "build-service/docker-publish").mkdir()
        (self.root / "build-service/docker-publish/config.json").write_text('{"auths":{"staging":{}}}')
        (self.root / "build-service/docker-build/config.json").write_text('{"auths":{"staging":{}}}')
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "Dockerfile").write_text("FROM scratch AS ci\n")
        self.env = {**os.environ, "RUNNER_TEMP": str(self.root), "GITHUB_WORKSPACE": str(self.repo),
                    "DOCKER_CONFIG": str(self.root / "build-service/docker-build"), "STAGING": "stage.invalid:5000/repo/run-1",
                    "STAGING_TAG": "run-1", "GITHUB_SHA": "f" * 40, "GITHUB_OUTPUT": str(self.root / "output"),
                    "GITHUB_REPOSITORY": "Rubio-Enterprises/repo", "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises", "GITHUB_ACTOR": "owner",
                    "GITHUB_SERVER_URL": "https://github.com", "REGISTRY_TOKEN": "not-a-real-secret",
                    "IMAGE": "ghcr.io/rubio-enterprises/repo/devcontainer-ci", "DIGEST": DIGEST,
                    "REF": "stage.invalid:5000/repo/run-1/ci@" + DIGEST,
                    "PATH": str(self.root / "bin") + ":" + os.environ["PATH"], "BUILD_SERVICE_RETRY_DELAY": "0"}
        self.stub("crane", r'''echo "crane $*" >> "$RUNNER_TEMP/calls"
if [[ "$1" = auth ]]; then
  cat > "$RUNNER_TEMP/token"
  jq '.auths["ghcr.io"]={auth:"temporary"}' "$DOCKER_CONFIG/config.json" > "$DOCKER_CONFIG/config.json.new"
  mv "$DOCKER_CONFIG/config.json.new" "$DOCKER_CONFIG/config.json"
elif [[ "$1" = digest ]]; then echo "${REGISTRY_DIGEST:-$DIGEST}"; elif [[ "$1" = config ]]; then printf '{"os":"linux","architecture":"%s"}\n' "${CONFIG_ARCH:-amd64}"; fi
''')
        self.stub("gh", 'printf "HTTP/2.0 200 OK\\n\\n%s\\n" "${VISIBILITY:-private}"\n')
        self.stub("docker", r'''echo "docker $*" >> "$RUNNER_TEMP/calls"
if [[ "$1 $2" = 'buildx build' ]]; then
  if [[ "${BUILD_FAIL:-false}" = true ]]; then exit 5; fi
  while [[ "$#" -gt 0 ]]; do
    if [[ "$1" = --metadata-file ]]; then printf '{"containerimage.digest":"%s"}' "$DIGEST" > "$2"; break; fi
    shift
  done
fi
''')

    def stub(self, name, body):
        path = self.root / "bin" / name
        path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
        path.chmod(0o755)

    def run_script(self, name, **overrides):
        return subprocess.run(["bash", str(ASSETS / (name + ".sh"))], env={**self.env, **overrides}, cwd=self.repo,
                              capture_output=True, text=True)

    def test_build_pulls_ci_amd64_and_returns_an_immutable_scannable_reference(self):
        result = self.run_script("build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        for flag in ("--pull", "--target ci", "--platform linux/amd64", "--provenance=false", "compression=zstd",
                     "org.opencontainers.image.revision=" + "f" * 40): self.assertIn(flag, calls)
        self.assertNotIn("--secret", calls)
        self.assertNotIn(self.env["REGISTRY_TOKEN"], calls)
        self.assertEqual((self.root / "output").read_text(), "ref=" + self.env["REF"] + "\n")
        self.assertEqual(json.loads((self.root / "build-service/docker-build/config.json").read_text())["auths"], {"staging": {}})

    def test_build_failures_and_digest_mismatches_never_produce_publishable_outputs(self):
        for overrides in ({"BUILD_FAIL": "true"}, {"REGISTRY_DIGEST": OTHER}):
            with self.subTest(overrides=overrides):
                result = self.run_script("build", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "output").exists())
                self.assertNotIn("ghcr.io", json.loads((self.root / "build-service/docker-build/config.json").read_text())["auths"])

    def test_traversal_symlink_escape_and_non_ci_targets_are_refused_before_build(self):
        (self.repo / "outside").symlink_to(self.root)
        for overrides in ({"BUILD_CONTEXT": "../"}, {"BUILD_FILE": "/etc/passwd"}, {"BUILD_CONTEXT": "outside"}, {"BUILD_TARGET": "dev"}):
            with self.subTest(overrides=overrides):
                result = self.run_script("build", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "calls").exists())

    def test_publish_copies_scanned_digest_before_moving_main(self):
        result = self.run_script("publish")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertIn("crane copy " + self.env["REF"], calls)
        self.assertIn("--prefer-index=true --tag " + self.env["IMAGE"] + ":main", calls)
        self.assertLess(calls.index("crane copy"), calls.index("imagetools create"))

    def test_public_destination_and_wrong_architecture_are_refused_before_any_copy(self):
        for overrides in ({"VISIBILITY": "public"}, {"CONFIG_ARCH": "arm64"}):
            with self.subTest(overrides=overrides):
                result = self.run_script("publish", **overrides)
                self.assertNotEqual(result.returncode, 0)
                calls = self.root / "calls"
                if calls.exists(): self.assertNotIn("crane copy", calls.read_text())

    def test_mismatched_copy_cannot_move_main(self):
        result = self.run_script("publish", REGISTRY_DIGEST=OTHER)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("imagetools create", (self.root / "calls").read_text())

    def test_scan_failure_is_redacted_and_cleans_up_its_transient_results(self):
        import io
        import tarfile
        for status in (0, 1):
            with self.subTest(status=status):
                body = ('#!/bin/sh\necho sensitive-fixture-output\nexit ' + str(status) + '\n').encode()
                archive = self.root / "scanner.tgz"
                with tarfile.open(archive, "w:gz") as tar:
                    member = tarfile.TarInfo("trivy")
                    member.mode = 0o755
                    member.size = len(body)
                    tar.addfile(member, io.BytesIO(body))
                self.stub("curl", 'while [[ "$1" != -o ]]; do shift; done; cp "$RUNNER_TEMP/scanner.tgz" "$2"\n')
                self.stub("sha256sum", 'cat >/dev/null\n')
                result = self.run_script("scan")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertNotIn("sensitive-fixture-output", result.stdout + result.stderr)
                self.assertFalse((self.root / "ci-scan").exists())


class Contracts(unittest.TestCase):
    def test_credentialed_ci_builder_runs_only_central_actions(self):
        workflow = (ROOT / ".github/workflows/repository-image.yml").read_text()
        self.assertNotIn("GITHUB_ACTION_PATH", workflow)
        self.assertNotIn("standards_package", workflow)
        self.assertNotIn("docker run", workflow)
        self.assertNotIn("prepare:", workflow)
        self.assertIn("github.event.repository.private == true", workflow)
        self.assertIn("operation: scan", workflow)
        self.assertLess(workflow.index("operation: scan"), workflow.index("operation: publish"))
        self.assertIn("operation: connect", workflow)
        self.assertIn("if: always()", workflow)
        self.assertEqual(workflow.count("id-token: write"), 1)

    def test_check_pr_is_not_lost_when_tree_checks_fail(self):
        workflow = (ROOT / ".github/workflows/standards.yml").read_text()
        self.assertIn("steps['check-ci'].outcome != 'skipped'", workflow)
        self.assertIn("steps['check-hosted'].outcome != 'skipped'", workflow)
        self.assertIn("!cancelled()", workflow)

    def test_bootstrap_compose_uses_host_paths_external_caches_and_no_image_symlinks(self):
        compose = (ROOT / "compose.yaml").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("${REPO_DIR}:${REPO_DIR}", compose)
        self.assertIn("${GIT_COMMON_DIR}:${GIT_COMMON_DIR}", compose)
        self.assertIn("pull_policy: missing", compose)
        self.assertNotIn("ln -s", dockerfile)
        self.assertIn("mise install --locked", dockerfile)
        self.assertIn("/etc/mise/mise.lock", dockerfile)
        for line in (ROOT / "justfile").read_text().splitlines():
            if "docker compose run" in line and "dev bash" not in line:
                self.assertIn("-T", line)
                self.assertIn("</dev/null", line)

    def test_fleet_dispatch_reads_canonical_inventory_and_fails_after_dispatch_errors(self):
        workflow = (ROOT / ".github/workflows/fleet-ci-image-rebuild.yml").read_text()
        self.assertIn("mac-dev-playbook/contents/cue/incus/ci-image-inventory.json", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("GH_DISPATCH_TOKEN", workflow)
        self.assertIn("permission-actions: write", workflow)
        body = workflow.split("      - name: Dispatch admitted repositories at a paced rate", 1)[1]
        body = textwrap.dedent(body.split("        run: |\n", 1)[1])
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "ci-image-inventory.json").write_text(json.dumps({"entries": [{"repository": "Rubio-Enterprises/one"}, {"repository": "Rubio-Enterprises/one"}, {"repository": "Rubio-Enterprises/two"}]}))
                for name, script in {"gh": '#!/bin/sh\necho "$*" >> "$RUNNER_TEMP/calls"\nexit ' + str(int(fail)) + '\n', "sleep": '#!/bin/sh\necho "$*" >> "$RUNNER_TEMP/sleeps"\n'}.items():
                    p = root / name; p.write_text(script); p.chmod(0o755)
                result = subprocess.run(["bash", "-euc", body], env={**os.environ, "RUNNER_TEMP": str(root), "PATH": str(root) + ":" + os.environ["PATH"]}, capture_output=True, text=True)
                self.assertEqual(result.returncode != 0, fail, result.stderr)
                self.assertEqual(len((root / "calls").read_text().splitlines()), 2)
                self.assertEqual((root / "sleeps").read_text().splitlines(), ["15", "15"])
