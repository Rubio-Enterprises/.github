from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The Trivy release actions/trivy/install.sh pins; Renovate moves it, so stubs follow it
TRIVY_VERSION = re.search(r'^image=\S+:([^:@\s]+)@', (ROOT / "actions/trivy/install.sh").read_text(), re.M)[1]
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
                    "BASE_DIGEST": BASE_DIGEST, "RUNNER_DIGEST": RUNNER_DIGEST, "COPY_EXISTING": OTHER,
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
  if [[ "$2" = "$REF" ]]; then
    printf '2026/11/22 15:00:00 existing blob: %s\n2026/11/22 15:00:00 pushed blob: %s\n2026/11/22 15:00:01 pushed blob: %s\n' "$COPY_EXISTING" "$RUNNER_DIGEST" "$DIGEST" >&2
  fi
elif [[ "$1" = manifest ]]; then
  case "$2" in
    "ghcr.io/rubio-enterprises/base@$BASE_DIGEST") printf '{"layers":[{"digest":"%s","size":314159}]}\n' "$BASE_DIGEST" ;;
    "$REF") printf '{"config":{"digest":"%s","size":19},"layers":[{"digest":"%s","size":9000},{"digest":"%s","size":2048}]}\n' "$DIGEST" "$COPY_EXISTING" "$RUNNER_DIGEST" ;;
    *) exit 9 ;;
  esac
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
elif [[ "$1" = config ]]; then printf '{"os":"linux","architecture":"%s","config":{"Labels":{"org.opencontainers.image.base.digest":"%s"}}}\n' "${CONFIG_ARCH:-amd64}" "$BASE_DIGEST"; fi
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

    def publish_built_image(self):
        ref = (self.root / "output").read_text().strip().removeprefix("ref=")
        result = self.run_script("publish", REF=ref)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
        self.publish_built_image()

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
        self.assertIn("--label org.opencontainers.image.base.name=" + BASE, calls)
        self.assertIn("--label org.opencontainers.image.base.digest=" + BASE_DIGEST, calls)
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
        self.publish_built_image()

    def test_digest_pins_are_not_duplicated_when_staging(self):
        pinned = BASE + "@" + OTHER
        runner = RUNNER + "@" + RUNNER_DIGEST
        (self.repo / "Dockerfile").write_text(
            f"FROM {pinned} AS dev\nFROM dev AS ci\nCOPY --from={runner} /home/runner /opt/actions-runner\n")
        result = self.run_script("build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = (self.root / "calls").read_text()
        for name, ref, digest in (("base", pinned, BASE_DIGEST), ("runner", runner, RUNNER_DIGEST)):
            self.assertIn(f"crane digest --platform linux/amd64 {ref}", calls)
            self.assertIn(f"crane copy {ref.split('@')[0]}@{digest} {self.env['STAGING']}/{name}:run-1", calls)
            self.assertIn(f"--build-context {ref}=docker-image://{self.env['STAGING']}/{name}@{digest}", calls)
        self.assertIn(f"--label org.opencontainers.image.base.name={pinned}", calls)
        self.assertIn(f"--label org.opencontainers.image.base.digest={BASE_DIGEST}", calls)
        self.publish_built_image()

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
        copy = "crane copy " + self.env["REF"]
        self.assertLess(calls.index(f"crane manifest {self.env['REF']}"), calls.index(copy))
        self.assertLess(calls.index(f"crane manifest ghcr.io/rubio-enterprises/base@{BASE_DIGEST}"), calls.index(copy))
        self.assertIn("--prefer-index=true --tag " + self.env["IMAGE"] + ":main", calls)
        self.assertLess(calls.index(copy), calls.index("imagetools create"))
        self.assertIn("Uploaded 2067 compressed blob bytes", result.stdout)
        self.assertEqual(calls.splitlines()[-1], f"crane digest --platform linux/amd64 {self.env['IMAGE']}:main")

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
        self.stub("uname", 'case "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        for status in (0, 1):
            with self.subTest(status=status):
                self.stub("trivy", f'if [[ "$1" == --version ]]; then echo "Version: {TRIVY_VERSION}"; exit 0; fi\necho sensitive-fixture-output\nexit ' + str(status) + '\n')
                result = self.run_script("scan")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertNotIn("sensitive-fixture-output", result.stdout + result.stderr)
                self.assertFalse((self.root / "ci-scan").exists())


class Contracts(unittest.TestCase):
    def test_hosted_checkouts_are_writable_by_the_non_root_image_user(self):
        for filename, job in (("bootstrap-test-gate.yml", "canonical-tests"),
                              ("workflow-validation.yml", "validate"),
                              ("standards.yml", "standards-linux")):
            with self.subTest(workflow=filename):
                workflow = json.loads(subprocess.check_output(["yq", "-o=json", str(ROOT / ".github/workflows" / filename)], text=True))
                steps = workflow["jobs"][job]["steps"]
                prepared = next(i for i, step in enumerate(steps)
                                if step.get("name") == "Prepare container-writable hosted checkout")
                self.assertIn("runner.environment == 'github-hosted'", steps[prepared]["if"])
                self.assertIn("runner.os == 'Linux'", steps[prepared]["if"])
                self.assertEqual(steps[prepared]["run"], 'chmod -R a+rwX "$GITHUB_WORKSPACE"')
                built = next(i for i, step in enumerate(steps) if "just build-dev" in step.get("run", ""))
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

    def test_registry_prune_is_the_last_non_blocking_build_step(self):
        jobs = json.loads(subprocess.check_output(
            ["yq", "-o=json", str(ROOT / ".github/workflows/repository-image.yml")], text=True))["jobs"]
        self.assertNotIn("prune", jobs)
        build = jobs["build"]
        steps = build["steps"]
        prune = steps[-1]
        self.assertTrue(prune["uses"].startswith("actions/delete-package-versions@"))
        self.assertEqual(prune["with"], {
            "owner": "${{ github.repository_owner }}",
            "package-name": "${{ steps.resolve.outputs.package }}",
            "package-type": "container",
            "delete-only-untagged-versions": "true",
            "min-versions-to-keep": 5,
        })
        self.assertEqual(build["permissions"]["packages"], "write")
        self.assertTrue(prune["continue-on-error"])
        self.assertNotIn("if", prune)
        self.assertEqual(prune["timeout-minutes"], 10)
        self.assertEqual(build["timeout-minutes"], 45 + prune["timeout-minutes"])
        publish = next(i for i, step in enumerate(steps) if step.get("with", {}).get("operation") == "publish")
        self.assertNotIn("continue-on-error", steps[publish])
        self.assertEqual(steps[-2]["name"], "Remove job credentials")
        self.assertEqual(jobs["verify"]["needs"], "build")
        self.assertIn("needs.build.result == 'success'", jobs["verify"]["if"])

    def test_check_pr_is_not_lost_when_tree_checks_fail(self):
        workflow = (ROOT / ".github/workflows/standards.yml").read_text()
        self.assertIn("steps['check-ci'].outcome != 'skipped'", workflow)
        self.assertIn("steps['check-hosted'].outcome != 'skipped'", workflow)
        self.assertIn("!cancelled()", workflow)

    def test_hosted_context_is_token_free_and_check_pr_reuses_it_with_summary_and_status(self):
        workflow = json.loads(subprocess.check_output(
            ["yq", "-o=json", str(ROOT / ".github/workflows/standards.yml")], text=True))
        steps = workflow["jobs"]["standards-linux"]["steps"]
        writer = next(step for step in steps if step.get("id") == "context-hosted")
        step = next(step for step in steps if step.get("id") == "check-pr-hosted")
        for status in (0, 7):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                event = root / "runner-event.json"
                event.write_text('{"pull_request":{"number":889}}')
                expected = {
                    "GITHUB_WORKFLOW": "standards", "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_EVENT_PATH": str(root / ".cache/ci/event.json"),
                    "GITHUB_REPOSITORY": "Rubio-Enterprises/standards", "GITHUB_RUN_ID": "123",
                    "GITHUB_SERVER_URL": "https://github.com",
                    "GITHUB_STEP_SUMMARY": str(root / ".cache/ci/summary.md"),
                    "BASE_SHA": "a" * 40, "HEAD_SHA": "b" * 40,
                    "PR_TITLE": "fix: keep '\" and $(touch injected)\na new line",
                    "CI": "true", "XDG_CACHE_HOME": str(root / ".cache"),
                    "TRIVY_CACHE_DIR": str(root / ".cache/trivy"),
                    "TRIVY_SKIP_DB_UPDATE": "true",
                }
                just = root / "just"
                just.write_text("#!/bin/sh\nset -eu\n"
                                'test "$1" = check-pr\n'
                                'test -f "$GITHUB_WORKSPACE/.cache/ci/context.json"\n'
                                'echo summary >"$GITHUB_STEP_SUMMARY"\n'
                                f"exit {status}\n")
                just.chmod(0o755)
                env = {**os.environ, **expected, "GITHUB_WORKSPACE": str(root),
                       "GITHUB_EVENT_PATH": str(event), "GITHUB_STEP_SUMMARY": str(root / "summary"),
                       "GH_TOKEN": "not-a-token", "GITHUB_TOKEN": "not-a-token",
                       "PATH": str(root) + ":" + os.environ["PATH"]}
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                written = subprocess.run(["bash", "-euc", writer["run"]], env=env, cwd=root,
                                         capture_output=True, text=True)
                self.assertEqual(written.returncode, 0, written.stderr)
                context = root / ".cache/ci/context.json"
                before = context.read_bytes()
                result = subprocess.run(["bash", "-euc", step["run"]], env=env, cwd=root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(context.read_bytes(), before)
                self.assertEqual(json.loads(before), expected)
                self.assertNotIn("not-a-token", context.read_text())
                self.assertEqual((root / ".cache/ci/event.json").read_bytes(), event.read_bytes())
                self.assertEqual((root / "summary").read_text(), "summary\n")
                self.assertFalse((root / "injected").exists())

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

    def test_generate_locks_in_the_pinned_base_before_building_dev(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "justfile").write_bytes((ROOT / "justfile").read_bytes())
            base = f"{BASE}@{BASE_DIGEST}"
            (repo / "Dockerfile").write_text(f"FROM {base} AS dev\n")
            docker = repo / "docker"
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> docker-calls\n')
            docker.chmod(0o755)
            subprocess.run(
                ["just", "generate"], cwd=repo, check=True,
                env={**os.environ, "PATH": f"{repo}:{os.environ['PATH']}"},
            )
            self.assertEqual((repo / "docker-calls").read_text().splitlines(), [
                f"run --rm -v {repo}:{repo} -w {repo} -e MISE_GITHUB_TOKEN -e GH_TOKEN {base} standards lock",
                "volume create rubio-uv-cache",
                "volume create rubio-npm-cache",
                "compose build --pull dev",
                "compose run --rm -T dev standards generate",
            ])
