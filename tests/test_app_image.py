from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/app-image"
WORKFLOW = ROOT / ".github/workflows/app-image.yml"
TEXT = WORKFLOW.read_text()
DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
IMAGE = "ghcr.io/rubio-enterprises/example"
STAGING = "stage.example.invalid:5000/rubio-enterprises/repo"
TOKEN = "ghs_secret_token_value"


def steps(name: str) -> str:
    """The text of one workflow step, from its name to the next step."""
    return TEXT.split(f"- name: {name}\n", 1)[1].split("\n      - name:", 1)[0]


class WorkflowShape(unittest.TestCase):
    def test_job_is_a_client_on_the_image_route_with_the_permissions_the_callers_must_grant(self):
        self.assertIn("permissions: {}", TEXT)
        self.assertIn("permissions:\n      contents: read\n      packages: write\n      id-token: write\n", TEXT)
        self.assertEqual(TEXT.count("id-token: write"), 1)
        self.assertNotIn("attestations: write", TEXT)
        self.assertIn("runs-on: ${{ fromJSON(vars.RUNNERS || '{}').image || fromJSON('[\"linux-x64-image\"]') }}", TEXT)
        self.assertNotIn("ubuntu", TEXT)
        self.assertIn("timeout-minutes: ${{ inputs.timeout-minutes }}", TEXT)
        self.assertIn('BUILDX_NO_DEFAULT_ATTESTATIONS: "1"', TEXT)
        for forbidden in ("docker/login-action", "docker/setup-buildx-action", "docker/build-push-action", "docker/setup-qemu-action",
                          "docker run", "docker pull", "docker push", "--insecure", "--cache-from", "--cache-to", "type=gha",
                          "continue-on-error", "pull_request_target", "workflow_run", "secrets:", "platforms"):
            self.assertNotIn(forbidden, TEXT)

    def test_third_party_actions_are_pinned_and_first_party_ones_follow_main(self):
        for ref in re.findall(r"uses: (\S+)", TEXT):
            if ref.startswith("Rubio-Enterprises/.github/actions/"):
                self.assertIn(ref, ("Rubio-Enterprises/.github/actions/repository-image@main", "Rubio-Enterprises/.github/actions/app-image@main"))
            else:
                self.assertRegex(ref, r"@[a-f0-9]{40}$")

    def test_steps_run_in_the_order_that_keeps_credentials_scoped(self):
        names = ["Resolve the image and the build mode", "Check out the caller", "Set up Node", "Install the Standards Package into .standards",
                 "Install the Build Service client tools", "Compute image tags and labels", "Connect to the Build Service",
                 "Build the image on the Build Service", "Copy the verified image to GHCR", "Prune stale candidate images",
                 "Remove the job's certificate, credentials and build inputs"]
        positions = [TEXT.index(f"- name: {name}\n") for name in names]
        self.assertEqual(positions, sorted(positions))
        # Repository tasks run before the certificate exists; only the copy step sees the GHCR token and only the prune the API token
        self.assertLess(TEXT.index("- name: Install the Standards Package into .standards"), TEXT.index("- name: Connect to the Build Service"))
        self.assertEqual(TEXT.count("github.token"), 3)  # standards install, copy, prune
        self.assertIn("REGISTRY_TOKEN: ${{ github.token }}", steps("Copy the verified image to GHCR"))
        self.assertNotIn("github.token", steps("Build the image on the Build Service"))
        self.assertIn("GH_TOKEN: ${{ github.token }}", steps("Prune stale candidate images"))
        self.assertIn("if: always()", TEXT.split("- name: Remove the job's certificate", 1)[1])
        self.assertIn("steps.resolve.outputs.mode != 'check'", steps("Copy the verified image to GHCR"))
        self.assertIn("steps.resolve.outputs.mode == 'candidate'", steps("Prune stale candidate images"))

    def test_caller_text_reaches_scripts_only_through_the_environment(self):
        for name in ("Resolve the image and the build mode", "Remove the job's certificate, credentials and build inputs",
                     "Install the Standards Package into .standards"):
            run = steps(name).split("run:", 1)[1]
            self.assertNotIn("${{", run)

    def test_the_standards_install_is_opt_in_and_runs_mise(self):
        for name in ("Set up uv for mise", "Set up mise", "Install the Standards Package into .standards"):
            self.assertIn("if: inputs.standards-install", steps(name))
        self.assertIn("mise install node && mise run standards:install", steps("Install the Standards Package into .standards"))
        self.assertIn("cache: false", steps("Set up mise"))

    def test_publish_and_candidate_are_exclusive_and_publish_needs_tags(self):
        resolve = steps("Resolve the image and the build mode")
        self.assertIn('"$PUBLISH" == true && "$CANDIDATE" == true', resolve)
        self.assertIn("publish needs the tags input", resolve)
        self.assertIn("tags: ${{ inputs.publish && steps.meta.outputs.tags || '' }}", TEXT)

    def test_the_action_dispatches_only_known_operations_and_reuses_the_connect_script(self):
        action = (ACTION / "action.yml").read_text()
        self.assertIn('exec bash "$GITHUB_ACTION_PATH/../repository-image/connect.sh"', action)
        self.assertIn("build | publish | prune)", action)
        self.assertIn("OPERATION: ${{ inputs.operation }}", action)
        self.assertNotIn("${{", action.split("run: |", 1)[1])
        self.assertTrue((ROOT / "actions/repository-image/connect.sh").is_file())


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.workspace = self.root / "workspace"
        (self.workspace / "sub").mkdir(parents=True)
        (self.workspace / "Dockerfile").write_text("FROM scratch\n")
        (self.workspace / "sub/Dockerfile").write_text("FROM scratch\n")
        (self.root / "runner").mkdir()
        self.output = self.root / "output"
        self.output.touch()
        self.summary = self.root / "summary"
        self.env = {
            **os.environ, "RUNNER_TEMP": str(self.root / "runner"), "GITHUB_WORKSPACE": str(self.workspace),
            "GITHUB_OUTPUT": str(self.output), "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises", "GITHUB_REPOSITORY": "Rubio-Enterprises/Repo",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_ACTOR": "octocat",
            "STAGING": STAGING, "STAGING_TAG": "run-123-2", "BUILDX_BUILDER": "build-service", "BUILD_SERVICE_RETRY_DELAY": "0",
            "IMAGE": IMAGE, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
        }
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "REGISTRY_TOKEN"):
            self.env.pop(key, None)

    def stub(self, name: str, body: str) -> None:
        target = self.bin / name
        target.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
        target.chmod(0o755)

    @property
    def temp(self) -> Path:
        return self.root / "runner"

    def calls(self, name: str) -> list[str]:
        file = self.temp / name
        return file.read_text().splitlines() if file.exists() else []

    def outputs(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines())

    def run_script(self, script: str, **overrides):
        env = {**self.env, **{key: value for key, value in overrides.items() if value is not None}}
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
        return subprocess.run(["bash", str(ACTION / script)], env=env, capture_output=True, text=True)


