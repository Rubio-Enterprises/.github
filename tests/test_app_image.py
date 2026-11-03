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
WRAPPED = "sha256:" + "c" * 64  # the index buildx wraps a bare image manifest in
IMAGE = "ghcr.io/rubio-enterprises/example"
STAGING = "stage.example.invalid:5000/rubio-enterprises/repo/run-123-2"
TOKEN = "ghs_secret_token_value"


def slug(reference: str) -> str:
    """How the registry stubs name the file that holds a reference's digest."""
    return re.sub(r"[^a-zA-Z0-9]", "_", reference)


def steps(name: str) -> str:
    """The text of one workflow step, from its name to the next step."""
    return TEXT.split(f"- name: {name}\n", 1)[1].split("\n      - name:", 1)[0].split("\n\n  ", 1)[0]


PREPARE = TEXT.split("\n  prepare:\n", 1)[1].split("\n  build:\n", 1)[0]
BUILD = TEXT.split("\n  build:\n", 1)[1].split("\n\n  # Gives a tested candidate", 1)[0]
PROMOTE = TEXT.split("\n  promote:\n", 1)[1]


class WorkflowShape(unittest.TestCase):
    def test_job_is_a_client_on_the_image_route_with_the_permissions_the_callers_must_grant(self):
        self.assertIn("permissions: {}", TEXT)
        self.assertIn("permissions:\n      contents: read\n      packages: write\n      id-token: write\n", TEXT)
        self.assertEqual(TEXT.count("id-token: write"), 1)
        self.assertNotIn("attestations: write", TEXT)
        self.assertIn("runs-on: ${{ fromJSON(vars.RUNNERS || '{}').image || fromJSON('[\"linux-x64-image\"]') }}", TEXT)
        self.assertNotIn("ubuntu", BUILD)  # the build has no hosted fallback; only the prepare job's small route has one
        self.assertIn("timeout-minutes: ${{ inputs.timeout-minutes }}", TEXT)
        self.assertIn('BUILDX_NO_DEFAULT_ATTESTATIONS: "1"', TEXT)
        for forbidden in ("docker/login-action", "docker/setup-buildx-action", "docker/build-push-action", "docker/setup-qemu-action",
                          "docker run", "docker pull", "docker push", "--insecure", "--cache-from", "--cache-to", "type=gha",
                          "continue-on-error", "pull_request_target", "workflow_run", "secrets:", "platforms"):
            self.assertNotIn(forbidden, TEXT)

    def test_third_party_actions_are_pinned_and_first_party_ones_follow_main(self):
        for ref in re.findall(r"uses: (\S+)", TEXT):
            if ref.startswith("Rubio-Enterprises/.github/actions/"):
                self.assertIn(ref, ("Rubio-Enterprises/.github/actions/dev-container-image@main", "Rubio-Enterprises/.github/actions/app-image@main"))
            else:
                self.assertRegex(ref, r"@[a-f0-9]{40}$")

    def test_steps_run_in_the_order_that_keeps_credentials_scoped(self):
        names = ["Resolve the image and the build mode", "Check out the caller", "Set up Node",
                 "Fetch the Standards Package prepared without credentials", "Install the Build Service client tools", "Compute image tags and labels", "Connect to the Build Service",
                 "Build the image on the Build Service", "Check the image's structure", "Copy the verified image to GHCR", "Prune stale candidate images",
                 "Remove the job's certificate, credentials and build inputs"]
        positions = [BUILD.index(f"- name: {name}\n") for name in names]
        self.assertEqual(positions, sorted(positions))
        # Only the copy step sees the GHCR token and only the prune the API token; the one other use is the prepare job's package read
        self.assertEqual(TEXT.count("github.token"), 4)  # the prepare job, the copy, the prune and the promote job
        self.assertEqual(BUILD.count("github.token"), 2)
        self.assertIn("REGISTRY_TOKEN: ${{ github.token }}", steps("Copy the verified image to GHCR"))
        self.assertNotIn("github.token", steps("Build the image on the Build Service"))
        self.assertIn("GH_TOKEN: ${{ github.token }}", steps("Prune stale candidate images"))
        self.assertIn("if: always()", TEXT.split("- name: Remove the job's certificate", 1)[1])
        self.assertIn("steps.resolve.outputs.mode != 'check'", steps("Copy the verified image to GHCR"))
        self.assertIn("steps.resolve.outputs.mode == 'candidate'", steps("Prune stale candidate images"))

    def test_caller_text_reaches_scripts_only_through_the_environment(self):
        for name in ("Resolve the image and the build mode", "Remove the job's certificate, credentials and build inputs",
                     "Install the Standards Package into .standards", "Name the artifact"):
            run = steps(name).split("run:", 1)[1]
            self.assertNotIn("${{", run)

    def test_the_standards_install_runs_in_its_own_job_without_the_oidc_token_or_package_write(self):
        self.assertIn("    if: inputs.standards-install && !inputs.promote\n    permissions:\n      contents: read\n      packages: read\n", PREPARE)
        self.assertIn("runs-on: ${{ fromJSON(vars.RUNNERS || '{}').small || fromJSON('[\"ubuntu-slim\"]') }}", PREPARE)
        for forbidden in ("id-token", "packages: write", "contents: write", "Rubio-Enterprises/.github/actions", "connect", "BUILD_SERVICE"):
            self.assertNotIn(forbidden, PREPARE)
        self.assertIn("mise install node && mise run standards:install", PREPARE)
        self.assertIn("cache: false", steps("Set up mise"))
        # The hand-over is an artifact named once per call, uploaded with its hidden directory
        self.assertIn("artifact=standards-$(cat /proc/sys/kernel/random/uuid)", PREPARE)
        upload = steps("Hand .standards over as an artifact")
        for text in ("path: .standards", "include-hidden-files: true", "if-no-files-found: error", "retention-days: 1"):
            self.assertIn(text, upload)
        # The build only downloads it, and only when asked
        download = steps("Fetch the Standards Package prepared without credentials")
        self.assertIn("if: inputs.standards-install", download)
        self.assertIn("name: ${{ needs.prepare.outputs.artifact }}\n          path: .standards", download)
        self.assertIn("    needs: prepare\n    if: ${{ !cancelled() && !failure() && !inputs.promote }}\n", BUILD)

    def test_repository_code_never_runs_in_the_job_that_holds_the_credentials(self):
        # The job holds the OIDC token, the certificate, the staging password and the GHCR token, and its steps share PATH,
        # GITHUB_ENV, GITHUB_PATH and BASH_ENV. A repository task there could mint its own token, shadow crane or jq, set
        # BASH_ENV or rewrite the downloaded action scripts. Repository files may only reach it as data.
        for forbidden in ("mise", "setup-uv", "standards:install", "npm ", "pnpm", "npx", "uv ", "make ", "python", "node ", "./",
                          "GITHUB_WORKSPACE", "$PWD", "hashFiles", "source ", "eval "):
            self.assertNotIn(forbidden, BUILD, forbidden)
        for run in re.findall(r"\n        run: (.*)", BUILD) + re.findall(r"\n        run: \|\n((?:          .*\n)+)", BUILD):
            self.assertNotIn("workspace", run.lower())
        # Everything before the connect step is: our validation, checkout, setup-node, an artifact download and two central actions
        before_connect = BUILD.split("- name: Connect to the Build Service", 1)[0]
        allowed = {"Resolve the image and the build mode", "Check out the caller", "Set up Node",
                   "Fetch the Standards Package prepared without credentials", "Install the Build Service client tools",
                   "Compute image tags and labels"}
        self.assertEqual(set(re.findall(r"- name: (.*)\n", before_connect)), allowed)
        self.assertEqual(sorted(re.findall(r"uses: (\S+)", before_connect)), sorted([
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38",
            "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333", "Rubio-Enterprises/.github/actions/dev-container-image@main",
            "docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302"]))
        # No step but the trusted central ones writes to the files later steps inherit from
        for name, step in re.findall(r"- name: (.*)\n((?:        .*\n|      #.*\n|\n)*)", BUILD):
            if "GITHUB_ENV" in step or "GITHUB_PATH" in step:
                self.fail(f"{name} writes the environment later steps inherit")
        # The caller's checkout stays a build context: the only files the build executes come from the central action directory
        self.assertEqual(BUILD.count("Rubio-Enterprises/.github/actions/"), 6)  # tools, connect, build, structure, publish, prune

    def test_publish_and_candidate_are_exclusive_and_publish_needs_tags(self):
        resolve = steps("Resolve the image and the build mode")
        self.assertIn('"$PUBLISH" == true && "$CANDIDATE" == true', resolve)
        self.assertIn("publish needs the tags input", resolve)
        self.assertIn("tags: ${{ inputs.publish && steps.meta.outputs.tags || '' }}", TEXT)

    def test_a_verified_image_is_a_publish_only_candidate_of_the_same_image(self):
        resolve = steps("Resolve the image and the build mode")
        self.assertIn("VERIFIED: ${{ inputs.verified-image }}", resolve)
        # The default is a sentinel, because a passed input that resolves empty stays "" (the default applies only when the input is
        # omitted); that difference is what lets an unwired output fail instead of skipping the check
        self.assertRegex(TEXT, r"      verified-image:\n        description: >-\n(?:          .*\n)+        type: string\n        default: none\n")
        self.assertIn('if [[ "$VERIFIED" != none ]]; then', resolve)
        self.assertIn("verified-image was passed but is empty", resolve)
        self.assertIn("verified-image only applies to a publish", resolve)
        self.assertIn('"${VERIFIED,,}" != "$image@sha256:"*', resolve)
        self.assertIn("VERIFIED_IMAGE: ${{ inputs.verified-image }}", steps("Copy the verified image to GHCR"))

    def test_tags_and_labels_follow_a_ref_other_than_the_events(self):
        meta = steps("Compute image tags and labels")
        self.assertIn("context: ${{ inputs.ref != '' && inputs.ref != github.ref && inputs.ref != github.sha && 'git' || 'workflow' }}", meta)

    def test_the_action_declares_every_output_the_workflow_reads_from_it(self):
        # A composite action's inner steps keep their outputs to themselves unless the action maps them: without this the build
        # job's `digest` and `image` outputs, and every caller's `needs.<job>.outputs.image`, are empty (found by the first canary run)
        action = (ACTION / "action.yml").read_text()
        self.assertRegex(action, r"\n    - id: run\n      shell: bash\n")
        declared = dict(re.findall(r"\n  (\w+):\n    description: .*\n    value: \$\{\{ steps\.run\.outputs\.(\w+) \}\}", action))
        self.assertEqual(declared, {"digest": "digest", "image": "image", "run": "run"})
        used = set()
        for step_id in re.findall(r"- name: .*\n        id: (\w+)\n(?:        .*\n)*?        uses: Rubio-Enterprises/\.github/actions/app-image@main", TEXT):
            used.update(re.findall(rf"steps\.{step_id}\.outputs\.([\w-]+)", TEXT))
        self.assertEqual(used, {"digest", "image"})
        self.assertLessEqual(used, set(declared))
        for output, source in (("digest", "steps.build.outputs.digest"), ("image", "steps.publish.outputs.image")):
            self.assertIn(f"{output}: ${{{{ {source} }}}}", BUILD)

    def test_the_action_dispatches_only_known_operations_and_reuses_the_connect_script(self):
        action = (ACTION / "action.yml").read_text()
        self.assertIn('exec bash "$GITHUB_ACTION_PATH/../dev-container-image/connect.sh"', action)
        self.assertIn("build | structure | publish | promote | prune | smoke | pull | changes)", action)
        self.assertIn("OPERATION: ${{ inputs.operation }}", action)
        self.assertNotIn("${{", action.split("run: |", 1)[1])
        self.assertTrue((ROOT / "actions/dev-container-image/connect.sh").is_file())


