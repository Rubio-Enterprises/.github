from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_repository_image_workflow import ASSETS, ROOT, TEXT, audit_module, config, manifest, script

SERVICE = TEXT.split("\n  build-service:\n", 1)[1].split("\n\n  alias:\n", 1)[0]
MODULE = (ASSETS / "image.mjs").as_uri()
STAGING = "stage.example.invalid:5000/rubio-enterprises/repo"
TAG = "run-123-1"
FEATURES, DEV, CI = ("sha256:" + char * 64 for char in "fdc")
LAYERS = ["sha256:" + char * 64 for char in "123"]  # base, feature, repository


def layer(files: dict[str, bytes]) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as contents:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            contents.addfile(member, io.BytesIO(body))
    return gzip.compress(data.getvalue())


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {
            **os.environ, "RUNNER_TEMP": str(self.root), "GITHUB_ENV": str(self.root / "github-env"),
            "GITHUB_PATH": str(self.root / "github-path"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
        }

    def stub(self, name: str, body: str) -> Path:
        target = self.bin / name
        target.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
        target.chmod(0o755)
        return target

    def calls(self, name: str) -> list[str]:
        file = self.root / name
        return file.read_text().splitlines() if file.exists() else []


class WorkflowShape(unittest.TestCase):
    def test_service_path_is_an_opt_in_canary_and_the_machine_path_yields_to_it(self):
        self.assertRegex(TEXT, r"      build-service:\n        description: >-\n(?:          .*\n)+        type: boolean\n        default: false\n")
        self.assertIn("!inputs.build-service && needs.prepare.outputs.publish == 'true'", TEXT)
        self.assertIn("inputs.build-service && needs.prepare.outputs.publish == 'true' &&\n      needs.prepare.outputs.skip != 'true'", SERVICE)
        self.assertEqual(TEXT.count("id-token: write"), 2)  # the input's description and the one job that needs it
        self.assertIn("permissions:\n      contents: read\n      packages: write\n      id-token: write\n", SERVICE)

    def test_service_job_is_a_client_on_the_image_route_with_no_hosted_fallback_or_daemon(self):
        self.assertIn("runs-on: ${{ fromJSON(vars.RUNNERS || '{}').image || fromJSON('[\"linux-x64-image\"]') }}", SERVICE)
        self.assertNotIn("ubuntu", SERVICE)
        self.assertIn("timeout-minutes: 45", SERVICE)
        self.assertIn('BUILDX_NO_DEFAULT_ATTESTATIONS: "1"', SERVICE)
        self.assertIn("export BUILDX_NO_DEFAULT_ATTESTATIONS=1", (ASSETS / "build-service.sh").read_text())
        for forbidden in ("docker/login-action", "docker/setup-buildx-action", "docker run", "docker pull", "docker push",
                          "--insecure", "--cache-from", "BUILDKIT_INLINE_CACHE", "continue-on-error", "secrets:"):
            self.assertNotIn(forbidden, SERVICE)
        for ref in re.findall(r"uses: (\S+)", SERVICE):
            if ref.startswith("Rubio-Enterprises/.github/actions/"):
                self.assertEqual(ref, "Rubio-Enterprises/.github/actions/repository-image@main")
            else:
                self.assertRegex(ref, r"@[a-f0-9]{40}$")

    def test_service_steps_run_in_the_order_that_keeps_credentials_scoped(self):
        names = ["Check out the caller", "Set up Node", "Install the Dev Container CLI", "Prepare central generic build inputs",
                 "Install the Build Service client tools", "Connect to the Build Service", "Fetch the verified Standards Package",
                 "Install checksum-pinned Trivy", "Build, audit and verify the image pair on the Build Service",
                 "Scan the staged dev artifact for secrets", "Copy the verified image pair to GHCR",
                 "Publish single-platform indices and aliases", "Remove the job's certificate, credentials and build inputs"]
        positions = [SERVICE.index(f"- name: {name}") for name in names]
        self.assertEqual(positions, sorted(positions))
        # Only the copy step sees the GHCR token, and only through its environment
        self.assertEqual(SERVICE.count("github.token"), 2)  # the package fetch and the copy
        copy = SERVICE.split("- name: Copy the verified image pair to GHCR", 1)[1].split("- name:", 1)[0]
        self.assertIn("REGISTRY_TOKEN: ${{ github.token }}", copy)
        self.assertIn("if: always()", SERVICE.split("- name: Remove the job's certificate", 1)[1])
        for name in ("Scan the staged dev artifact for secrets", "Copy the verified image pair to GHCR",
                     "Publish single-platform indices and aliases", "Connect to the Build Service"):
            self.assertNotIn("${{", script(name, SERVICE))

    def test_scan_is_layer_aware_against_the_staging_registry_with_verified_tls(self):
        scan = script("Scan the staged dev artifact for secrets", SERVICE)
        self.assertIn("--image-src remote --scanners secret --image-config-scanners secret", scan)
        self.assertIn('ref="$(< "$CONTEXT/dev.ref")"', scan)
        self.assertNotIn("ci.ref", scan)
        self.assertIn("--config '' --ignorefile ''", scan)
        self.assertIn('cp "$ASSETS/trivy-secret.yaml"', scan)
        self.assertIn('if [[ "$status" != 0 ]]; then exit "$status"; fi', scan)

    def test_prune_follows_either_build_and_the_machine_path_is_unchanged(self):
        prune = TEXT.split("\n  prune:\n", 1)[1]
        self.assertIn("needs: [prepare, build, build-service]", prune)
        self.assertIn("!cancelled() && (needs.build.result == 'success' || needs.build-service.result == 'success')", prune)
        self.assertIn("- name: Build, audit and verify the image pair\n", TEXT)
        self.assertIn('bash "$ASSETS/build.sh"', TEXT)

    def test_action_documents_the_new_operation(self):
        self.assertIn("resolve, inspect, release, prepare, pack, key, alias or tools", (ASSETS / "action.yml").read_text())


OIDC_TOKEN = "oidc.jwt.value"
REQUEST_TOKEN = "runtime-secret"
LEAF = "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----"
INTERMEDIATE = "-----BEGIN CERTIFICATE-----\nINTERMEDIATE\n-----END CERTIFICATE-----"


class Connect(Workspace):
    def setUp(self):
        super().setUp()
        self.roots = self.root / "system-roots.pem"
        self.roots.write_text("SYSTEM ROOTS\n")
        # A real self-signed root, so the fingerprint check runs real openssl
        self.ca = self.root / "private-ca.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", str(self.root / "root.key"), "-out", str(self.ca), "-subj", "/CN=test root", "-days", "1"],
                       check=True, capture_output=True)
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.ca.read_text())).hexdigest()
        self.auth = self.root / "registry-auth"
        self.auth.write_text("builder:p@ss:word\n")
        self.sign_response = json.dumps({"crt": LEAF, "ca": INTERMEDIATE, "certChain": [LEAF, INTERMEDIATE]})
        self.env.update(
            BUILD_SERVICE_BUILDER="tcp://build.example.invalid:1234", BUILD_SERVICE_REGISTRY="stage.example.invalid:5000",
            BUILD_SERVICE_REGISTRY_AUTH_FILE=str(self.auth), BUILD_SERVICE_CA_URL="https://ca.example.invalid:9000",
            BUILD_SERVICE_CA_ROOT_SHA256=self.fingerprint, BUILD_SERVICE_SYSTEM_ROOTS=str(self.roots),
            GITHUB_REPOSITORY="Rubio-Enterprises/Repo", GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1",
            ACTIONS_ID_TOKEN_REQUEST_URL="https://token.example.invalid/oidc?api=1",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN=REQUEST_TOKEN, PLATFORMS="linux/amd64, linux/amd64/v2, linux/386",
            ROOT_PEM=str(self.ca), SIGN_RESPONSE=self.sign_response)
        # Every command that could carry a token records its command line; curl also plays the three endpoints
        for name in ("openssl", "jq"):
            real = shutil.which(name)
            self.stub(name, f"printf '%s %s\\n' \"{name}\" \"$*\" >> \"$RUNNER_TEMP/argv-log\"\nexec {real} \"$@\"\n")
        self.stub("curl", '''printf 'curl %s\\n' "$*" >> "$RUNNER_TEMP/argv-log"
out=""; url=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" = --output ]]; then out="${args[i + 1]}"; fi
  if [[ "${args[i]}" == http* ]]; then url="${args[i]}"; fi
done
case "$url" in
  */roots.pem) cp "$ROOT_PEM" "$out" ;;
  */1.0/sign)
    cat > "$RUNNER_TEMP/sign-body.json"
    printf '%s' "${SIGN_BODY:-$SIGN_RESPONSE}" > "$out"
    printf '%s' "${SIGN_STATUS:-201}" ;;
  *) cat > "$RUNNER_TEMP/oidc-stdin"; printf '{"count":1,"value":"oidc.jwt.value"}' ;;
esac
''')
        self.stub("crane", '''if [[ "$1 $2" = 'auth login' ]]; then
  printf '%s\\n' "$*" >> "$RUNNER_TEMP/crane-calls"
  cat > "$RUNNER_TEMP/crane-stdin-$3"
  config="$DOCKER_CONFIG/config.json"
  if [[ ! -f "$config" ]]; then echo '{}' > "$config"; fi
  jq --arg host "$3" '.auths[$host] = {auth: "redacted"}' "$config" > "$config.new" && mv "$config.new" "$config"
fi
''')
        self.stub("docker", '''printf '%s|%s\\n' "${DOCKER_CONFIG:-}" "$*" >> "$RUNNER_TEMP/docker-calls"
case "$*" in
  'buildx create'*) ;;
  'buildx inspect --bootstrap'*)
    count="$(cat "$RUNNER_TEMP/bootstrap-count" 2>/dev/null || echo 0)"; echo $((count + 1)) > "$RUNNER_TEMP/bootstrap-count"
    if (( count < ${BOOTSTRAP_FAILURES:-0} )); then exit 1; fi
    printf 'Name: build-service\\nPlatforms: %s\\n' "$PLATFORMS" ;;
esac
''')
        self.stub("timeout", 'shift; exec "$@"\n')
        self.stub("sleep", "exit 0\n")

    def connect(self, **overrides):
        env = {**self.env, **{key: value for key, value in overrides.items() if value is not None}}
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
        return subprocess.run(["bash", str(ASSETS / "connect.sh")], env=env, capture_output=True, text=True)

    def test_connects_with_a_per_job_certificate_and_two_scoped_docker_configs(self):
        (self.root / "build-service/cli-plugins").mkdir(parents=True)
        result = self.connect()
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.root / "build-service"
        self.assertTrue((directory / "client.key").read_text().startswith("-----BEGIN"))
        self.assertEqual(oct((directory / "client.key").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct((directory / "client.crt").stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(directory.stat().st_mode & 0o777), "0o700")
        self.assertEqual((directory / "client.crt").read_text(), f"{LEAF}\n{INTERMEDIATE}\n")  # the leaf, then the intermediate
        self.assertEqual((directory / "ca.crt").read_text(), self.ca.read_text())
        # Nothing the exchange used is left behind
        for name in ("roots.pem", "response.json", "client.csr", "request.json"):
            self.assertFalse((directory / name).exists(), name)
        # The sign request carries the token and a CSR in its body
        body = json.loads((self.root / "sign-body.json").read_text())
        self.assertEqual(body["ott"], OIDC_TOKEN)
        self.assertTrue(body["csr"].startswith("-----BEGIN CERTIFICATE REQUEST-----"))
        self.assertEqual(set(body), {"csr", "ott"})
        self.assertIn(f"Authorization: Bearer {REQUEST_TOKEN}", (self.root / "oidc-stdin").read_text())
        argv = "\n".join(self.calls("argv-log"))
        self.assertIn("curl --fail --silent --show-error --insecure --max-time 20 https://ca.example.invalid:9000/roots.pem", argv)
        self.assertIn("audience=homelab-build-service", argv)
        self.assertIn("--cacert " + str(directory / "ca.crt"), argv)
        self.assertIn("https://ca.example.invalid:9000/1.0/sign", argv)
        self.assertIn("::add-mask::" + OIDC_TOKEN, result.stdout)
        self.assertIn("::add-mask::p@ss:word", result.stdout)
        self.assertEqual((directory / "ca-bundle.pem").read_text(), "SYSTEM ROOTS\n" + self.ca.read_text())
        for name in ("docker-build", "docker-publish"):
            configuration = json.loads((directory / name / "config.json").read_text())
            self.assertEqual(list(configuration["auths"]), ["stage.example.invalid:5000"])
            self.assertEqual(configuration["cliPluginsExtraDirs"], [str(directory / "cli-plugins")])
        self.assertEqual((self.root / "crane-stdin-stage.example.invalid:5000").read_text(), "p@ss:word")
        self.assertNotIn("p@ss", "\n".join(self.calls("crane-calls")))
        create = next(call for call in self.calls("docker-calls") if "buildx create" in call)
        self.assertTrue(create.startswith(f"{directory}/docker-build|"))
        self.assertIn(f"--name build-service --driver remote --driver-opt cacert={directory}/ca.crt,cert={directory}/client.crt,key={directory}/client.key tcp://build.example.invalid:1234", create)
        self.assertNotIn("servername", create)
        self.assertEqual(dict(line.split("=", 1) for line in (self.root / "github-env").read_text().splitlines()), {
            "DOCKER_CONFIG": f"{directory}/docker-build", "BUILDX_BUILDER": "build-service",
            "SSL_CERT_FILE": f"{directory}/ca-bundle.pem", "STAGING": "stage.example.invalid:5000/rubio-enterprises/repo",
            "STAGING_TAG": "run-123-1"})

    def test_neither_token_ever_appears_in_a_command_line(self):
        result = self.connect()
        self.assertEqual(result.returncode, 0, result.stderr)
        logged = "\n".join(self.calls("argv-log") + self.calls("docker-calls") + self.calls("crane-calls"))
        self.assertIn("curl ", logged)  # the log is not vacuous
        self.assertIn("jq ", logged)
        self.assertIn("openssl ", logged)
        for secret in (OIDC_TOKEN, REQUEST_TOKEN, "p@ss"):
            self.assertNotIn(secret, logged)
        # Nor does the exchange put one in an argument: no jq --arg, no header or token flag, no inline request body
        text = (ASSETS / "connect.sh").read_text()
        exchange = text.split("request_certificate() {", 1)[1].split("\n}\n", 1)[0]
        for forbidden in ("--arg", "--token", '-H "Authorization', "--header \"Authorization", "-d \"", "--data \""):
            self.assertNotIn(forbidden, exchange)
        self.assertIn("<<< \"$token\"", exchange)  # the token reaches jq on stdin
        self.assertNotIn("step ca", text)

    def test_the_root_is_trusted_only_by_its_pinned_fingerprint(self):
        result = self.connect(BUILD_SERVICE_CA_ROOT_SHA256="0" * 64)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root fingerprint does not match", result.stdout)
        directory = self.root / "build-service"
        self.assertFalse((directory / "ca.crt").exists())
        self.assertFalse((directory / "roots.pem").exists())
        self.assertFalse((self.root / "sign-body.json").exists())  # the token was never sent to an untrusted CA
        self.assertEqual(self.calls("docker-calls"), [])

    def test_a_refusal_prints_the_cas_reason_and_keeps_no_key(self):
        message = 'job_workflow_ref "x" is not an allowed Build Service workflow'
        result = self.connect(SIGN_STATUS="400", SIGN_BODY=json.dumps({"message": message}))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refused (HTTP 400)", result.stdout)
        self.assertIn(message, result.stdout)
        directory = self.root / "build-service"
        for name in ("client.key", "client.crt", "response.json", "client.csr"):
            self.assertFalse((directory / name).exists(), name)
        self.assertEqual(self.calls("docker-calls"), [])

    def test_a_configured_server_name_and_audience_are_used(self):
        result = self.connect(BUILD_SERVICE_SERVERNAME="buildkitd", BUILD_SERVICE_AUDIENCE="other-audience")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(",servername=buildkitd ", next(call for call in self.calls("docker-calls") if "buildx create" in call) + " ")
        self.assertIn("audience=other-audience", "\n".join(self.calls("argv-log")))

    def test_missing_coordinates_tools_or_oidc_endpoint_fail_before_any_contact(self):
        for name in ("BUILD_SERVICE_BUILDER", "BUILD_SERVICE_REGISTRY", "BUILD_SERVICE_REGISTRY_AUTH_FILE",
                     "BUILD_SERVICE_CA_URL", "BUILD_SERVICE_CA_ROOT_SHA256"):
            result = self.connect(**{name: None})
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn(f"{name} is not set", result.stdout)
        for fingerprint in (self.fingerprint.upper(), ":".join(self.fingerprint[i:i + 2] for i in range(0, 64, 2)), "abc"):
            result = self.connect(BUILD_SERVICE_CA_ROOT_SHA256=fingerprint)
            self.assertNotEqual(result.returncode, 0, fingerprint)
            self.assertIn("64 lowercase hex digits", result.stdout)
        result = self.connect(ACTIONS_ID_TOKEN_REQUEST_URL=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("grant id-token: write", result.stdout)
        self.assertEqual(self.calls("docker-calls"), [])
        self.assertFalse((self.root / "sign-body.json").exists())
        self.auth.write_text("no-colon\n")
        self.assertIn("user:password", self.connect().stdout)
        # Only builtins run before the tool check, so an empty PATH shows which tool is missing first
        empty = self.root / "empty-path"
        empty.mkdir()
        bash = shutil.which("bash")
        result = subprocess.run([bash, str(ASSETS / "connect.sh")], env={**self.env, "PATH": str(empty)}, capture_output=True, text=True)
        self.assertIn("curl is required", result.stdout)
        (empty / "curl").write_text("#!/bin/sh\n")
        (empty / "curl").chmod(0o755)
        result = subprocess.run([bash, str(ASSETS / "connect.sh")], env={**self.env, "PATH": str(empty)}, capture_output=True, text=True)
        self.assertIn("openssl is required", result.stdout)

    def test_a_daemon_that_does_not_answer_is_retried_once_then_fails(self):
        self.assertEqual(self.connect(BOOTSTRAP_FAILURES="1").returncode, 0)
        self.assertEqual(len([call for call in self.calls("docker-calls") if "--bootstrap" in call]), 2)
        (self.root / "bootstrap-count").unlink()
        (self.root / "docker-calls").unlink()
        result = self.connect(BOOTSTRAP_FAILURES="2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not accept this job", result.stdout)
        self.assertEqual(len([call for call in self.calls("docker-calls") if "--bootstrap" in call]), 2)

    def test_a_builder_without_native_amd64_is_rejected(self):
        result = self.connect(PLATFORMS="linux/arm64, linux/amd64, linux/386")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("native linux/amd64", result.stdout)


class BuildService(Workspace):
    def setUp(self):
        super().setUp()
        self.context = self.root / "context"
        for directory in ("src", "features", "pins", "expected", "build", "package"):
            (self.context / directory).mkdir(parents=True)
        (self.context / "package/Dockerfile").write_text("FROM scratch AS ci\n")
        with tarfile.open(self.context / "package/standards.tgz", "w:gz") as package:
            member = tarfile.TarInfo("package/config/fixture")
            member.size = 7
            package.addfile(member, io.BytesIO(b"fixture"))
        (self.context / "src/main.py").write_text("print('fixture')\n")
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        self.blobs = self.root / "blobs"
        self.blobs.mkdir()
        self.write_fixtures()
        self.env.update(
            ASSETS=str(ASSETS), CONTEXT=str(self.context), ARCH="amd64", STAGING=STAGING, STAGING_TAG=TAG,
            BUILDX_BUILDER="build-service", BASE="ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64,
            BASE_CI="ghcr.io/rubio-enterprises/devcontainers/base-ci@sha256:" + "b" * 64, INPUT_KEY="sha256:" + "e" * 64,
            GITHUB_REPOSITORY="Rubio-Enterprises/repo", GITHUB_SERVER_URL="https://github.com", BUILD_SERVICE_RETRY_DELAY="0",
            DEVCONTAINER=str(self.stub("devcontainer", 'printf "%s\\n" "$*" >> "$RUNNER_TEMP/cli-calls"\n')))
        self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$*" == *'--check'* ]]; then exit 0; fi
count_file="$RUNNER_TEMP/build-count"
count="$(cat "$count_file" 2>/dev/null || echo 0)"; echo $((count + 1)) > "$count_file"
if (( count < ${BUILD_FAILURES:-0} )); then echo "${BUILD_ERROR:-ERROR: rpc error: code = Unavailable desc = error reading from server: EOF}" >&2; exit 1; fi
''')
        self.stub("crane", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/crane-calls"
ref="${*: -1}"
case "$1" in
  digest)
    case "$ref" in */features:*) echo "$FEATURES";; */dev:*) echo "$DEV";; */ci:*) echo "$CI";; *) exit 2;; esac ;;
  mutate | rebase) ;;
  manifest | config)
    case "$ref" in *base-ci@*) name=base-ci;; *base@*) name=base;; */dev@*) name=dev;; */ci@*) name=ci;; *) exit 2;; esac
    if [[ "$1" = config ]]; then name="$name-config"; fi
    cat "$RUNNER_TEMP/fixtures/$name.json" ;;
  blob) cat "$RUNNER_TEMP/blobs/${ref##*@sha256:}" ;;
  *) exit 2 ;;
esac
''')
        self.env.update(FEATURES=FEATURES, DEV=DEV, CI=CI)

    def write_fixtures(self, repository_files=None):
        base, ci_base = manifest(LAYERS[0]), manifest(LAYERS[0], "sha256:" + "9" * 64)
        dev = manifest(*LAYERS)
        ci = manifest(LAYERS[0], "sha256:" + "9" * 64, *LAYERS[1:])
        for item, char in ((dev, "a"), (ci, "b")):
            item["annotations"] = {"org.opencontainers.image.base.digest": "sha256:" + char * 64}
        dev_config, ci_config = config(), config()
        dev_config["rootfs"]["diff_ids"] = ["base", "feature", "repo"]
        ci_config["rootfs"]["diff_ids"] = ["base", "runner", "feature", "repo"]
        for item in (dev_config, ci_config):
            item["config"]["Labels"].update({"org.rubio-enterprises.image.input-key": "sha256:" + "e" * 64,
                                              "org.rubio-enterprises.image.built-at": "2026-11-02T00:00:00Z"})
        for name, data in {"base": base, "base-ci": ci_base, "dev": dev, "ci": ci, "dev-config": dev_config,
                           "ci-config": ci_config, "base-config": config(), "base-ci-config": config()}.items():
            (self.fixtures / f"{name}.json").write_text(json.dumps(data))
        files = repository_files or {"workspaces/repo/main.py": b"print('fixture')\n"}
        (self.blobs / LAYERS[1].removeprefix("sha256:")).write_bytes(layer({"etc/feature": b"feature"}))
        (self.blobs / LAYERS[2].removeprefix("sha256:")).write_bytes(layer(files))
        (self.blobs / LAYERS[0].removeprefix("sha256:")).write_bytes(b"base layers must never be downloaded")

    def build(self, **overrides):
        return subprocess.run(["bash", str(ASSETS / "build-service.sh")], env={**self.env, **overrides}, capture_output=True, text=True)

    def test_builds_on_the_remote_builder_and_publishes_nothing_itself(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls("cli-calls"), [
            f"build --workspace-folder {self.context}/src --config {self.context}/features/devcontainer.json --platform linux/amd64 "
            f"--image-name {STAGING}/features:{TAG} --push --omit-syntax-directive --label org.opencontainers.image.source=https://github.com/Rubio-Enterprises/repo"])
        docker = self.calls("docker-calls")
        self.assertEqual(len(docker), 3)
        self.assertTrue(docker[0].startswith("buildx build --check "))
        build = docker[1]
        self.assertIn(f"--build-arg BASE={STAGING}/features@{FEATURES}", build)
        self.assertIn(f"--output type=image,name={STAGING}/dev:{TAG},push=true", build)
        self.assertIn("--provenance=false --sbom=false", build)
        self.assertIn("--target ci", build)
        self.assertIn(f"--secret id=standards_package,src={self.context}/package/standards.tgz", build)
        self.assertIn(f"--build-context pins={self.context}/pins --build-context expected={self.context}/expected", build)
        self.assertIn("--label org.rubio-enterprises.image.input-key=sha256:" + "e" * 64, build)
        self.assertRegex(build, r"--label org\.rubio-enterprises\.image\.built-at=\d{4}-\d\d-\d\dT")
        self.assertTrue(build.endswith(f"{self.context}/build"))
        for text in (build, docker[0], "\n".join(self.calls("crane-calls"))):
            for forbidden in ("--cache-from", "BUILDKIT_INLINE_CACHE", "--insecure", "ghcr.io/rubio-enterprises/repo"):
                self.assertNotIn(forbidden, text)
        smoke = docker[2]
        self.assertIn("--no-cache-filter dev-1000,dev-1001", smoke)
        self.assertIn(f"--build-arg DEV_IMAGE={STAGING}/dev@{DEV}", smoke)
        self.assertNotIn("CI_IMAGE", smoke)
        self.assertIn("--output type=cacheonly", smoke)
        self.assertTrue(smoke.endswith(f"{self.context}/smoke"))
        self.assertEqual((self.context / "smoke/Dockerfile").read_text(), (ASSETS / "smoke.Dockerfile").read_text())
        self.assertEqual((self.context / "smoke/cache-smoke.sh").read_text(), (ASSETS / "cache-smoke.sh").read_text())
        self.assertEqual((self.context / "dev.ref").read_text().strip(), f"{STAGING}/dev@{DEV}")
        self.assertEqual((self.context / "ci.ref").read_text().strip(), f"{STAGING}/ci@{CI}")
        crane = self.calls("crane-calls")
        self.assertIn(f"mutate {STAGING}/dev:{TAG} --annotation org.opencontainers.image.base.digest=sha256:{'a' * 64} -t {STAGING}/dev:{TAG}", crane)
        self.assertIn(f"rebase --platform linux/amd64 {STAGING}/dev:{TAG} --old_base {self.env['BASE']} --new_base {self.env['BASE_CI']} -t {STAGING}/ci:{TAG}", crane)
        self.assertIn("Audited registry artifact: " + f"{STAGING}/dev@{DEV}", result.stdout)

    def test_audit_downloads_and_reads_only_the_layers_above_the_base(self):
        self.assertEqual(self.build().returncode, 0)
        blobs = [call for call in self.calls("crane-calls") if call.startswith("blob ")]
        self.assertEqual(blobs, [f"blob {STAGING}/dev@{digest}" for digest in LAYERS[1:]])
        self.assertFalse((self.context / "audit").exists())

    def test_a_persisted_standards_file_in_a_repository_layer_stops_the_build_before_the_smoke_checks(self):
        self.write_fixtures({"workspaces/repo/.standards/node_modules/@rubio-enterprises/standards/package.json": b"{}"})
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Package or temporary Git state persisted in a layer", result.stderr)
        self.assertEqual(len(self.calls("docker-calls")), 2)  # check and build, never the smoke build

    def test_a_dev_image_with_no_layers_above_the_base_fails_closed(self):
        same = json.loads((self.fixtures / "base.json").read_text())
        for name in ("dev", "ci"):
            image = json.loads((self.fixtures / f"{name}.json").read_text())
            (self.fixtures / f"{name}.json").write_text(json.dumps({**image, "layers": same["layers"]}))
        (self.fixtures / "ci-base.json").write_text("{}")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("docker-calls")[2:], [])

    def test_a_connection_lost_to_a_restarting_daemon_is_retried_once(self):
        result = self.build(BUILD_FAILURES="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("retrying once", result.stdout)
        self.assertEqual(len([call for call in self.calls("docker-calls") if "--output type=image" in call]), 2)
        (self.root / "docker-calls").unlink()
        (self.root / "build-count").unlink()
        result = self.build(BUILD_FAILURES="2")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([call for call in self.calls("docker-calls") if "--output type=image" in call]), 2)
        self.assertEqual(len(self.calls("docker-calls")), 3)

    def test_other_failures_are_not_retried_and_cannot_proceed(self):
        result = self.build(BUILD_FAILURES="1", BUILD_ERROR="ERROR: process did not complete successfully: exit code: 1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([call for call in self.calls("docker-calls") if "--output type=image" in call]), 1)
        self.assertFalse((self.context / "dev.ref").exists())

    def test_rejects_other_architectures_and_malformed_keys_before_building(self):
        for overrides in ({"ARCH": "arm64"}, {"INPUT_KEY": "not-a-key"}):
            for name in ("cli-calls", "docker-calls"):
                (self.root / name).unlink(missing_ok=True)
            self.assertNotEqual(self.build(**overrides).returncode, 0, overrides)
            self.assertEqual(self.calls("docker-calls"), [], overrides)

    def test_missing_provenance_on_either_artifact_fails_verification(self):
        for name in ("dev", "ci"):
            good = (self.fixtures / f"{name}.json").read_text()
            damaged = json.loads(good)
            damaged.pop("annotations")
            (self.fixtures / f"{name}.json").write_text(json.dumps(damaged))
            result = self.build()
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn("base digest annotation", result.stderr)
            (self.fixtures / f"{name}.json").write_text(good)
            (self.root / "docker-calls").unlink(missing_ok=True)

    def test_each_step_needs_its_inputs(self):
        for name in ("STAGING", "STAGING_TAG", "BUILDX_BUILDER", "ASSETS"):
            env = {key: value for key, value in self.env.items() if key != name}
            result = subprocess.run(["bash", str(ASSETS / "build-service.sh")], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, name)


class SmokeStages(unittest.TestCase):
    DOCKERFILE = (ASSETS / "smoke.Dockerfile").read_text()

    def test_every_stage_is_forced_to_run_and_feeds_the_output(self):
        stages = re.findall(r"^FROM \S+ AS (\S+)$", self.DOCKERFILE, re.MULTILINE)
        self.assertEqual(stages, ["dev-1000", "dev-1001"])
        build = (ASSETS / "build-service.sh").read_text()
        self.assertIn("--no-cache-filter " + ",".join(stages), build)
        final = self.DOCKERFILE.split("FROM scratch\n", 1)[1]
        for stage in stages:
            self.assertIn(f"COPY --from={stage} /tmp/smoke-ok /{stage}", final)

    def test_checks_run_without_network_on_the_dev_image_only(self):
        self.assertEqual(self.DOCKERFILE.count("RUN --network=none"), 2)
        self.assertNotIn("--network=host", self.DOCKERFILE)
        self.assertEqual(len(re.findall(r"cache-smoke\.sh 100[01] &&", self.DOCKERFILE)), 2)
        self.assertEqual(self.DOCKERFILE.count("FROM ${DEV_IMAGE}"), 2)
        # The CI image's runtime and cache checks run once per base pair release in devcontainers
        for gone in ("CI_IMAGE", "runtime", "Runner.Listener", "ci-1000"):
            self.assertNotIn(gone, self.DOCKERFILE.split("ARG DEV_IMAGE", 1)[1])
        self.assertNotIn("# syntax=", self.DOCKERFILE)  # the daemon's bundled frontend: no extra image pull


class Tools(Workspace):
    def archive(self, member: str, body: bytes) -> bytes:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as contents:
            info = tarfile.TarInfo(member)
            info.size, info.mode = len(body), 0o755
            contents.addfile(info, io.BytesIO(body))
        return data.getvalue()

    def run_install(self, assets: dict[str, bytes], overrides=None, pins=None, path=None):
        tools = [
            {"name": "docker", "url": "https://example.invalid/docker.tgz", "member": "docker/docker"},
            {"name": "docker-buildx", "url": "https://example.invalid/buildx", "plugin": True},
            {"name": "crane", "url": "https://example.invalid/crane.tgz", "member": "crane"},
        ]
        for tool in tools:
            body = assets[tool["url"]]
            tool["sha256"] = (pins or {}).get(tool["name"], hashlib.sha256(body).hexdigest())
        responses = {url: base64.b64encode(body).decode() for url, body in assets.items()}
        env = {**self.env, "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", **(overrides or {})}
        if path is not None:
            env["PATH"] = path
        source = f"""
        const responses = {json.dumps(responses)};
        globalThis.fetch = async url => responses[url] ? new Response(Buffer.from(responses[url], 'base64')) : new Response('', {{ status: 404 }});
        const {{ installTools }} = await import({json.dumps(MODULE)});
        await installTools(process.env, {json.dumps(tools)});
        """
        return subprocess.run(["node", "--input-type=module", "-e", source], env=env, capture_output=True, text=True)

    def assets(self):
        return {
            "https://example.invalid/docker.tgz": self.archive("docker/docker", b"#!/bin/sh\necho docker\n"),
            "https://example.invalid/buildx": b"#!/bin/sh\necho buildx\n",
            "https://example.invalid/crane.tgz": self.archive("crane", b"#!/bin/sh\necho crane\n"),
        }

    def test_installs_checksum_pinned_tools_into_the_job_and_extends_path(self):
        node = shutil.which("node")
        path = f"{Path(node).parent}{os.pathsep}/usr/bin{os.pathsep}/bin"
        with tempfile.TemporaryDirectory() as clean:  # no docker or crane on PATH
            for name in ("node", "tar", "gzip"):
                (Path(clean) / name).symlink_to(shutil.which(name))
            result = self.run_install(self.assets(), path=clean)
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.root / "build-service"
        for name in ("docker", "crane"):
            self.assertTrue(os.access(directory / "bin" / name, os.X_OK), name)
            self.assertEqual(subprocess.check_output([directory / "bin" / name], text=True).strip(), name)
        self.assertTrue(os.access(directory / "cli-plugins/docker-buildx", os.X_OK))
        self.assertEqual((self.root / "github-path").read_text(), f"{directory}/bin\n")
        self.assertEqual(list(directory.glob("*.tgz")) + list(directory.glob("*.extract")), [])
        self.assertIsNotNone(path)

    def test_a_checksum_mismatch_installs_nothing_further(self):
        with tempfile.TemporaryDirectory() as clean:
            for name in ("node", "tar", "gzip"):
                (Path(clean) / name).symlink_to(shutil.which(name))
            result = self.run_install(self.assets(), pins={"crane": "0" * 64}, path=clean)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("crane checksum mismatch", result.stderr)
        self.assertFalse((self.root / "build-service/bin/crane").exists())
        self.assertFalse((self.root / "github-path").exists())

    def test_tools_already_on_path_are_left_alone(self):
        with tempfile.TemporaryDirectory() as clean:
            for name in ("node", "tar", "gzip"):
                (Path(clean) / name).symlink_to(shutil.which(name))
            for name in ("docker", "crane"):
                tool = Path(clean) / name
                tool.write_text("#!/bin/sh\nexit 0\n")
                tool.chmod(0o755)
            # A docker whose buildx works needs no plugin; the pins are deliberately wrong to prove no download is verified
            result = self.run_install(self.assets(), pins={name: "0" * 64 for name in ("docker", "docker-buildx", "crane")}, path=clean)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("already available"), 3)
        self.assertEqual(list((self.root / "build-service/bin").iterdir()), [])

    def test_only_linux_x64_runners_are_supported(self):
        result = self.run_install(self.assets(), overrides={"RUNNER_ARCH": "ARM64"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Linux X64", result.stderr)

    def test_the_pins_are_https_release_artifacts_with_sha256_checksums(self):
        source = f"import {{ CLIENT_TOOLS }} from {json.dumps(MODULE)}; console.log(JSON.stringify(CLIENT_TOOLS));"
        tools = json.loads(subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True, check=True).stdout)
        self.assertEqual([tool["name"] for tool in tools], ["docker", "docker-buildx", "crane"])
        for tool in tools:
            self.assertRegex(tool["sha256"], r"^[a-f0-9]{64}$")
            self.assertRegex(tool["url"], r"^https://(download\.docker\.com|github\.com)/")
            self.assertNotIn("latest", tool["url"])


class AboveBase(Workspace):
    def run_op(self, dev_layers, base_layers):
        directory = self.root / "verify"
        directory.mkdir(exist_ok=True)
        (directory / "base.json").write_text(json.dumps(manifest(*base_layers)))
        (directory / "dev.json").write_text(json.dumps(manifest(*dev_layers)))
        return subprocess.run(["node", str(ASSETS / "image.mjs"), "above-base", str(directory)], env=self.env, capture_output=True, text=True)

    def test_lists_the_dev_layers_beyond_the_base_prefix_in_order(self):
        result = self.run_op(LAYERS, LAYERS[:1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), LAYERS[1:])

    def test_no_layers_above_the_base_or_malformed_digests_fail_closed(self):
        for dev in (LAYERS[:1], [LAYERS[0], "../escape"]):
            result = self.run_op(dev, LAYERS[:1])
            self.assertNotEqual(result.returncode, 0, dev)
            self.assertEqual(result.stdout, "")


class DirectoryAudit(Workspace):
    def package(self):
        archive = self.root / "standards.tgz"
        private = b"private bundle fixture\n" * 20
        with tarfile.open(archive, "w:gz") as package:
            member = tarfile.TarInfo("package/config/private.yml")
            member.size = len(private)
            package.addfile(member, io.BytesIO(private))
        return archive, private

    def image(self, layers: list[bytes], image_config=None) -> Path:
        directory = self.root / "audit"
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir()
        (directory / "config.json").write_text(json.dumps(image_config or config()))
        for index, data in enumerate(layers):
            (directory / f"layer-{index:04d}").write_bytes(data)
        return directory

    def test_audits_compressed_registry_layers_with_the_same_rules_as_the_archive_form(self):
        package, private = self.package()
        audit_module.audit(self.image([layer({"repo/source": b"safe"})]), package)
        for files, message in (({"elsewhere/copied-policy": private}, "Shared Config Bundle content persisted"),
                               ({"repo/.standards-config/config/private": b"short"}, "Shared Config Directory persisted"),
                               ({"workspaces/repo/.git/HEAD": b"ref"}, "Package or temporary Git state persisted")):
            with self.assertRaisesRegex(ValueError, message):
                audit_module.audit(self.image([layer({"repo/source": b"safe"}), layer(files)]), package)
        with self.assertRaisesRegex(ValueError, "Build pin persisted"):
            audit_module.audit(self.image([layer({})], {"config": {"Env": ["STANDARDS_PIN=other"]}}), package)

    def test_a_directory_without_config_or_layers_is_rejected(self):
        package, _ = self.package()
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "image config and at least one layer"):
            audit_module.audit(empty, package)
        with self.assertRaisesRegex(ValueError, "image config and at least one layer"):
            audit_module.audit(self.image([]), package)

    def test_layers_are_read_in_order_and_command_line_form_works(self):
        package, private = self.package()
        directory = self.image([layer({"repo/source": b"safe"})])
        result = subprocess.run(["python3", str(ASSETS / "audit.py"), str(directory), str(package)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.image([layer({"elsewhere/copied-policy": private})])
        result = subprocess.run(["python3", str(ASSETS / "audit.py"), str(directory), str(package)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::Shared Config Bundle content persisted", result.stderr)
        self.assertNotIn("private bundle", result.stderr + result.stdout)


class PublishSteps(Workspace):
    def setUp(self):
        super().setUp()
        self.digest = "sha256:" + "b" * 64
        for name in ("dev", "ci"):
            (self.root / f"{name}.ref").write_text(f"{STAGING}/{name}@{self.digest}\n")
        self.env.update(
            CONTEXT=str(self.root), ARCH="amd64", GITHUB_SHA="a" * 40, GITHUB_ACTOR="octocat", REGISTRY_TOKEN="ghs_secret",
            IMAGE="ghcr.io/rubio-enterprises/repo/devcontainer", CI_IMAGE="ghcr.io/rubio-enterprises/repo/devcontainer-ci",
            BUILDX_BUILDER="build-service", DOCKER_CONFIG=str(self.root / "build-config"))
        self.stub("crane", '''printf '%s|%s\\n' "$DOCKER_CONFIG" "$*" >> "$RUNNER_TEMP/crane-calls"
case "$1" in
  auth) cat > "$RUNNER_TEMP/crane-stdin" ;;
  copy) exit "${COPY_STATUS:-0}" ;;
  digest) echo "${PUBLISHED_DIGEST:-$DIGEST}" ;;
esac
''')
        self.env["DIGEST"] = self.digest

    def run_step(self, name, **overrides):
        return subprocess.run(["bash", "-e", "-c", script(name, SERVICE)], env={**self.env, **overrides}, capture_output=True, text=True)

    def test_copy_uses_the_job_token_through_stdin_in_the_publish_config_only(self):
        result = self.run_step("Copy the verified image pair to GHCR")
        self.assertEqual(result.returncode, 0, result.stderr)
        publish = f"{self.root}/build-service/docker-publish"
        calls = self.calls("crane-calls")
        self.assertEqual(calls[0], f"{publish}|auth login ghcr.io --username octocat --password-stdin")
        self.assertEqual((self.root / "crane-stdin").read_text(), "ghs_secret")
        self.assertNotIn("ghs_secret", "\n".join(calls))
        self.assertTrue(all(call.startswith(publish + "|") for call in calls))
        for name, artifact in (("dev", self.env["IMAGE"]), ("ci", self.env["CI_IMAGE"])):
            self.assertIn(f"copy {STAGING}/{name}@{self.digest} {artifact}:sha-{'a' * 40}-amd64", "\n".join(calls))
        self.assertNotIn("build-config", "\n".join(calls))

    def test_a_failed_copy_or_a_digest_that_differs_from_the_verified_artifact_fails(self):
        for overrides in ({"COPY_STATUS": "2"}, {"PUBLISHED_DIGEST": "sha256:" + "c" * 64}):
            result = self.run_step("Copy the verified image pair to GHCR", **overrides)
            self.assertNotEqual(result.returncode, 0, overrides)

    def test_indices_are_published_with_the_publish_config_and_no_builder(self):
        self.stub("docker", '''printf '%s|%s|%s\\n' "$DOCKER_CONFIG" "${BUILDX_BUILDER-unset}" "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$*" == *'inspect --raw'* ]]; then printf '%s\\n' "$INDEX_JSON"; fi
''')
        index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                 "manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]}
        result = self.run_step("Publish single-platform indices and aliases", INDEX_JSON=json.dumps(index))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls("docker-calls")
        self.assertEqual(len(calls), 8)
        publish = f"{self.root}/build-service/docker-publish"
        self.assertTrue(all(call.startswith(f"{publish}|unset|buildx imagetools ") for call in calls))
        sha, image = "a" * 40, self.env["IMAGE"]
        self.assertTrue(calls[0].endswith(f"create --prefer-index=true --tag {image}:main --tag {image}:sha-{sha} {image}:sha-{sha}-amd64"))
        self.assertTrue(calls[1].endswith(f"create --prefer-index=false --tag {image}:main-amd64 {image}:sha-{sha}-amd64"))
        for bad in (dict(index, manifests=[]), dict(index, manifests=index["manifests"] * 2),
                    dict(index, manifests=[{"platform": {"os": "linux", "architecture": "arm64"}}])):
            result = self.run_step("Publish single-platform indices and aliases", INDEX_JSON=json.dumps(bad))
            self.assertNotEqual(result.returncode, 0, bad)

    def test_cleanup_removes_keys_credentials_and_the_build_context(self):
        directory = self.root / "build-service"
        directory.mkdir()
        (directory / "client.key").write_text("KEY")
        context = self.root / "repository-image-abc"
        context.mkdir()
        result = self.run_step("Remove the job's certificate, credentials and build inputs", CONTEXT=str(context))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(directory.exists())
        self.assertFalse(context.exists())
        # A context outside the runner temp directory is never removed
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.run_step("Remove the job's certificate, credentials and build inputs", CONTEXT=str(outside))
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