class Build(Workspace):
    def setUp(self):
        super().setUp()
        self.env["MODE"] = "check"
        # docker records its line, fails on request, and writes the metadata file the build asked for
        self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
count="$(cat "$RUNNER_TEMP/docker-count" 2>/dev/null || echo 0)"; echo $((count + 1)) > "$RUNNER_TEMP/docker-count"
if (( count < ${DOCKER_FAILURES:-0} )); then echo "${DOCKER_ERROR:-rpc error: code = Unavailable desc = graceful_stop}" >&2; exit 1; fi
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" = --metadata-file ]]; then printf '{"containerimage.digest": "%s"}' "${BUILT_DIGEST:-''' + DIGEST + '''}" > "${args[i + 1]}"; fi
done
''')
        self.stub("crane", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/crane-calls"
if [[ "$1" = digest ]]; then printf '%s\\n' "${STAGED_DIGEST:-''' + DIGEST + '''}"; fi
''')
        self.stub("sleep", "exit 0\n")

    def build(self, **overrides):
        return self.run_script("build.sh", **overrides)

    def command(self) -> str:
        return self.calls("docker-calls")[-1]

    def test_check_builds_amd64_without_attestations_into_a_per_package_staging_name(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        command = self.command()
        self.assertIn("buildx build --platform linux/amd64 --file " + str(self.workspace / "Dockerfile"), command)
        self.assertIn("--provenance=false --sbom=false", command)
        self.assertIn(f"--output type=image,name={STAGING}/example:run-123-2-check,push=true {self.workspace}", command)
        self.assertEqual(self.outputs(), {"digest": DIGEST})
        self.assertEqual((self.root / "runner/app-image/digest").read_text().strip(), DIGEST)

    def test_publish_adds_max_provenance_and_an_sbom_unless_turned_off(self):
        self.assertEqual(self.build(MODE="publish").returncode, 0)
        self.assertIn("--provenance=mode=max --sbom=true", self.command())
        self.assertIn("run-123-2-publish,push=true", self.command())
        self.assertEqual(self.build(MODE="publish", ATTESTATIONS="false").returncode, 0)
        self.assertIn("--provenance=false --sbom=false", self.command())

    def test_a_candidate_is_a_single_manifest_whatever_attestations_says(self):
        self.assertEqual(self.build(MODE="candidate", ATTESTATIONS="true").returncode, 0)
        self.assertIn("--provenance=false --sbom=false", self.command())
        self.assertIn("run-123-2-candidate,push=true", self.command())

    def test_context_file_target_arguments_and_labels_reach_the_build(self):
        result = self.build(BUILD_CONTEXT="sub", BUILD_FILE="sub/Dockerfile", BUILD_TARGET="aio",
                            BUILD_ARGS="SERVER_VERSION=1.2.3\n\n# a comment\r\nTWO=has spaces = and equals\n",
                            BUILD_LABELS="org.opencontainers.image.source=https://example.invalid/x\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        command = self.command()
        self.assertIn(f"--file {self.workspace}/sub/Dockerfile --target aio", command)
        self.assertIn("--build-arg SERVER_VERSION=1.2.3 --build-arg TWO=has spaces = and equals", command)
        self.assertIn("--label org.opencontainers.image.source=https://example.invalid/x", command)
        self.assertTrue(command.endswith(f"{self.workspace}/sub"))

    def test_the_file_defaults_to_the_contexts_dockerfile(self):
        self.assertEqual(self.build(BUILD_CONTEXT="sub").returncode, 0)
        self.assertIn(f"--file {self.workspace}/sub/Dockerfile", self.command())

    def test_paths_and_arguments_that_escape_or_malform_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "Dockerfile").write_text("FROM scratch\n")
        (self.workspace / "link").symlink_to(outside)
        (self.workspace / "linked-file").symlink_to(outside / "Dockerfile")
        for name, overrides, message in [
            ("parent context", {"BUILD_CONTEXT": "../x"}, "context must be a path inside the repository"),
            ("absolute context", {"BUILD_CONTEXT": "/etc"}, "context must be a path inside the repository"),
            ("nested parent", {"BUILD_CONTEXT": "sub/../.."}, "context must be a path inside the repository"),
            ("absolute file", {"BUILD_FILE": "/etc/passwd"}, "file must be a path inside the repository"),
            ("missing context", {"BUILD_CONTEXT": "nope"}, "is not a directory"),
            ("missing file", {"BUILD_FILE": "nope"}, "does not exist"),
            ("symlinked context", {"BUILD_CONTEXT": "link"}, "resolves outside the repository"),
            ("symlinked file", {"BUILD_FILE": "linked-file"}, "resolves outside the repository"),
            ("bad argument", {"BUILD_ARGS": "NOT AN ARG"}, "NAME=value"),
            ("bad label", {"BUILD_LABELS": "novalue"}, "key=value"),
            ("bad mode", {"MODE": "push"}, "MODE must be"),
            ("foreign image", {"IMAGE": "ghcr.io/other/example"}, "namespace"),
            ("tagged image", {"IMAGE": f"{IMAGE}:latest"}, "valid package name"),
            ("hub image", {"IMAGE": "docker.io/rubio-enterprises/example"}, "namespace"),
        ]:
            with self.subTest(name):
                result = self.build(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout + result.stderr)
        self.assertEqual(self.calls("docker-calls"), [])

    def test_a_staged_image_that_differs_from_the_build_result_fails_the_step(self):
        result = self.build(STAGED_DIGEST=OTHER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staged image differs", result.stdout)
        self.assertEqual(self.output.read_text(), "")

    def test_a_build_without_a_digest_fails(self):
        result = self.build(BUILT_DIGEST="not-a-digest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not report an image digest", result.stdout)

    def test_one_retry_when_the_daemon_restarts_and_none_for_a_real_failure(self):
        retried = self.build(DOCKER_FAILURES="1")
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(len(self.calls("docker-calls")), 2)
        self.assertIn("retrying once", retried.stdout)
        shutil.rmtree(self.root / "runner")
        (self.root / "runner").mkdir()
        failed = self.build(DOCKER_FAILURES="1", DOCKER_ERROR="ERROR: failed to solve: process did not complete successfully")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(len(self.calls("docker-calls")), 1)
        shutil.rmtree(self.root / "runner")
        (self.root / "runner").mkdir()
        down = self.build(DOCKER_FAILURES="2")
        self.assertNotEqual(down.returncode, 0)
        self.assertEqual(len(self.calls("docker-calls")), 2)


class Publish(Workspace):
    def setUp(self):
        super().setUp()
        self.env.update(MODE="publish", REGISTRY_TOKEN=TOKEN,
                        TAGS=f"{IMAGE}:1.2.3\n{IMAGE}:1.2\n{IMAGE}:sha-abc1234\n")
        state = self.root / "runner/app-image"
        state.mkdir()
        (state / "digest").write_text(DIGEST + "\n")
        (state / "staging.ref").write_text(f"{STAGING}/example:run-123-2-publish\n")
        self.stub("crane", '''printf '%s|%s\\n' "${DOCKER_CONFIG:-}" "$*" >> "$RUNNER_TEMP/crane-calls"
case "$1" in
  auth) cat > "$RUNNER_TEMP/crane-stdin" ;;
  digest) printf '%s\\n' "${PUBLISHED_DIGEST:-''' + DIGEST + '''}" ;;
esac
''')
        # imagetools is the copy; the builder must not be selected for it
        self.stub("docker", '''printf '%s|%s|%s\\n' "${DOCKER_CONFIG:-}" "${BUILDX_BUILDER-unset}" "$*" >> "$RUNNER_TEMP/docker-calls"
exit "${IMAGETOOLS_STATUS:-0}"
''')

    def publish(self, **overrides):
        return self.run_script("publish.sh", **overrides)

    def test_copies_the_staged_digest_to_every_final_tag_with_one_imagetools_call_and_verifies_each(self):
        result = self.publish(BUILDX_BUILDER="build-service")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = f"{self.root}/runner/build-service/docker-publish"
        staged = f"{STAGING}/example:run-123-2-publish@{DIGEST}"
        self.assertEqual(self.calls("crane-calls")[0], f"{config}|auth login ghcr.io --username octocat --password-stdin")
        self.assertEqual(self.calls("docker-calls"), [
            f"{config}|unset|buildx imagetools create --tag {IMAGE}:1.2.3 --tag {IMAGE}:1.2 --tag {IMAGE}:sha-abc1234 {staged}"])
        for tag in ("1.2.3", "1.2", "sha-abc1234"):
            self.assertIn(f"{config}|digest {IMAGE}:{tag}", self.calls("crane-calls"))
        self.assertFalse([call for call in self.calls("crane-calls") if "|copy" in call])  # crane copy adds referrers tags
        self.assertEqual((self.temp / "crane-stdin").read_text(), TOKEN)
        self.assertEqual(self.outputs(), {"digest": DIGEST, "image": f"{IMAGE}@{DIGEST}"})
        self.assertIn(f"{IMAGE}:1.2.3@{DIGEST}", self.summary.read_text())

    def test_the_token_never_appears_in_a_command_line_or_the_output(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, "\n".join(self.calls("crane-calls") + self.calls("docker-calls")))
        self.assertNotIn(TOKEN, result.stdout + result.stderr + self.output.read_text() + self.summary.read_text())

    def test_a_candidate_gets_one_temporary_tag_and_no_other(self):
        result = self.publish(MODE="candidate", TAGS="")
        self.assertEqual(result.returncode, 0, result.stderr)
        copies = self.calls("docker-calls")
        self.assertEqual(len(copies), 1)
        self.assertIn(f"imagetools create --tag {IMAGE}:candidate-123-2 {STAGING}/example:run-123-2-publish@{DIGEST}", copies[0])
        self.assertEqual(self.outputs()["image"], f"{IMAGE}@{DIGEST}")

    def test_tags_outside_the_image_or_malformed_publish_nothing(self):
        for name, tags in [("other repository", f"{IMAGE}:ok\nghcr.io/rubio-enterprises/other:ok"),
                           ("a digest", f"{IMAGE}:ok\n{IMAGE}@{OTHER}"),
                           ("a bad tag", f"{IMAGE}:-bad"), ("a space", f"{IMAGE}:a b"), ("none", "\n \n")]:
            with self.subTest(name):
                shutil.rmtree(self.root / "runner/build-service", ignore_errors=True)
                (self.temp / "crane-calls").unlink(missing_ok=True)
                result = self.publish(TAGS=tags)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls("crane-calls"), [])
                self.assertEqual(self.calls("docker-calls"), [])

    def test_a_tag_that_does_not_resolve_to_the_staged_digest_fails(self):
        result = self.publish(PUBLISHED_DIGEST=OTHER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the staged image", result.stdout)
        self.assertEqual(self.output.read_text(), "")

    def test_a_failed_copy_fails_the_step_before_any_verification(self):
        result = self.publish(IMAGETOOLS_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse([call for call in self.calls("crane-calls") if "|digest" in call])
        self.assertEqual(self.output.read_text(), "")

    def test_nothing_is_published_when_the_build_did_not_stage_anything(self):
        (self.root / "runner/app-image/digest").write_text("\n")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no staged image", result.stdout)
        self.assertEqual(self.calls("crane-calls"), [])


class Prune(Workspace):
    NOW = int(time.time())

    @staticmethod
    def version(identifier: int, tags: list[str], age_hours: float) -> dict:
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(Prune.NOW - int(age_hours * 3600)))
        return {"id": identifier, "created_at": created, "metadata": {"container": {"tags": tags}}}

    def setUp(self):
        super().setUp()
        self.env["GH_TOKEN"] = TOKEN
        self.pages = self.root / "pages"
        self.pages.mkdir()
        # curl plays the packages API: it lists the page files, answers DELETE with its own status, and records both
        self.stub("curl", '''cat > "$RUNNER_TEMP/curl-stdin"
out=""; fmt=""; method=GET; url=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --output) out="${args[i + 1]}" ;;
    --write-out) fmt="${args[i + 1]}" ;;
    --request) method="${args[i + 1]}" ;;
    http*) url="${args[i]}" ;;
  esac
done
printf '%s %s\\n' "$method" "$url" >> "$RUNNER_TEMP/api-calls"
printf 'ARGS %s\\n' "$*" >> "$RUNNER_TEMP/api-argv"
if [[ "$method" = DELETE ]]; then printf '%s' "${DELETE_STATUS:-204}"; exit 0; fi
page="${url##*page=}"
if [[ -n "${LIST_STATUS:-}" ]]; then printf '%s' "$LIST_STATUS"; exit 0; fi
cat "$PAGES/$page.json" > "$out" 2>/dev/null || echo '[]' > "$out"
printf '200'
''')
        self.env["PAGES"] = str(self.pages)

    def pages_of(self, *pages: list[dict]):
        for number, versions in enumerate(pages, 1):
            (self.pages / f"{number}.json").write_text(json.dumps(versions))

    def prune(self, **overrides):
        return self.run_script("prune.sh", **overrides)

    def deleted(self) -> list[str]:
        return [call.split("/")[-1] for call in self.calls("api-calls") if call.startswith("DELETE ")]

    def test_deletes_only_old_versions_whose_every_tag_is_a_candidate(self):
        self.pages_of([
            self.version(1, ["candidate-10-1"], 30), self.version(2, ["candidate-11-1", "candidate-11-2"], 50),
            self.version(3, ["candidate-12-1"], 2), self.version(4, ["candidate-9-1", "1.2.3"], 99),
            self.version(5, ["latest"], 99), self.version(6, [], 99), self.version(7, ["candidate-x-1"], 99),
            self.version(8, ["precandidate-1-1"], 99),
        ])
        result = self.prune()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.deleted(), ["1", "2"])
        self.assertIn("Pruned 2 stale candidate version(s)", result.stdout)

    def test_addresses_the_org_package_with_an_encoded_name_and_keeps_the_token_off_the_command_line(self):
        self.pages_of([self.version(1, ["candidate-10-1"], 30)])
        result = self.prune(IMAGE="ghcr.io/rubio-enterprises/org/pkg")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("api-calls")[0],
                         "GET https://api.github.com/orgs/rubio-enterprises/packages/container/org%2Fpkg/versions?per_page=100&page=1")
        self.assertEqual(self.calls("api-calls")[-1],
                         "DELETE https://api.github.com/orgs/rubio-enterprises/packages/container/org%2Fpkg/versions/1")
        self.assertNotIn(TOKEN, "\n".join(self.calls("api-argv")))
        self.assertIn(f"Bearer {TOKEN}", (self.temp / "curl-stdin").read_text())

    def test_the_time_to_live_is_configurable_and_validated(self):
        self.pages_of([self.version(1, ["candidate-10-1"], 2), self.version(2, ["candidate-11-1"], 30)])
        self.assertEqual(self.prune(TTL_HOURS="1").returncode, 0)
        self.assertEqual(self.deleted(), ["1", "2"])
        for bad in ("0", "-1", "1h", ""):
            with self.subTest(bad):
                self.assertNotEqual(self.prune(TTL_HOURS=bad if bad else "x").returncode, 0)

    def test_pages_until_a_short_page_and_never_beyond_three(self):
        full = [self.version(100 + n, ["latest"], 99) for n in range(100)]
        self.pages_of(full, full, [self.version(300, ["candidate-1-1"], 99)] + full[:99], full)
        self.assertEqual(self.prune().returncode, 0)
        self.assertEqual([call.rsplit("page=", 1)[1] for call in self.calls("api-calls") if call.startswith("GET ")], ["1", "2", "3"])
        self.assertEqual(self.deleted(), ["300"])

    def test_failures_only_warn(self):
        self.pages_of([self.version(1, ["candidate-10-1"], 30)])
        listing = self.prune(LIST_STATUS="404")
        self.assertEqual(listing.returncode, 0)
        self.assertIn("::warning::Could not list", listing.stdout)
        self.assertEqual(self.deleted(), [])
        deleting = self.prune(DELETE_STATUS="403")
        self.assertEqual(deleting.returncode, 0)
        self.assertIn("::warning::Could not delete candidate version 1", deleting.stdout)


if __name__ == "__main__":
    unittest.main()
