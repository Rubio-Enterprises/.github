from __future__ import annotations

import base64
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
BASE_DIGEST = "sha256:" + "c" * 64
RUNNER_DIGEST = "sha256:" + "d" * 64
BASE = "ghcr.io/rubio-enterprises/base:main"
RUNNER = "ghcr.io/rubio-enterprises/runner:main"


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
        (self.repo / "Dockerfile").write_text(f"FROM {BASE} AS dev\nFROM dev AS ci\nCOPY --link --from={RUNNER} /home/runner /opt/actions-runner\n")
        self.env = {**os.environ, "RUNNER_TEMP": str(self.root), "GITHUB_WORKSPACE": str(self.repo),
                    "DOCKER_CONFIG": str(self.root / "build-service/docker-build"), "STAGING": "stage.invalid:5000/repo/run-1",
                    "STAGING_TAG": "run-1", "GITHUB_SHA": "f" * 40, "GITHUB_OUTPUT": str(self.root / "output"),
                    "GITHUB_REPOSITORY": "Rubio-Enterprises/repo", "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises", "GITHUB_ACTOR": "owner",
                    "GITHUB_SERVER_URL": "https://github.com", "REGISTRY_TOKEN": "not-a-real-secret",
                    "IMAGE": "ghcr.io/rubio-enterprises/repo/devcontainer-ci", "DIGEST": DIGEST,
                    "BASE_DIGEST": BASE_DIGEST, "RUNNER_DIGEST": RUNNER_DIGEST,
                    "REF": "stage.invalid:5000/repo/run-1/ci@" + DIGEST,
                    "PATH": str(self.root / "bin") + ":" + os.environ["PATH"], "BUILD_SERVICE_RETRY_DELAY": "0"}
        self.stub("crane", r'''echo "crane $*" >> "$RUNNER_TEMP/calls"
echo "$DOCKER_CONFIG|crane $*" >> "$RUNNER_TEMP/credentials"
if [[ "$1" = auth ]]; then
  cat > "$RUNNER_TEMP/token"
  auth=$(printf '%s:%s' "$GITHUB_ACTOR" "$(cat "$RUNNER_TEMP/token")" | base64 | tr -d '\n')
  jq --arg auth "$auth" '.auths["ghcr.io"]={auth:$auth}' "$DOCKER_CONFIG/config.json" > "$DOCKER_CONFIG/config.json.new"
  mv "$DOCKER_CONFIG/config.json.new" "$DOCKER_CONFIG/config.json"
elif [[ "$1" = copy ]]; then
  if [[ "${COPY_FAIL:-false}" = true ]]; then exit 7; fi
elif [[ "$1" = digest ]]; then
  ref="${@: -1}"
  case "$ref" in
    ghcr.io/rubio-enterprises/base*|ghcr.io/rubio-enterprises/runner*)
      if [[ "${RESOLVE_FAIL:-false}" = true ]]; then exit 8; fi ;;
  esac
  case "$ref" in
    ghcr.io/rubio-enterprises/base*) echo "$BASE_DIGEST" ;;
    ghcr.io/rubio-enterprises/runner*) echo "$RUNNER_DIGEST" ;;
    */base:*) echo "${STAGED_BASE_DIGEST:-$BASE_DIGEST}" ;;
    */runner:*) echo "${STAGED_RUNNER_DIGEST:-$RUNNER_DIGEST}" ;;
    *) echo "${REGISTRY_DIGEST:-$DIGEST}" ;;
  esac
elif [[ "$1" = config ]]; then printf '{"os":"linux","architecture":"%s"}\n' "${CONFIG_ARCH:-amd64}"; fi
''')
        self.stub("gh", 'printf "HTTP/2.0 200 OK\\n\\n%s\\n" "${VISIBILITY:-private}"\n')
        self.stub("docker", r'''echo "docker $*" >> "$RUNNER_TEMP/calls"
if [[ "$1 $2" = 'buildx build' ]]; then
  echo "$DOCKER_CONFIG" >> "$RUNNER_TEMP/build-config-paths"
  cp "$DOCKER_CONFIG/config.json" "$RUNNER_TEMP/build-config-at-contact.json"
  printf '%s' "${REGISTRY_TOKEN-unset}" > "$RUNNER_TEMP/build-token-env"
  if [[ "${BUILD_UNAVAILABLE:-false}" = true && ! -f "$RUNNER_TEMP/retried" ]]; then
    touch "$RUNNER_TEMP/retried"; echo 'code = Unavailable'; exit 6
  fi
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
                self.assertNotIn("ghcr.io", (self.root / "build-config-at-contact.json").read_text())

    def test_traversal_symlink_escape_and_non_ci_targets_are_refused_before_build(self):
        (self.repo / "outside").symlink_to(self.root)
        for overrides in ({"BUILD_CONTEXT": "../"}, {"BUILD_FILE": "/etc/passwd"}, {"BUILD_CONTEXT": "outside"}, {"BUILD_TARGET": "dev"}):
            with self.subTest(overrides=overrides):
                result = self.run_script("build", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "calls").exists())

    def test_bases_are_staged_and_verified_before_named_context_build(self):
        result = self.run_script("build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        for name, upstream, digest in (("base", BASE, BASE_DIGEST), ("runner", RUNNER, RUNNER_DIGEST)):
            staged = f"{self.env['STAGING']}/{name}:{self.env['STAGING_TAG']}"
            resolve = f"crane digest --platform linux/amd64 {upstream}"
            copy = f"crane copy {upstream}@{digest} {staged}"
            verify = f"crane digest {staged}"
            self.assertLess(calls.index(resolve), calls.index(copy))
            self.assertLess(calls.index(copy), calls.index(verify))
            self.assertLess(calls.index(verify), calls.index("docker buildx build"))
            self.assertIn(f"--build-context {upstream}=docker-image://{self.env['STAGING']}/{name}@{digest}", calls)
        self.assertIn("--output type=image,name=" + self.env["STAGING"] + "/ci:run-1,push=true,compression=zstd,compression-level=9,oci-mediatypes=true", calls)
        for removed in ("--secret", "pins=", "expected=", "source.tar"):
            self.assertNotIn(removed, calls)

    def test_token_never_reaches_generated_build_config_even_during_build(self):
        config = self.root / "build-service/docker-build/config.json"
        before = config.read_bytes()
        result = self.run_script("build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        token = self.env["REGISTRY_TOKEN"]
        encoded = base64.b64encode(f"{self.env['GITHUB_ACTOR']}:{token}".encode()).decode()
        captured = self.root / "build-config-at-contact.json"
        for path in (captured, config):
            for secret in (token, encoded):
                match = subprocess.run(["grep", "-F", "--", secret, str(path)], capture_output=True)
                self.assertEqual(match.returncode, 1, f"token placeholder leaked into {path}")
            self.assertEqual(path.read_bytes(), before)
        publish = json.loads((self.root / "build-service/docker-publish/config.json").read_text())
        self.assertEqual(publish["auths"]["ghcr.io"]["auth"], encoded)
        self.assertEqual((self.root / "token").read_text(), token)
        self.assertEqual((self.root / "build-token-env").read_text(), "unset")
        self.assertEqual((self.root / "build-config-paths").read_text().splitlines(), [str(config.parent)])
        credentials = (self.root / "credentials").read_text().splitlines()
        # Each line is "<docker config dir>|crane <args>"; keep the calls whose arguments name a
        # GHCR reference, by prefix, not by substring.
        client_calls = [
            call
            for call in credentials
            if any(arg.split("/", 1)[0] == "ghcr.io" for arg in call.split("|", 1)[1].split()[1:])
        ]
        self.assertTrue(client_calls)
        self.assertTrue(all(call.startswith(str(self.root / "build-service/docker-publish") + "|") for call in client_calls))

    def test_build_selects_staging_auth_even_if_the_inherited_config_is_publish(self):
        result = self.run_script("build", DOCKER_CONFIG=str(self.root / "build-service/docker-publish"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "build-config-paths").read_text().splitlines(),
                         [str(self.root / "build-service/docker-build")])
        self.assertNotIn("ghcr.io", (self.root / "build-config-at-contact.json").read_text())

    def test_release_pin_is_resolved_and_used_as_the_context_key(self):
        pinned = "ghcr.io/rubio-enterprises/base:v4.5.0"
        runner = "ghcr.io/rubio-enterprises/runner:v4.5.0"
        (self.repo / "image").mkdir()
        (self.repo / "image/ci.Dockerfile").write_text(
            f"# release pin: https://github.com/Rubio-Enterprises/standards/issues/1\n"
            f"  fRoM --platform=linux/amd64 {pinned} aS dev\nFROM dev AS ci\n"
            f"COPY --link --from={runner} --chown=1000:1000 /home/runner /opt/actions-runner\n")
        result = self.run_script("build", BUILD_FILE="image/ci.Dockerfile", BUILD_CONTEXT="image")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        for name, ref, digest in (("base", pinned, BASE_DIGEST), ("runner", runner, RUNNER_DIGEST)):
            self.assertIn(f"crane digest --platform linux/amd64 {ref}", calls)
            self.assertIn(f"--build-context {ref}=docker-image://{self.env['STAGING']}/{name}@{digest}", calls)
        self.assertNotIn(BASE, calls)
        self.assertNotIn(RUNNER, calls)
        self.assertIn(f"--file {self.repo}/image/ci.Dockerfile", calls)
        self.assertTrue(next(line for line in calls.splitlines() if line.startswith("docker ")).endswith(str(self.repo / "image")))

    def test_digest_pins_are_not_duplicated_when_staging(self):
        pinned = BASE + "@" + BASE_DIGEST
        (self.repo / "Dockerfile").write_text(f"FROM {pinned} AS dev\nCOPY --from={RUNNER} /home/runner /opt/actions-runner\n")
        result = self.run_script("build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertIn(f"crane copy {pinned} {self.env['STAGING']}/base:run-1", calls)
        self.assertNotIn(BASE_DIGEST + "@", calls)
        self.assertIn(f"--build-context {pinned}=docker-image://", calls)

    def test_each_build_resolves_bases_afresh(self):
        for digest in (BASE_DIGEST, OTHER):
            result = self.run_script("build", BASE_DIGEST=digest)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertEqual(calls.count(f"crane digest --platform linux/amd64 {BASE}"), 2)
        for digest in (BASE_DIGEST, OTHER):
            self.assertIn(f"--build-context {BASE}=docker-image://{self.env['STAGING']}/base@{digest}", calls)

    def test_resolution_copy_and_invalid_digest_failures_stop_before_build(self):
        for overrides in ({"RESOLVE_FAIL": "true"}, {"COPY_FAIL": "true"}, {"BASE_DIGEST": "not-a-digest"},
                          {"STAGED_BASE_DIGEST": OTHER}, {"STAGED_RUNNER_DIGEST": OTHER}):
            with self.subTest(overrides=overrides):
                result = self.run_script("build", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("docker buildx build", (self.root / "calls").read_text())
                self.assertFalse((self.root / "output").exists())

    def test_nonliteral_or_missing_base_and_runner_refs_fail_before_auth(self):
        for body in (f"ARG BASE={BASE}\nFROM ${{BASE}} AS dev\nCOPY --from={RUNNER} /a /b\n",
                     "FROM scratch AS ci\n", f"FROM {BASE} AS dev\nCOPY --from=$RUNNER /a /b\n",
                     f"FROM ghcrXio/rubio-enterprises/base:main AS dev\nCOPY --from={RUNNER} /a /b\n",
                     f"FROM ghcr.io/rubio-enterprises/base AS dev\nCOPY --from={RUNNER} /a /b\n",
                     f"FROM {BASE} AS dev\nCOPY --from={RUNNER} /a /b\nCOPY --from=ghcr.io/rubio-enterprises/runner:v1 /c /d\n"):
            with self.subTest(body=body):
                (self.repo / "Dockerfile").write_text(body)
                result = self.run_script("build")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("literal", result.stdout)
                self.assertFalse((self.root / "calls").exists())

    def test_unavailable_retry_reuses_staged_digests_and_staging_only_auth(self):
        result = self.run_script("build", BUILD_UNAVAILABLE="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        self.assertEqual(calls.count("docker buildx build"), 2)
        self.assertEqual(calls.count(f"crane digest --platform linux/amd64 {BASE}"), 1)
        self.assertEqual(calls.count(f"crane copy {BASE}@{BASE_DIGEST}"), 1)
        paths = (self.root / "build-config-paths").read_text().splitlines()
        self.assertEqual(paths, [str(self.root / "build-service/docker-build")] * 2)
        self.assertNotIn("ghcr.io", (self.root / "build-config-at-contact.json").read_text())

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
    def test_hosted_checkouts_are_writable_by_the_non_root_image_user(self):
        for filename, job in (("test-gate.yml", "canonical-tests"),
                              ("workflow-validation.yml", "validate"),
                              ("standards.yml", "standards")):
            with self.subTest(workflow=filename):
                workflow = json.loads(subprocess.check_output(["yq", "-o=json", str(ROOT / ".github/workflows" / filename)], text=True))
                steps = workflow["jobs"][job]["steps"]
                prepared = next(i for i, step in enumerate(steps)
                                if step.get("name") == "Prepare container-writable hosted checkout")
                self.assertIn("runner.environment == 'github-hosted'", steps[prepared]["if"])
                self.assertIn("runner.os == 'Linux'", steps[prepared]["if"])
                self.assertEqual(steps[prepared]["run"], 'chmod -R a+rwX "$GITHUB_WORKSPACE"')
                built = next(i for i, step in enumerate(steps) if "just build" in step.get("run", ""))
                self.assertLess(prepared, built)

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