def run_block(step_name: str) -> str:
    """The shell of one `run: |` step, dedented."""
    lines = steps(step_name).split("run: |\n", 1)[1].splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip())
    return "\n".join(line[indent:] for line in lines) + "\n"


class Resolve(unittest.TestCase):
    """The workflow's own validation step, run as the runner would run it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "output"
        self.output.touch()
        self.script = run_block("Resolve the image and the build mode")

    def resolve(self, **overrides):
        env = {**os.environ, "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises", "GITHUB_REPOSITORY": "Rubio-Enterprises/Repo",
               "GITHUB_OUTPUT": str(self.output), "IMAGE_INPUT": "", "PUBLISH": "true", "CANDIDATE": "false",
               "TAGS": "type=raw,value=1", "VERIFIED": "none", "POLICY": "true", **overrides}
        return subprocess.run(["bash", "-c", self.script], env=env, capture_output=True, text=True)

    def test_an_omitted_verified_image_publishes_without_the_check(self):
        result = self.resolve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mode=publish", self.output.read_text())

    def test_a_verified_image_that_resolved_empty_fails_loudly_in_every_mode(self):
        # A caller who wires the input to a job output that is missing (a skipped job, a renamed output) gets "" here,
        # not the default, and must never silently lose the check
        for name, overrides in [("publish", {}), ("candidate", {"PUBLISH": "false", "CANDIDATE": "true", "TAGS": ""}),
                                ("check", {"PUBLISH": "false", "TAGS": ""})]:
            with self.subTest(name):
                result = self.resolve(VERIFIED="", **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("verified-image was passed but is empty", result.stdout)
                self.assertEqual(self.output.read_text(), "")

    def test_a_real_candidate_of_this_image_passes_and_anything_else_is_refused(self):
        image = "ghcr.io/rubio-enterprises/repo"
        good = f"{image}@sha256:{'a' * 64}"
        self.assertEqual(self.resolve(VERIFIED=good).returncode, 0)
        for name, overrides, message in [
            ("another image", {"VERIFIED": f"ghcr.io/rubio-enterprises/other@sha256:{'a' * 64}"}, "must be this image's candidate"),
            ("a tag", {"VERIFIED": f"{image}:candidate-1-1-aaaaaaaaaaaa"}, "must be this image's candidate"),
            ("a short digest", {"VERIFIED": f"{image}@sha256:abc"}, "must be this image's candidate"),
            ("not a publish", {"VERIFIED": good, "PUBLISH": "false", "CANDIDATE": "true", "TAGS": ""}, "only applies to a publish"),
        ]:
            with self.subTest(name):
                result = self.resolve(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout)

    def test_an_image_belongs_to_the_repository_that_builds_it(self):
        for image in ("ghcr.io/rubio-enterprises/repo", "ghcr.io/rubio-enterprises/repo/worker", "GHCR.io/Rubio-Enterprises/Repo/Worker"):
            with self.subTest(image):
                self.assertEqual(self.resolve(IMAGE_INPUT=image).returncode, 0)
        for image in ("ghcr.io/rubio-enterprises/other", "ghcr.io/rubio-enterprises/repository", "ghcr.io/rubio-enterprises/other/repo",
                      "ghcr.io/rubio-enterprises/repos/worker"):
            with self.subTest(image):
                result = self.resolve(IMAGE_INPUT=image)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("an image belongs to the repository that builds it", result.stdout)


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
            "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises", "GITHUB_REPOSITORY": "Rubio-Enterprises/Example",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_ACTOR": "octocat",
            "STAGING": STAGING, "STAGING_TAG": "run-123-2", "BUILDX_BUILDER": "build-service", "BUILD_SERVICE_RETRY_DELAY": "0",
            "IMAGE": IMAGE, "APP_IMAGE_POLICY": "true", "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
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

    def test_a_candidate_carries_the_attestations_its_promotion_will_publish(self):
        self.assertEqual(self.build(MODE="candidate", ATTESTATIONS="true").returncode, 0)
        self.assertIn("--provenance=mode=max --sbom=true", self.command())
        self.assertIn("run-123-2-candidate,push=true", self.command())
        self.assertEqual(self.build(MODE="candidate", ATTESTATIONS="false").returncode, 0)
        self.assertIn("--provenance=false --sbom=false", self.command())

    def test_a_check_never_carries_attestations(self):
        self.assertEqual(self.build(MODE="check", ATTESTATIONS="true").returncode, 0)
        self.assertIn("--provenance=false --sbom=false", self.command())

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

    def test_a_failed_build_leaves_no_staged_image_for_publish_to_pick_up(self):
        state = self.temp / "app-image"
        state.mkdir()
        (state / "digest").write_text(DIGEST + "\n")
        (state / "staging.ref").write_text("earlier-build\n")
        result = self.build(DOCKER_FAILURES="2", DOCKER_ERROR="ERROR: failed to solve")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((state / "digest").exists())
        self.assertFalse((state / "staging.ref").exists())

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


def layers_manifest(*digests: str) -> str:
    return json.dumps({"schemaVersion": 2, "layers": [{"digest": digest, "size": 1} for digest in digests]})


class Publish(Workspace):
    def setUp(self):
        super().setUp()
        self.env.update(MODE="publish", REGISTRY_TOKEN=TOKEN, STUB_DIGEST=DIGEST, STUB_WRAPPED=WRAPPED,
                        TAGS=f"{IMAGE}:1.2.3\n{IMAGE}:1.2\n{IMAGE}:sha-abc1234\n")
        state = self.root / "runner/app-image"
        state.mkdir()
        (state / "digest").write_text(DIGEST + "\n")
        (state / "staging.ref").write_text(f"{STAGING}/example:run-123-2-publish\n")
        # crane resolves a tag to what the docker stub published under it; manifest reads the layers of the image it is given
        self.stub("crane", '''printf '%s|%s\\n' "${DOCKER_CONFIG:-}" "$*" >> "$RUNNER_TEMP/crane-calls"
case "$1" in
  auth) cat > "$RUNNER_TEMP/crane-stdin" ;;
  digest)
    # PUBLISHED_DIGEST models a tag that resolves elsewhere once the copy has happened; a tag nothing was copied to is unknown
    if [[ -n "${PUBLISHED_DIGEST:-}" && -d "$RUNNER_TEMP/published" ]]; then printf '%s\\n' "$PUBLISHED_DIGEST"; exit 0; fi
    if [[ -f "$RUNNER_TEMP/published/${2//[^a-zA-Z0-9]/_}" ]]; then cat "$RUNNER_TEMP/published/${2//[^a-zA-Z0-9]/_}"; exit 0; fi
    echo "MANIFEST_UNKNOWN: manifest unknown to registry" >&2; exit 1 ;;
  manifest)
    [[ "$2 $3" == "--platform linux/amd64" ]] || { echo "crane manifest needs --platform linux/amd64" >&2; exit 2; }
    if [[ "$4" == ghcr.io/* ]]; then printf '%s' "${TESTED_MANIFEST:-}"; else printf '%s' "${STAGED_MANIFEST:-}"; fi
    if [[ -n "${MANIFEST_STATUS:-}" ]]; then exit "$MANIFEST_STATUS"; fi ;;
esac
''')
        # imagetools create copies the source under every --tag. Its default (--prefer-index=true) wraps a bare image manifest
        # in a new index with a different digest (buildx 0.38.1, util/imagetools/create.go); an index is copied as it is.
        self.stub("docker", '''printf '%s|%s|%s\\n' "${DOCKER_CONFIG:-}" "${BUILDX_BUILDER-unset}" "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "${IMAGETOOLS_STATUS:-0}" != 0 ]]; then exit "$IMAGETOOLS_STATUS"; fi
args=("$@"); prefer_index=true; tags=()
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --prefer-index=false) prefer_index=false ;;
    --tag) tags+=("${args[i + 1]}") ;;
  esac
done
published="$STUB_DIGEST"
if [[ "${SOURCE_KIND:-index}" == image && "$prefer_index" == true ]]; then published="$STUB_WRAPPED"; fi
mkdir -p "$RUNNER_TEMP/published"
for tag in "${tags[@]}"; do printf '%s\\n' "$published" > "$RUNNER_TEMP/published/${tag//[^a-zA-Z0-9]/_}"; done
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
            f"{config}|unset|buildx imagetools create --prefer-index=false --tag {IMAGE}:1.2.3 --tag {IMAGE}:1.2 --tag {IMAGE}:sha-abc1234 {staged}"])
        for tag in ("1.2.3", "1.2", "sha-abc1234"):
            self.assertIn(f"{config}|digest {IMAGE}:{tag}", self.calls("crane-calls"))
        self.assertFalse([call for call in self.calls("crane-calls") if "|copy" in call])  # crane copy adds referrers tags
        self.assertEqual((self.temp / "crane-stdin").read_text(), TOKEN)
        self.assertEqual(self.outputs(), {"digest": DIGEST, "image": f"{IMAGE}@{DIGEST}"})
        self.assertIn(f"{IMAGE}:1.2.3@{DIGEST}", self.summary.read_text())

    def test_a_bare_image_manifest_is_copied_as_itself_not_wrapped_in_a_new_index(self):
        # A candidate, and a publish with attestations off, stage one image manifest
        for mode, tags in (("candidate", ""), ("publish", f"{IMAGE}:1.0.0")):
            with self.subTest(mode):
                shutil.rmtree(self.temp / "published", ignore_errors=True)
                result = self.publish(MODE=mode, TAGS=tags, SOURCE_KIND="image")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.outputs()["image"], f"{IMAGE}@{DIGEST}")

    def test_without_prefer_index_false_a_bare_image_manifest_would_fail_the_digest_check(self):
        # publish.sh as it was before the flag: the stub wraps the image as buildx 0.38 does, and the digest check catches it
        broken = self.root / "broken/app-image"
        broken.mkdir(parents=True)
        (self.root / "broken/build-service").mkdir()
        (self.root / "broken/build-service/retry.sh").write_text((ROOT / "actions/build-service/retry.sh").read_text())
        for name in ("publish.sh", "lib.sh"):
            (broken / name).write_text((ACTION / name).read_text().replace("--prefer-index=false ", ""))
        self.assertNotIn("--prefer-index", (broken / "lib.sh").read_text().split("imagetools create", 1)[1].splitlines()[0])
        env = {**self.env, "MODE": "candidate", "TAGS": "", "SOURCE_KIND": "image"}
        result = subprocess.run(["bash", str(broken / "publish.sh")], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the copied image", result.stdout)

    def test_an_index_with_attestations_is_copied_byte_for_byte(self):
        result = self.publish(SOURCE_KIND="index")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--prefer-index=false", self.calls("docker-calls")[0])

    def test_the_token_never_appears_in_a_command_line_or_the_output(self):
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, "\n".join(self.calls("crane-calls") + self.calls("docker-calls")))
        self.assertNotIn(TOKEN, result.stdout + result.stderr + self.output.read_text() + self.summary.read_text())

    def test_a_candidate_gets_one_temporary_tag_named_for_the_run_and_the_image(self):
        result = self.publish(MODE="candidate", TAGS="", SOURCE_KIND="image")
        self.assertEqual(result.returncode, 0, result.stderr)
        copies = self.calls("docker-calls")
        self.assertEqual(len(copies), 1)
        self.assertIn(f"imagetools create --prefer-index=false --tag {IMAGE}:candidate-123-2-{DIGEST[7:19]} {STAGING}/example:run-123-2-publish@{DIGEST}", copies[0])
        self.assertEqual(self.outputs()["image"], f"{IMAGE}@{DIGEST}")

    def test_two_candidates_of_one_image_in_a_run_do_not_share_a_tag(self):
        first = self.publish(MODE="candidate", TAGS="", SOURCE_KIND="image")
        (self.root / "runner/app-image/digest").write_text(OTHER + "\n")
        self.output.write_text("")
        second = self.publish(MODE="candidate", TAGS="", SOURCE_KIND="image", STUB_DIGEST=OTHER)
        self.assertEqual((first.returncode, second.returncode), (0, 0), first.stdout + second.stdout)
        tags = [re.search(r"--tag (\S+)", call).group(1) for call in self.calls("docker-calls")]
        self.assertEqual(len(set(tags)), 2, tags)
        for tag in tags:
            self.assertRegex(tag, rf"^{re.escape(IMAGE)}:candidate-123-2-[a-f0-9]{{12}}$")

    def test_tags_outside_the_image_or_malformed_publish_nothing(self):
        for name, tags in [("other repository", f"{IMAGE}:ok\nghcr.io/rubio-enterprises/other:ok"),
                           ("a digest", f"{IMAGE}:ok\n{IMAGE}@{OTHER}"),
                           ("a bad tag", f"{IMAGE}:-bad"), ("a space", f"{IMAGE}:a b"), ("none", "\n \n"),
                           ("a candidate lookalike", f"{IMAGE}:1.0\n{IMAGE}:candidate-1-1-aaaaaaaaaaaa"),
                           ("any candidate prefix", f"{IMAGE}:candidate-release")]:
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
        self.assertIn("differs from the copied image", result.stdout)
        self.assertEqual(self.output.read_text(), "")

    def test_a_failed_copy_fails_the_step_before_any_verification(self):
        result = self.publish(IMAGETOOLS_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        # only the check that each tag is unused ran: no tag was verified after the copy
        self.assertEqual(len([call for call in self.calls("crane-calls") if "|digest" in call]), 3)
        self.assertEqual(self.output.read_text(), "")

    def test_nothing_is_published_when_the_build_did_not_stage_anything(self):
        (self.root / "runner/app-image/digest").write_text("\n")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no staged image", result.stdout)
        self.assertEqual(self.calls("crane-calls"), [])

    def test_a_publish_of_the_tested_candidates_layers_goes_ahead(self):
        layers = ["sha256:" + char * 64 for char in "123"]
        result = self.publish(VERIFIED_IMAGE=f"{IMAGE}@{OTHER}", TESTED_MANIFEST=layers_manifest(*layers),
                              STAGED_MANIFEST=layers_manifest(*layers))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("The staged image has the tested image's layers", result.stdout)
        manifests = [call for call in self.calls("crane-calls") if "|manifest" in call]
        self.assertEqual(len(manifests), 2)
        self.assertTrue(all("manifest --platform linux/amd64" in call for call in manifests))
        self.assertTrue(self.calls("docker-calls"))

    def test_a_publish_whose_layers_differ_from_the_tested_ones_publishes_nothing(self):
        tested = ["sha256:" + char * 64 for char in "123"]
        built = ["sha256:" + char * 64 for char in "124"]
        result = self.publish(VERIFIED_IMAGE=f"{IMAGE}@{OTHER}", TESTED_MANIFEST=layers_manifest(*tested),
                              STAGED_MANIFEST=layers_manifest(*built))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different layers from the tested image", result.stdout)
        self.assertEqual(self.calls("docker-calls"), [])
        self.assertEqual(self.output.read_text(), "")
        # Layer order is part of the image
        reordered = self.publish(VERIFIED_IMAGE=f"{IMAGE}@{OTHER}", TESTED_MANIFEST=layers_manifest(*tested),
                                 STAGED_MANIFEST=layers_manifest(*reversed(tested)))
        self.assertNotEqual(reordered.returncode, 0)

    def test_an_omitted_or_none_verified_image_skips_the_check_and_an_empty_one_fails_before_anything_is_copied(self):
        for name, overrides in [("unset", {}), ("none", {"VERIFIED_IMAGE": "none"})]:
            with self.subTest(name):
                shutil.rmtree(self.temp / "published", ignore_errors=True)
                (self.temp / "crane-calls").unlink(missing_ok=True)
                (self.temp / "docker-calls").unlink(missing_ok=True)
                result = self.publish(**overrides)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse([call for call in self.calls("crane-calls") if "|manifest" in call])
        shutil.rmtree(self.temp / "published", ignore_errors=True)
        (self.temp / "crane-calls").unlink(missing_ok=True)
        (self.temp / "docker-calls").unlink(missing_ok=True)
        empty = self.publish(VERIFIED_IMAGE="")
        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("verified-image was passed but is empty", empty.stdout)
        self.assertEqual(self.calls("docker-calls"), [])
        self.assertEqual(self.calls("crane-calls"), [])

    def test_an_unreadable_tested_or_staged_image_fails_rather_than_skipping_the_check(self):
        layers = layers_manifest("sha256:" + "1" * 64)
        for name, overrides in [("registry error", {"MANIFEST_STATUS": "1"}), ("no layers", {"TESTED_MANIFEST": "{}", "STAGED_MANIFEST": "{}"})]:
            with self.subTest(name):
                env = {"TESTED_MANIFEST": layers, "STAGED_MANIFEST": layers, **overrides}
                result = self.publish(VERIFIED_IMAGE=f"{IMAGE}@{OTHER}", **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls("docker-calls"), [])

    def test_a_verified_image_must_be_a_digest_of_this_image_and_only_for_a_publish(self):
        layers = layers_manifest("sha256:" + "1" * 64)
        base = {"TESTED_MANIFEST": layers, "STAGED_MANIFEST": layers}
        other_repository = self.publish(VERIFIED_IMAGE=f"ghcr.io/rubio-enterprises/other@{OTHER}", **base)
        self.assertNotEqual(other_repository.returncode, 0)
        self.assertIn("is not a digest of", other_repository.stdout)
        candidate = self.publish(MODE="candidate", TAGS="", VERIFIED_IMAGE=f"{IMAGE}@{OTHER}", **base)
        self.assertNotEqual(candidate.returncode, 0)
        self.assertIn("only applies to a publish", candidate.stdout)
        self.assertEqual(self.calls("docker-calls"), [])

    def test_a_tag_that_already_holds_another_image_is_never_overwritten(self):
        published = self.temp / "published"
        published.mkdir()
        (published / slug(f"{IMAGE}:1.2.3")).write_text(OTHER + "\n")
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already points at", result.stdout)
        self.assertIn("never overwritten", result.stdout)
        self.assertEqual(self.calls("docker-calls"), [])
        self.assertEqual(self.output.read_text(), "")

    def test_a_tag_on_the_moving_list_may_move_and_the_same_digest_is_a_rerun(self):
        published = self.temp / "published"
        published.mkdir()
        (published / slug(f"{IMAGE}:1.2.3")).write_text(OTHER + "\n")
        (published / slug(f"{IMAGE}:1.2")).write_text(DIGEST + "\n")
        result = self.publish(MOVING_TAGS="\nsha-xyz\n1.2.3\r\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.calls("docker-calls")), 1)

    def test_a_tag_that_cannot_be_read_stops_the_publish_instead_of_being_assumed_free(self):
        self.stub("crane", '''case "$1" in auth) cat > /dev/null ;; digest) echo "UNAUTHORIZED: authentication required" >&2; exit 1 ;; esac
''')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not read", result.stdout)
        self.assertEqual(self.calls("docker-calls"), [])

    def test_latest_is_never_published(self):
        for tags in (f"{IMAGE}:latest", f"{IMAGE}:1.0.0\n{IMAGE}:latest"):
            with self.subTest(tags):
                result = self.publish(TAGS=tags)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("no latest tag", result.stdout)
                self.assertEqual(self.calls("docker-calls"), [])

    def test_an_image_outside_the_repositorys_namespace_is_refused(self):
        for image in ("ghcr.io/rubio-enterprises/other", "ghcr.io/rubio-enterprises/examples", "ghcr.io/rubio-enterprises/other/example"):
            with self.subTest(image):
                result = self.publish(IMAGE=image, TAGS=f"{image}:1.0.0")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("outside this repository's namespace", result.stdout)
        self.assertEqual(self.publish(IMAGE=f"{IMAGE}/worker", TAGS=f"{IMAGE}/worker:1.0.0").returncode, 0)


class Promote(Workspace):
    """promote.sh: a tested candidate gets its final tags by digest, with nothing built."""

    CANDIDATE = f"{IMAGE}@{DIGEST}"

    def setUp(self):
        super().setUp()
        self.env.update(REGISTRY_TOKEN=TOKEN, STUB_DIGEST=DIGEST, CANDIDATE_IMAGE=self.CANDIDATE,
                        TAGS=f"{IMAGE}:1.2.3\n{IMAGE}:sha-abc1234\n")
        self.published = self.temp / "published"
        self.published.mkdir()
        self.seed(self.CANDIDATE, DIGEST)
        self.stub("crane", '''printf '%s|%s\\n' "${DOCKER_CONFIG:-}" "$*" >> "$RUNNER_TEMP/crane-calls"
case "$1" in
  digest)
    if [[ -n "${PUBLISHED_DIGEST:-}" && -f "$RUNNER_TEMP/copied" ]]; then printf '%s\\n' "$PUBLISHED_DIGEST"; exit 0; fi
    if [[ -f "$RUNNER_TEMP/published/${2//[^a-zA-Z0-9]/_}" ]]; then cat "$RUNNER_TEMP/published/${2//[^a-zA-Z0-9]/_}"; exit 0; fi
    echo "MANIFEST_UNKNOWN: manifest unknown to registry" >&2; exit 1 ;;
esac
''')
        self.stub("docker", '''printf '%s|%s|%s\\n' "${DOCKER_CONFIG:-}" "${BUILDX_BUILDER-unset}" "$*" >> "$RUNNER_TEMP/docker-calls"
args=("$@"); tags=()
for ((i = 0; i < ${#args[@]}; i++)); do [[ "${args[i]}" = --tag ]] && tags+=("${args[i + 1]}"); done
touch "$RUNNER_TEMP/copied"
for tag in "${tags[@]}"; do printf '%s\\n' "$STUB_DIGEST" > "$RUNNER_TEMP/published/${tag//[^a-zA-Z0-9]/_}"; done
''')

    def seed(self, reference: str, digest: str) -> None:
        (self.published / re.sub(r"[^a-zA-Z0-9]", "_", reference)).write_text(digest + "\n")

    def promote(self, **overrides):
        return self.run_script("promote.sh", **overrides)

    def test_retags_the_tested_digest_with_one_imagetools_call_and_builds_nothing(self):
        result = self.promote()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = f"{self.temp}/app-image/docker-ghcr"
        self.assertEqual(self.calls("docker-calls"), [
            f"{config}|unset|buildx imagetools create --prefer-index=false --tag {IMAGE}:1.2.3 --tag {IMAGE}:sha-abc1234 {self.CANDIDATE}"])
        for tag in ("1.2.3", "sha-abc1234"):
            self.assertIn(f"{config}|digest {IMAGE}:{tag}", self.calls("crane-calls"))
        self.assertFalse([call for call in self.calls("crane-calls") if "|copy" in call or "|push" in call])
        self.assertEqual(self.outputs(), {"digest": DIGEST, "image": f"{IMAGE}@{DIGEST}"})
        self.assertIn(f"{IMAGE}:1.2.3@{DIGEST}", self.summary.read_text())

    def test_the_token_reaches_only_the_job_local_docker_config(self):
        result = self.promote()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(TOKEN, "\n".join(self.calls("crane-calls") + self.calls("docker-calls")))
        self.assertNotIn(TOKEN, result.stdout + result.stderr + self.output.read_text() + self.summary.read_text())
        config = json.loads((self.temp / "app-image/docker-ghcr/config.json").read_text())
        self.assertEqual(list(config["auths"]), ["ghcr.io"])
        self.assertEqual((self.temp / "app-image/docker-ghcr/config.json").stat().st_mode & 0o777, 0o600)

    def test_a_candidate_that_is_not_this_images_digest_publishes_nothing(self):
        for name, candidate in [("another repository", f"ghcr.io/rubio-enterprises/other@{DIGEST}"), ("a tag", f"{IMAGE}:candidate-1-1-aaaaaaaaaaaa"),
                                ("a short digest", f"{IMAGE}@sha256:abc"), ("another package of this repository", f"{IMAGE}/worker@{DIGEST}")]:
            with self.subTest(name):
                result = self.promote(CANDIDATE_IMAGE=candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("candidate-image must be a digest of", result.stdout)
        self.assertEqual(self.calls("docker-calls"), [])

    def test_a_candidate_that_is_not_in_the_registry_publishes_nothing(self):
        (self.published / re.sub(r"[^a-zA-Z0-9]", "_", self.CANDIDATE)).unlink()
        result = self.promote()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("docker-calls"), [])

    def test_latest_a_candidate_lookalike_and_an_empty_list_are_refused_before_any_login(self):
        for tags in (f"{IMAGE}:latest", f"{IMAGE}:candidate-release", "", f"ghcr.io/rubio-enterprises/other:1.0"):
            with self.subTest(tags):
                result = self.promote(TAGS=tags)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls("docker-calls"), [])
                self.assertFalse((self.temp / "app-image/docker-ghcr").exists())

    def test_an_existing_version_tag_is_never_moved_but_a_rerun_of_the_same_digest_passes(self):
        self.seed(f"{IMAGE}:1.2.3", OTHER)
        refused = self.promote()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("never overwritten", refused.stdout)
        self.assertEqual(self.calls("docker-calls"), [])
        self.seed(f"{IMAGE}:1.2.3", DIGEST)
        self.assertEqual(self.promote().returncode, 0)
        self.seed(f"{IMAGE}:1.2.3", OTHER)
        self.assertEqual(self.promote(MOVING_TAGS="1.2.3").returncode, 0)

    def test_a_tag_that_resolves_elsewhere_after_the_copy_fails_the_step(self):
        result = self.promote(PUBLISHED_DIGEST=OTHER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from the copied image", result.stdout)
        self.assertEqual(self.output.read_text(), "")


def image_config(**overrides) -> str:
    config = {"User": "app", "Healthcheck": {"Test": ["CMD", "true"]},
              "Labels": {"org.opencontainers.image.source": "https://github.com/Rubio-Enterprises/example",
                         "org.opencontainers.image.revision": "0" * 40}}
    config.update(overrides)
    return json.dumps({"config": {key: value for key, value in config.items() if value is not None}})


class Structure(Workspace):
    """structure.sh: tier 1, read from a registry, never from the repository."""

    def setUp(self):
        super().setUp()
        state = self.root / "runner/app-image"
        state.mkdir()
        (state / "digest").write_text(DIGEST + "\n")
        (state / "staging.ref").write_text(f"{STAGING}/example:run-123-2-candidate\n")
        self.env.update(SERVER="true", TRIVY_BIN=str(self.bin / "trivy"), CONFIG=image_config(), SECRETS="{}", VULNERABILITIES="{}")
        self.stub("crane", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/crane-calls"
[[ "$1" = config ]] || exit 99
printf '%s' "$CONFIG"
''')
        # Trivy writes the report a scan asked for: the secret scan and the vulnerability scan are separate calls
        self.stub("trivy", '''printf '%s|%s\\n' "$PWD" "$*" >> "$RUNNER_TEMP/trivy-calls"
args=("$@"); out=""; scanner=""
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in --output) out="${args[i + 1]}" ;; --scanners) scanner="${args[i + 1]}" ;; esac
done
if [[ "$scanner" = secret ]]; then printf '%s' "$SECRETS" > "$out"
else
  if [[ -n "${VULN_SCAN_FAILS:-}" ]]; then exit 1; fi
  printf '%s' "$VULNERABILITIES" > "$out"
fi
''')

    def structure(self, **overrides):
        return self.run_script("structure.sh", **overrides)

    @staticmethod
    def vulnerabilities(*findings: tuple[str, str, str]) -> str:
        return json.dumps({"Results": [{"Target": "os", "Vulnerabilities": [
            {"VulnerabilityID": ident, "PkgName": "libssl", "InstalledVersion": "1", "FixedVersion": fixed, "Severity": severity}
            for ident, severity, fixed in findings]}]})

    def test_a_non_root_server_with_a_healthcheck_and_labels_passes_reading_the_staged_image(self):
        result = self.structure()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Image structure checks passed", result.stdout)
        self.assertEqual(self.calls("crane-calls"), [f"config --platform linux/amd64 {STAGING}/example:run-123-2-candidate@{DIGEST}"])

    def test_root_in_every_spelling_fails_and_a_named_or_numeric_user_passes(self):
        for user in (None, "", "root", "0", "0:0", "root:root", "0:1000"):
            with self.subTest(user):
                result = self.structure(CONFIG=image_config(User=user))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runs as root", result.stdout)
        for user in ("app", "1000", "1000:1000", "node:node"):
            with self.subTest(user):
                self.assertEqual(self.structure(CONFIG=image_config(User=user)).returncode, 0)

    def test_a_server_needs_a_healthcheck_and_a_command_line_tool_does_not(self):
        for health in (None, {"Test": ["NONE"]}, {"Test": []}):
            with self.subTest(health):
                result = self.structure(CONFIG=image_config(Healthcheck=health))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("declares no HEALTHCHECK", result.stdout)
        self.assertEqual(self.structure(CONFIG=image_config(Healthcheck=None), SERVER="false").returncode, 0)

    def test_the_source_and_revision_labels_are_required(self):
        for missing in ("org.opencontainers.image.source", "org.opencontainers.image.revision"):
            labels = {"org.opencontainers.image.source": "x", "org.opencontainers.image.revision": "y"}
            del labels[missing]
            result = self.structure(CONFIG=image_config(Labels=labels))
            self.assertNotEqual(result.returncode, 0, missing)
            self.assertIn(f"no {missing} label", result.stdout)

    def test_every_problem_is_reported_in_one_run(self):
        result = self.structure(CONFIG=image_config(User=None, Healthcheck=None, Labels={}))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count("::error title=Image structure::"), 4)
        self.assertIn("4 image structure problem(s)", result.stdout)

    def test_a_secret_finding_fails_without_printing_what_matched(self):
        found = json.dumps({"Results": [{"Target": "app/.env", "Secrets": [
            {"RuleID": "aws-access-key-id", "Severity": "CRITICAL", "StartLine": 3, "EndLine": 3, "Match": "AKIAIOSFODNN7EXAMPLE"}]}]})
        result = self.structure(SECRETS=found)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("app/.env:3-3: aws-access-key-id (CRITICAL)", result.stdout)
        self.assertNotIn("AKIA", result.stdout + result.stderr)

    def test_trivy_runs_outside_the_checkout_and_without_the_repositorys_configuration(self):
        self.assertEqual(self.structure().returncode, 0)
        calls = self.calls("trivy-calls")
        self.assertEqual(len(calls), 2)
        for call in calls:
            directory, command = call.split("|", 1)
            self.assertEqual(os.path.realpath(directory), os.path.realpath(self.temp / "app-image"))  # not the workspace
            for flag in ("--image-src remote", "--platform linux/amd64", "--config ", "--ignorefile ", "--exit-code 0", "--format json"):
                self.assertIn(flag, command)
            self.assertTrue(command.endswith(f"{STAGING}/example:run-123-2-candidate@{DIGEST}"))
        self.assertIn("--scanners secret --image-config-scanners secret --secret-config", calls[0])
        self.assertIn("trivy-secret.yaml", calls[0])
        self.assertNotIn("--secret-config", calls[1])
        self.assertIn("--scanners vuln --severity HIGH,CRITICAL", calls[1])

    def test_package_caches_and_logs_are_outside_the_secret_scan(self):
        # an npm cache index records the signed download URLs it fetched from; the playwright base image carries one (found by the canary)
        policy = (ACTION / "trivy-secret.yaml").read_text()
        for pattern in ("**/_cacache/**", "**/.npm/_logs/**", "**/.cache/uv/**", "**/node_modules/**", "**/.git/**"):
            self.assertIn(f'"{pattern}"', policy)
        self.assertNotIn("exclude-block", policy)

    def test_the_vulnerability_report_does_not_fail_the_build_by_default(self):
        report = self.vulnerabilities(("CVE-1", "CRITICAL", "1.1"), ("CVE-2", "CRITICAL", ""), ("CVE-3", "HIGH", "2"))
        result = self.structure(VULNERABILITIES=report)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Vulnerabilities with a fix: 1 CRITICAL, 1 HIGH", result.stdout)
        self.assertIn("::warning title=Vulnerabilities::1 CRITICAL", result.stdout)
        summary = self.summary.read_text()
        self.assertIn("| CVE-1 | libssl | 1 | 1.1 |", summary)
        self.assertNotIn("CVE-2", summary)  # no fix: not actionable

    def test_blocking_fails_only_on_a_fixable_critical(self):
        blocked = self.structure(BLOCK_VULNERABILITIES="true", VULNERABILITIES=self.vulnerabilities(("CVE-1", "CRITICAL", "1.1")))
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("1 fixable CRITICAL vulnerabilities", blocked.stdout)
        for findings in ([("CVE-2", "CRITICAL", "")], [("CVE-3", "HIGH", "2")], []):
            with self.subTest(findings):
                self.assertEqual(self.structure(BLOCK_VULNERABILITIES="true", VULNERABILITIES=self.vulnerabilities(*findings)).returncode, 0)

    def test_a_vulnerability_scan_that_did_not_complete_warns_unless_blocking(self):
        warned = self.structure(VULN_SCAN_FAILS="1")
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn("did not complete", warned.stdout)
        blocked = self.structure(VULN_SCAN_FAILS="1", BLOCK_VULNERABILITIES="true")
        self.assertNotEqual(blocked.returncode, 0)

    def test_an_image_built_elsewhere_is_read_from_ghcr_for_its_own_platform_with_the_job_token(self):
        ref = f"{IMAGE}@{DIGEST}"
        result = self.structure(REF=ref, PLATFORM="linux/arm64", REGISTRY_TOKEN=TOKEN)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls("crane-calls"), [f"config --platform linux/arm64 {ref}"])
        self.assertTrue(all("--platform linux/arm64" in call for call in self.calls("trivy-calls")))
        self.assertEqual(list(json.loads((self.temp / "app-image/docker-ghcr/config.json").read_text())["auths"]), ["ghcr.io"])
        self.assertNotIn(TOKEN, "\n".join(self.calls("crane-calls") + self.calls("trivy-calls")) + result.stdout)

    def test_bad_inputs_are_refused_before_any_registry_read(self):
        for overrides in ({"REF": "image:tag"}, {"REF": f"{IMAGE}@{DIGEST}", "PLATFORM": "linux/s390x"}, {"SERVER": "yes"}, {"BLOCK_VULNERABILITIES": "1"}):
            with self.subTest(overrides):
                self.assertNotEqual(self.structure(**overrides).returncode, 0)
        self.assertEqual(self.calls("crane-calls"), [])

    def test_nothing_is_checked_when_the_build_did_not_stage_anything(self):
        (self.root / "runner/app-image/digest").write_text("\n")
        self.assertNotEqual(self.structure().returncode, 0)
        self.assertEqual(self.calls("crane-calls"), [])


class Smoke(Workspace):
    """smoke.sh and pull.sh: tier 2 runs the candidate on a runner with a Docker daemon."""

    def setUp(self):
        super().setUp()
        self.env.update(IMAGE=f"{IMAGE}@{DIGEST}", REGISTRY_TOKEN=TOKEN, SMOKE_TIMEOUT="6")
        # docker records its line and answers inspect from the HEALTH sequence, one entry per call
        self.stub("docker", '''printf '%s|%s\\n' "${DOCKER_CONFIG:-}" "$*" >> "$RUNNER_TEMP/docker-calls"
case "$1" in
  login) cat > "$RUNNER_TEMP/docker-login-stdin"; [[ -z "${LOGIN_FAILS:-}" ]] || exit 1 ;;
  pull) [[ -z "${PULL_FAILS:-}" ]] || exit 1 ;;
  run) [[ -z "${RUN_STATUS:-}" ]] || exit "$RUN_STATUS" ;;
  inspect)
    n="$(cat "$RUNNER_TEMP/inspect-count" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$RUNNER_TEMP/inspect-count"
    IFS=, read -r -a sequence <<< "${STATES:-running healthy}"
    printf '%s\\n' "${sequence[$(( n < ${#sequence[@]} ? n : ${#sequence[@]} - 1 ))]//+/ }" ;;
  top) [[ "$*" == *"-eo pid,uid"* ]] || { echo "Couldn't find PID field in ps output" >&2; exit 1; }
       printf 'PID UID\\n'; while read -r uid; do printf '42 %s\\n' "$uid"; done <<< "${TOP_UIDS:-1000}" ;;
  logs|rm) ;;
esac
''')
        self.stub("sleep", "exit 0\n")

    def smoke(self, **overrides):
        return self.run_script("smoke.sh", **overrides)

    def docker_commands(self) -> list[str]:
        return [call.split("|", 1)[1] for call in self.calls("docker-calls")]

    def test_a_server_must_become_healthy_and_run_without_root(self):
        result = self.smoke(STATES="running+starting,running+starting,running+healthy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.docker_commands()
        self.assertTrue(any(c.startswith("run --detach --name app-image-smoke-") and c.endswith(f"--pull never {IMAGE}@{DIGEST}") for c in commands), commands)
        self.assertIn("top app-image-smoke-", " ".join(commands))
        self.assertIn("The server became healthy", result.stdout)

    def test_a_pull_leaves_no_registry_credential_behind(self):
        result = self.smoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls("docker-calls")
        pulls = [call for call in calls if "|pull " in call or "|login " in call]
        self.assertEqual(len(pulls), 2)
        directory = pulls[0].split("|")[0]
        self.assertTrue(directory.startswith(f"{self.temp}/docker-pull."))
        self.assertFalse(Path(directory).exists())
        self.assertEqual((self.temp / "docker-login-stdin").read_text(), TOKEN)
        self.assertNotIn(TOKEN, "\n".join(calls) + result.stdout + result.stderr)
        # the run itself uses the runner's own Docker config, never the pull's
        self.assertTrue(all(not call.split("|")[0].startswith(f"{self.temp}/docker-pull.") for call in calls if "|run " in call))

    def test_unhealthy_stopped_and_never_healthy_containers_fail(self):
        for name, states, message in [("unhealthy", "running+starting,running+unhealthy", "reported unhealthy"),
                                      ("stopped", "running+starting,exited+starting", "stopped (exited) before it became healthy"),
                                      ("no healthcheck", "running+none", "declares no HEALTHCHECK")]:
            with self.subTest(name):
                (self.temp / "inspect-count").unlink(missing_ok=True)
                result = self.smoke(STATES=states)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout)
                self.assertTrue(any(c.startswith("rm --force app-image-smoke-") for c in self.docker_commands()))

    def test_a_server_that_stays_starting_times_out(self):
        self.stub("sleep", 'echo $((SECONDS + 100)) > /dev/null; exit 0\n')
        result = self.smoke(STATES="running+starting", SMOKE_TIMEOUT="1")
        self.assertNotEqual(result.returncode, 0)

    def test_any_process_running_as_root_fails(self):
        for uids in ("0", "1000\n0"):
            with self.subTest(uids):
                result = self.smoke(TOP_UIDS=uids)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("runs as root", result.stdout)

    def test_a_command_line_tool_runs_its_command_and_must_exit_zero(self):
        ok = self.smoke(SMOKE_COMMAND="--version --short")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertTrue(any(c.endswith(f"--pull never {IMAGE}@{DIGEST} --version --short") for c in self.docker_commands()), self.docker_commands())
        self.assertFalse(any(c.startswith("inspect") for c in self.docker_commands()))
        failed = self.smoke(SMOKE_COMMAND="--version", RUN_STATUS="3")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("exited with status 3", failed.stdout)

    def test_environment_lines_reach_the_container_as_flags_and_malformed_ones_are_refused(self):
        self.assertEqual(self.smoke(SMOKE_ENV="A=1\n# note\n\nB=two words\r\n").returncode, 0)
        run = next(c for c in self.docker_commands() if c.startswith("run "))
        self.assertIn("--env A=1 --env B=two words", run)
        self.assertNotEqual(self.smoke(SMOKE_ENV="not an assignment").returncode, 0)

    def test_the_image_must_be_a_ghcr_digest_and_a_failed_pull_stops_the_run(self):
        for image in ("ghcr.io/rubio-enterprises/example:latest", f"docker.io/library/alpine@{DIGEST}", ""):
            with self.subTest(image):
                self.assertNotEqual(self.smoke(IMAGE=image).returncode, 0)
        self.assertNotEqual(self.smoke(PULL_FAILS="1").returncode, 0)
        self.assertFalse(any(c.startswith("run ") for c in self.docker_commands()))
        self.assertNotEqual(self.smoke(SMOKE_TIMEOUT="soon").returncode, 0)

    def test_the_pull_operation_only_pulls(self):
        result = self.run_script("pull.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([c.split(" ")[0] for c in self.docker_commands()], ["login", "pull"])


class Changes(Workspace):
    """changes.sh: a pull request that only touches documentation skips the image jobs."""

    def setUp(self):
        super().setUp()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "a@b.c")
        self.git("config", "user.name", "n")
        (self.repo / "README.md").write_text("x")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.env["EVENT_NAME"] = "pull_request"
        self.env["IGNORE_PATHS"] = "*.md\ndocs/*\nLICENSE\n"

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def merge(self, *files: str):
        """A pull request: a branch with one commit, merged with --no-ff, as GitHub's merge ref is."""
        self.git("checkout", "-qb", "topic")
        for file in files:
            (self.repo / file).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / file).write_text("changed")
        self.git("add", ".")
        self.git("commit", "-qm", "topic")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "topic")

    def decide(self, **overrides):
        env = {**self.env, **overrides}
        result = subprocess.run(["bash", str(ACTION / "changes.sh")], env=env, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.output.read_text().strip().splitlines()[-1].removeprefix("run=")

    def test_documentation_only_pull_requests_skip_the_image_jobs(self):
        self.merge("README.md", "docs/guide/a.md", "LICENSE", "notes.md")
        self.assertEqual(self.decide(), "false")

    def test_any_other_changed_file_runs_them(self):
        for files in (["README.md", "Dockerfile"], ["src/app.py"], ["assets/diagram.svg"], [".github/workflows/image.yml"]):
            with self.subTest(files):
                self.setUp()
                self.merge(*files)
                self.assertEqual(self.decide(), "true")

    def test_a_push_a_tag_or_a_dispatch_always_runs(self):
        self.merge("README.md")
        for event in ("push", "workflow_dispatch", "schedule", "merge_group"):
            self.assertEqual(self.decide(EVENT_NAME=event), "true", event)

    def test_trouble_reading_the_diff_runs_the_jobs(self):
        self.assertEqual(self.decide(), "true")  # one commit: no first parent
        self.assertEqual(self.decide(IGNORE_PATHS=""), "true")

    def test_extra_ignore_patterns_are_the_callers_choice(self):
        self.merge("assets/logo.png")
        self.assertEqual(self.decide(), "true")
        self.assertEqual(self.decide(IGNORE_PATHS="*.md\nassets/*\n"), "false")


class DirectBehaviour(Workspace):
    """Without the pipeline's policy the scripts behave as they did before the pipeline existed."""

    def setUp(self):
        super().setUp()
        self.env["APP_IMAGE_POLICY"] = "false"
        self.env.update(MODE="publish", REGISTRY_TOKEN=TOKEN, STUB_DIGEST=DIGEST)
        state = self.root / "runner/app-image"
        state.mkdir()
        (state / "digest").write_text(DIGEST + "\n")
        (state / "staging.ref").write_text(f"{STAGING}/other:run-123-2-publish\n")
        (self.temp / "published").mkdir()
        self.stub("crane", '''case "$1" in
  auth) cat > /dev/null ;;
  digest) if [[ -f "$RUNNER_TEMP/published/${2//[^a-zA-Z0-9]/_}" ]]; then cat "$RUNNER_TEMP/published/${2//[^a-zA-Z0-9]/_}"; else echo "MANIFEST_UNKNOWN" >&2; exit 1; fi ;;
esac
''')
        self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" = --tag ]]; then printf '%s\\n' "$STUB_DIGEST" > "$RUNNER_TEMP/published/${args[i + 1]//[^a-zA-Z0-9]/_}"; fi
done
''')

    def test_latest_a_foreign_image_name_and_a_moved_tag_still_publish(self):
        other = "ghcr.io/rubio-enterprises/music-assistant"
        (self.temp / "published" / slug(f"{other}:1.0")).write_text(OTHER + "\n")
        result = self.run_script("publish.sh", IMAGE=other, TAGS=f"{other}:1.0\n{other}:latest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.calls("docker-calls")), 1)
        self.assertIn(f"--tag {other}:latest", self.calls("docker-calls")[0])

    def test_the_other_checks_on_tags_still_apply(self):
        for tags in ("ghcr.io/rubio-enterprises/other:candidate-release", "ghcr.io/rubio-enterprises/other:-bad", "ghcr.io/rubio-enterprises/x:1"):
            self.assertNotEqual(self.run_script("publish.sh", IMAGE="ghcr.io/rubio-enterprises/other", TAGS=tags).returncode, 0, tags)

    def test_resolve_accepts_any_name_in_the_owners_namespace(self):
        script = run_block("Resolve the image and the build mode")
        output = self.root / "resolve-output"
        output.touch()
        env = {**os.environ, "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises", "GITHUB_REPOSITORY": "Rubio-Enterprises/Repo", "GITHUB_OUTPUT": str(output),
               "IMAGE_INPUT": "ghcr.io/rubio-enterprises/music-assistant", "PUBLISH": "false", "CANDIDATE": "false", "TAGS": "", "VERIFIED": "none"}
        self.assertEqual(subprocess.run(["bash", "-c", script], env={**env, "POLICY": "false"}, capture_output=True, text=True).returncode, 0)
        self.assertNotEqual(subprocess.run(["bash", "-c", script], env={**env, "POLICY": "true"}, capture_output=True, text=True).returncode, 0)

    def test_prune_and_build_resolve_any_name_without_the_policy(self):
        self.env.update(GH_TOKEN=TOKEN)
        self.stub("curl", "cat > /dev/null; printf '[]' > \"$(printf '%s\\n' \"$@\" | awk '$0==\"--output\"{getline; print}')\"; printf 200\n")
        self.assertEqual(self.run_script("prune.sh", IMAGE="ghcr.io/rubio-enterprises/music-assistant").returncode, 0)


class Prune(Workspace):
    NOW = int(time.time())

    @staticmethod
    def version(identifier: int, tags: list[str], age_hours: float, name: str | None = None) -> dict:
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(Prune.NOW - int(age_hours * 3600)))
        return {"id": identifier, "name": name or f"sha256:{identifier:064x}", "created_at": created,
                "metadata": {"container": {"tags": tags}}}

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
        # the index of the stale candidate, read through docker buildx imagetools inspect --raw
        self.stub("docker", '''printf '%s|%s\\n' "${DOCKER_CONFIG:-}" "$*" >> "$RUNNER_TEMP/docker-calls"
cat "$RUNNER_TEMP/../index.json"
''')
        self.stub("crane", "exit 99\n")  # prune needs no crane: the arm pool has none

    def pages_of(self, *pages: list[dict]):
        for number, versions in enumerate(pages, 1):
            (self.pages / f"{number}.json").write_text(json.dumps(versions))

    def prune(self, **overrides):
        return self.run_script("prune.sh", **overrides)

    def deleted(self) -> list[str]:
        return [call.split("/")[-1] for call in self.calls("api-calls") if call.startswith("DELETE ")]

    def test_deletes_only_old_versions_whose_every_tag_is_a_candidate(self):
        self.pages_of([
            self.version(1, ["candidate-10-1-abcdef012345"], 30), self.version(2, ["candidate-11-1-abcdef012345", "candidate-11-2-abcdef012345"], 50),
            self.version(3, ["candidate-12-1-abcdef012345"], 2), self.version(4, ["candidate-9-1-abcdef012345", "1.2.3"], 99),
            self.version(5, ["latest"], 99), self.version(6, [], 99), self.version(7, ["candidate-x-1-abcdef012345"], 99),
            self.version(8, ["precandidate-1-1-abcdef012345"], 99),
            # Not the current shape: no digest part, or not twelve hex digits
            self.version(9, ["candidate-10-1"], 99), self.version(10, ["candidate-10-1-abcdef01234"], 99),
            self.version(11, ["candidate-10-1-ABCDEF012345"], 99), self.version(12, ["candidate-10-1-abcdef0123456"], 99),
        ])
        result = self.prune()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.deleted(), ["1", "2"])
        self.assertIn("Pruned 2 stale candidate version(s)", result.stdout)

    def test_addresses_the_org_package_with_an_encoded_name_and_keeps_the_token_off_the_command_line(self):
        self.pages_of([self.version(1, ["candidate-10-1-abcdef012345"], 30)])
        result = self.prune(IMAGE="ghcr.io/rubio-enterprises/example/pkg")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("api-calls")[0],
                         "GET https://api.github.com/orgs/rubio-enterprises/packages/container/example%2Fpkg/versions?per_page=100&page=1")
        self.assertEqual(self.calls("api-calls")[-1],
                         "DELETE https://api.github.com/orgs/rubio-enterprises/packages/container/example%2Fpkg/versions/1")
        self.assertNotIn(TOKEN, "\n".join(self.calls("api-argv")))
        self.assertIn(f"Bearer {TOKEN}", (self.temp / "curl-stdin").read_text())

    def test_the_time_to_live_is_configurable_and_validated(self):
        self.pages_of([self.version(1, ["candidate-10-1-abcdef012345"], 2), self.version(2, ["candidate-11-1-abcdef012345"], 30)])
        self.assertEqual(self.prune(TTL_HOURS="1").returncode, 0)
        self.assertEqual(self.deleted(), ["1", "2"])
        for bad in ("0", "-1", "1h", ""):
            with self.subTest(bad):
                self.assertNotEqual(self.prune(TTL_HOURS=bad if bad else "x").returncode, 0)

    def test_pages_until_a_short_page_however_many_there_are(self):
        full = [self.version(100 + n, ["latest"], 99) for n in range(100)]
        stale = self.version(900, ["candidate-1-1-abcdef012345"], 99)
        self.pages_of(full, full, full, full, [stale] + full[:49])
        self.assertEqual(self.prune().returncode, 0)
        self.assertEqual([call.rsplit("page=", 1)[1] for call in self.calls("api-calls") if call.startswith("GET ")], ["1", "2", "3", "4", "5"])
        self.assertEqual(self.deleted(), ["900"])

    def test_failures_only_warn(self):
        self.pages_of([self.version(1, ["candidate-10-1-abcdef012345"], 30)])
        listing = self.prune(LIST_STATUS="404")
        self.assertEqual(listing.returncode, 0)
        self.assertIn("::warning::Could not list", listing.stdout)
        self.assertEqual(self.deleted(), [])
        deleting = self.prune(DELETE_STATUS="403")
        self.assertEqual(deleting.returncode, 0)
        self.assertIn("::warning::Could not delete candidate index version 1", deleting.stdout)
        self.assertIn("Admin role", deleting.stdout)


    def test_a_stale_candidate_index_takes_its_untagged_children_with_it(self):
        index, image, attestation, other = (f"sha256:{n * 64}" for n in "1234")
        self.pages_of([
            self.version(1, ["candidate-10-1-abcdef012345"], 30, index),
            self.version(11, [], 30, image), self.version(12, [], 30, attestation), self.version(13, [], 30, other),
            # promoted: the candidate tag sits beside a real tag, so neither the version nor its children are touched
            self.version(2, ["candidate-9-1-abcdef012345", "1.2.3"], 99, "sha256:" + "5" * 64),
            self.version(21, [], 99, "sha256:" + "6" * 64),
        ])
        (self.root / "index.json").write_text(json.dumps({"manifests": [{"digest": image}, {"digest": attestation}]}))
        result = self.prune()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.deleted(), ["1", "11", "12"])
        self.assertIn("Pruned 1 stale candidate version(s)", result.stdout)
        # the index is read from GHCR with the job's own login, through the buildx plugin directory when tools installed one
        reads = self.calls("docker-calls")
        self.assertEqual(len(reads), 1)
        self.assertIn(f"buildx imagetools inspect --raw {IMAGE}@{index}", reads[0])
        self.assertIn(f"{self.temp}/app-image/docker-ghcr|", reads[0])

    def test_a_bare_candidate_has_no_children_and_an_unreadable_index_leaves_them(self):
        self.pages_of([self.version(1, ["candidate-10-1-abcdef012345"], 30, "sha256:" + "1" * 64), self.version(11, [], 30, "sha256:" + "2" * 64)])
        self.assertEqual(self.prune().returncode, 0)  # no index.json: the manifest read fails
        self.assertEqual(self.deleted(), ["1"])

    def test_the_children_stay_when_the_index_could_not_be_deleted(self):
        index, image = "sha256:" + "1" * 64, "sha256:" + "2" * 64
        self.pages_of([self.version(1, ["candidate-10-1-abcdef012345"], 30, index), self.version(11, [], 30, image)])
        (self.root / "index.json").write_text(json.dumps({"manifests": [{"digest": image}]}))
        result = self.prune(DELETE_STATUS="403")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.deleted(), ["1"])
        self.assertIn("Pruned 0 stale candidate version(s)", result.stdout)


if __name__ == "__main__":
    unittest.main()
