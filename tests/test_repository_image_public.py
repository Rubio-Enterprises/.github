"""Public repositories build on a GitHub-hosted runner with local Docker; private ones use the Build Service."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_repository_image_workflow import ASSETS, MODULE, ROOT, TEXT, config, manifest, script

JOBS = "\n" + TEXT.split("\njobs:\n", 1)[1]
PREPARE = JOBS.split("\n  prepare:\n", 1)[1].split("\n  build:\n", 1)[0]
BUILD = JOBS.split("\n  build:\n", 1)[1].split("\n\n  # Public repositories cannot use self-hosted runners", 1)[0]
PUBLIC = JOBS.split("\n  build-public:\n", 1)[1].split("\n  alias:\n", 1)[0]
PRUNE = JOBS.split("\n  prune:\n", 1)[1]
BUILD_SH = ASSETS / "public/build.sh"


class Routing(unittest.TestCase):
    def policy(self, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            env = {**os.environ, "GITHUB_OUTPUT": str(output), "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises",
                   "GITHUB_REPOSITORY": "Rubio-Enterprises/Repo", "CONFIG": ".github/.devcontainer/devcontainer.json",
                   "ON_DEFAULT_BRANCH": "true", "IMAGE_INPUT": ""}
            env.pop("PRIVATE", None)
            env.update(extra)
            result = subprocess.run(["bash", "-e", "-c", script("Resolve image and publication policy")], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_only_an_explicit_false_private_flag_selects_the_hosted_path(self):
        self.assertEqual(self.policy(PRIVATE="false")["public"], "true")
        self.assertEqual(self.policy(PRIVATE="true")["public"], "false")
        # An event that omits the field must not move a private repository onto a hosted runner
        self.assertEqual(self.policy(PRIVATE="")["public"], "false")
        self.assertEqual(self.policy()["public"], "false")

    def test_the_flag_is_the_repository_visibility_and_prepare_exports_it(self):
        self.assertIn("PRIVATE: ${{ github.event.repository.private }}", PREPARE)
        self.assertIn("public: ${{ steps.image.outputs.public }}", PREPARE)

    def test_each_visibility_runs_exactly_one_build_job(self):
        self.assertIn("if: needs.prepare.outputs.skip != 'true' && needs.prepare.outputs.public != 'true'", BUILD)
        self.assertIn("if: needs.prepare.outputs.skip != 'true' && needs.prepare.outputs.public == 'true'", PUBLIC)
        # A skipped image (key unchanged) skips both
        self.assertEqual(TEXT.count("needs.prepare.outputs.skip != 'true'"), 2)

    def test_prune_follows_whichever_build_ran(self):
        self.assertIn("needs: [prepare, build, build-public]", PRUNE)
        condition = PRUNE.split("if:", 1)[1].split("\n", 1)[0]
        self.assertIn("!cancelled()", condition)
        self.assertIn("needs.build.result == 'success' || needs.build-public.result == 'success'", condition)

    def test_the_hosted_job_is_not_the_build_service_job_and_neither_is_the_reverse(self):
        self.assertIn("runs-on: ubuntu-latest", PUBLIC)
        self.assertNotIn("vars.RUNNERS", PUBLIC)
        self.assertIn("vars.RUNNERS", BUILD)
        self.assertNotIn("ubuntu", BUILD)
        self.assertNotIn("docker/login-action", BUILD)


class HostedJobIsolation(unittest.TestCase):
    def test_no_identity_token_and_least_privilege(self):
        self.assertNotIn("id-token", PUBLIC)
        self.assertIn("permissions:\n      contents: read\n      packages: write\n", PUBLIC)
        self.assertEqual(TEXT.count("id-token: write"), 1)
        self.assertIn("id-token: write", BUILD)

    def test_never_touches_the_build_service(self):
        for text in (PUBLIC, BUILD_SH.read_text()):
            for name in ("connect.sh", "build-service", "Build Service client", "BUILDKIT_HOST", "BUILDX_BUILDER=service",
                         "ACTIONS_ID_TOKEN", "172.31.", "linux-x64-image", "RUNNERS"):
                self.assertNotIn(name, text)
        self.assertIn('bash "$ASSETS/public/build.sh"', PUBLIC)
        self.assertNotIn("operation: tools", PUBLIC)

    def test_actions_are_pinned_and_the_same_checks_run_in_order(self):
        for ref in re.findall(r"uses: (\S+)", PUBLIC):
            if ref.startswith("Rubio-Enterprises/.github/actions/"):
                self.assertEqual(ref, "Rubio-Enterprises/.github/actions/repository-image@main")
            else:
                self.assertRegex(ref, r"@[a-f0-9]{40}$")
        names = ["Check out the caller", "Verify native Docker architecture", "Set up Buildx", "Set up Node",
                 "Install the Dev Container CLI", "Log in to GHCR", "Prepare central generic build inputs",
                 "Fetch the verified Standards Package", "Install checksum-pinned Trivy",
                 "Build, audit and verify the image pair with local Docker",
                 "Scan the dev artifact for secrets against the job-local registry", "Push the verified image pair to GHCR",
                 "Publish single-platform indices and cache aliases (hosted)", "Clean job-local artifacts"]
        positions = [PUBLIC.index(f"- name: {name}") for name in names]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("if: always()", PUBLIC.split("- name: Clean job-local artifacts", 1)[1])
        self.assertIn("persist-credentials: false", PUBLIC)
        # The hosted job pushes with the login's credentials, so the token is the job's own and nothing wider
        self.assertEqual(PUBLIC.count("github.token"), 2)  # the GHCR login and the package fetch, nothing else
        self.assertNotIn("secrets.", PUBLIC)
        self.assertNotIn("continue-on-error", PUBLIC)

    def test_publication_order_is_scan_then_push_then_indices(self):
        self.assertLess(PUBLIC.index("- name: Scan the dev artifact"), PUBLIC.index("- name: Push the verified image pair"))
        self.assertLess(PUBLIC.index("- name: Push the verified image pair"),
                        PUBLIC.index("- name: Publish single-platform indices"))
        self.assertIn("--insecure", PUBLIC.split("- name: Scan the dev artifact", 1)[1].split("- name:", 1)[0])
        self.assertNotIn("publish ==", PUBLIC)


class InputKeys(unittest.TestCase):
    def test_hosted_script_lives_outside_the_hashed_assets_so_no_private_key_changes(self):
        digest = f"import {{ assetsDigest }} from {json.dumps((ASSETS / 'key.mjs').as_uri())}; console.log(assetsDigest(process.argv[1]));"
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repository-image"
            shutil.copytree(ASSETS, copy, ignore=shutil.ignore_patterns("__pycache__"))
            with_public = subprocess.run(["node", "--input-type=module", "-e", digest, str(copy)], capture_output=True, text=True)
            shutil.rmtree(copy / "public")
            without = subprocess.run(["node", "--input-type=module", "-e", digest, str(copy)], capture_output=True, text=True)
        self.assertEqual(with_public.returncode, 0, with_public.stderr)
        self.assertEqual(with_public.stdout, without.stdout)
        self.assertEqual({path.name for path in (ASSETS / "public").iterdir()}, {"build.sh"})


class HostedBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def stub(self, name, body):
        target = self.root / name
        target.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
        target.chmod(0o755)
        return target

    def fixtures(self, key):
        dev, ci = config(), config()
        dev["rootfs"]["diff_ids"] = ["base", "feature", "repo"]
        ci["rootfs"]["diff_ids"] = ["base", "runner", "feature", "repo"]
        for item in (dev, ci):
            item["config"]["Labels"].update({"org.rubio-enterprises.image.input-key": key,
                                             "org.rubio-enterprises.image.built-at": "2026-11-02T00:00:00Z"})
        items = [manifest("base"), manifest("base", "runner"), manifest("base", "feature", "repo"),
                 manifest("base", "runner", "feature", "repo")]
        for index, char in ((2, "a"), (3, "b")):
            items[index]["annotations"] = {"org.opencontainers.image.base.digest": "sha256:" + char * 64}
        fixtures = self.root / "fixtures"
        fixtures.mkdir(exist_ok=True)
        data = dict(zip(["base", "base-ci", "dev", "ci", "dev-config", "ci-config"], items + [dev, ci]))
        data["base-config"] = data["base-ci-config"] = config()
        for name, value in data.items():
            (fixtures / f"{name}.json").write_text(json.dumps(value))

    def run_build(self, key="sha256:" + "e" * 64, damage=None, **extra):
        context = self.root / "context"
        for directory in ("features", "pins", "src", "expected", "verify", "build", "package"):
            (context / directory).mkdir(parents=True, exist_ok=True)
        (context / "package/Dockerfile").write_text("FROM scratch AS ci\n")
        (context / "package/standards.tgz").write_bytes(b"fixture")
        self.fixtures(key)
        if damage:
            path = self.root / "fixtures" / f"{damage}.json"
            data = json.loads(path.read_text())
            data.pop("annotations")
            path.write_text(json.dumps(data))
        docker = self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$1" = info ]]; then echo x86_64; exit; fi
if [[ "$1" = port ]]; then echo 127.0.0.1:12345; exit; fi
for arg in "$@"; do
  if [[ "$arg" = pull && "$1" = run ]]; then : > "$CONTEXT/artifact.tar"; exit; fi
  if [[ "$arg" = digest ]]; then printf 'sha256:%064d\\n' 0; exit; fi
  if [[ "$arg" = manifest || "$arg" = config ]]; then
    case "${*: -1}" in
      *base-ci@*) name=base-ci;; *base@*) name=base;; */dev:*|*/dev@*) name=dev;; */ci:*|*/ci@*) name=ci;; *) exit 2;;
    esac
    if [[ "$arg" = config ]]; then name="$name-config"; fi
    cat "$RUNNER_TEMP/fixtures/$name.json"; exit
  fi
done
''')
        self.stub("curl", "exit 0\n")
        self.stub("python3", 'printf "audit %s\\n" "$*" >> "$RUNNER_TEMP/docker-calls"\n')
        cli = self.stub("devcontainer", 'printf "%s\\n" "$*" > "$RUNNER_TEMP/cli-call"\n')
        env = {**os.environ, "RUNNER_TEMP": str(self.root), "ASSETS": str(ASSETS), "CONTEXT": str(context), "ARCH": "amd64",
               "BASE": "ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64,
               "BASE_CI": "ghcr.io/rubio-enterprises/devcontainers/base-ci@sha256:" + "b" * 64,
               "IMAGE": "ghcr.io/rubio-enterprises/repo/devcontainer", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
               "GITHUB_SHA": "c" * 40, "GITHUB_REPOSITORY": "Rubio-Enterprises/repo", "GITHUB_SERVER_URL": "https://github.com",
               "DEVCONTAINER": str(cli), "INPUT_KEY": key, "PATH": str(self.root) + os.pathsep + os.environ["PATH"]}
        env.update(extra)
        return subprocess.run(["bash", str(BUILD_SH)], env=env, capture_output=True, text=True), context

    def test_builds_the_pair_in_a_job_local_registry_with_the_current_recipe_inputs(self):
        key = "sha256:" + "e" * 64
        result, context = self.run_build(key)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "docker-calls").read_text()
        self.assertIn("--old_base ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64
                      + " --new_base ghcr.io/rubio-enterprises/devcontainers/base-ci@sha256:" + "b" * 64, calls)
        self.assertIn("--cache-from ghcr.io/rubio-enterprises/repo/devcontainer:main-amd64", calls)
        builds = [call for call in calls.splitlines() if call.startswith("build ")]
        self.assertEqual(len(builds), 2)  # --check, then the build
        for call in builds:
            self.assertIn("--file " + str(context / "package/Dockerfile") + " --target ci", call)
            self.assertIn("--build-context pins=" + str(context / "pins"), call)
            self.assertIn("--build-context expected=" + str(context / "expected"), call)
            self.assertIn("--build-arg REPO=repo", call)
            self.assertIn("--label org.rubio-enterprises.image.input-key=" + key, call)
            self.assertRegex(call, r"--label org\.rubio-enterprises\.image\.built-at=\d{4}-\d\d-\d\dT")
            self.assertIn("--secret id=standards_package,src=" + str(context / "package/standards.tgz"), call)
            self.assertNotIn("STANDARDS_PIN", call)
        self.assertIn("--platform linux/amd64", (self.root / "cli-call").read_text())
        self.assertIn("pull --insecure " + (context / "dev.ref").read_text().strip(), calls)
        self.assertEqual(result.stdout.count("Audited registry artifact:"), 1)
        self.assertNotIn("push ghcr.io", calls)
        self.assertNotIn("buildx", calls)

    def test_runs_the_dev_cache_smoke_for_both_uids_and_leaves_the_ci_checks_to_the_release(self):
        result, _ = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "docker-calls").read_text()
        smoke = [call for call in calls.splitlines() if "-s -- " in call]
        sha = "c" * 40
        self.assertEqual(len(smoke), 2)
        for call, uid in zip(smoke, (1000, 1001)):
            self.assertIn(f"ghcr.io/rubio-enterprises/repo/devcontainer:sha-{sha}-amd64 -s -- {uid}", call)
        self.assertNotIn("devcontainer-ci", calls)
        self.assertNotIn("Runner.Listener", calls)

    def test_rejects_an_artifact_without_the_base_digest_annotation_and_a_bad_key(self):
        for name in ("dev", "ci"):
            rejected, _ = self.run_build(damage=name)
            self.assertNotEqual(rejected.returncode, 0, name)
            self.assertIn("base digest annotation", rejected.stderr)
        for bad in ("sha256:" + "f" * 3, "not-a-key"):
            rejected, _ = self.run_build(bad)
            self.assertNotEqual(rejected.returncode, 0, bad)

    def test_refuses_a_daemon_that_is_not_the_requested_native_architecture(self):
        result, _ = self.run_build(ARCH="arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("build --", (self.root / "docker-calls").read_text())


if __name__ == "__main__":
    unittest.main()
