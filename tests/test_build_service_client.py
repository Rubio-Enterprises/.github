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

ROOT = Path(__file__).resolve().parents[1]

ASSETS = ROOT / "actions/build-service"

MODULE = (ASSETS / "tools.mjs").as_uri()

OIDC_TOKEN = "oidc.jwt.value"

REQUEST_TOKEN = "runtime-secret"

LEAF = "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----"

INTERMEDIATE = "-----BEGIN CERTIFICATE-----\nINTERMEDIATE\n-----END CERTIFICATE-----"

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
            "SSL_CERT_FILE": f"{directory}/ca-bundle.pem", "STAGING": "stage.example.invalid:5000/rubio-enterprises/repo/run-123-1",
            "STAGING_TAG": "run-123-1"})

    def test_every_run_and_attempt_stages_into_its_own_repository(self):
        # zot ages a digest from its first push to a repository: the deterministic Features image must not share one across runs
        staged = set()
        for run, attempt in ("123", "1"), ("123", "2"), ("124", "1"):
            result = self.connect(GITHUB_RUN_ID=run, GITHUB_RUN_ATTEMPT=attempt)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = dict(line.split("=", 1) for line in (self.root / "github-env").read_text().splitlines())
            self.assertEqual(written["STAGING"], f"stage.example.invalid:5000/rubio-enterprises/repo/run-{run}-{attempt}")
            self.assertEqual(written["STAGING_TAG"], f"run-{run}-{attempt}")
            staged.add(written["STAGING"])
            (self.root / "github-env").write_text("")
        self.assertEqual(len(staged), 3)

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

    def test_the_registry_credential_is_used_without_its_line_ending(self):
        # Lane P's file ends in a newline; others have none, CRLF endings or trailing blank lines
        for content in (b"builder:s3cret\n", b"builder:s3cret", b"builder:s3cret\r\n", b"builder:s3cret\n\n", b"builder:s3cret\nignored:line\n"):
            for name in ("crane-calls", "crane-stdin-stage.example.invalid:5000", "docker-calls", "github-env"):
                (self.root / name).unlink(missing_ok=True)
            shutil.rmtree(self.root / "build-service", ignore_errors=True)
            self.auth.write_bytes(content)
            result = self.connect()
            self.assertEqual(result.returncode, 0, (content, result.stdout, result.stderr))
            self.assertEqual((self.root / "crane-stdin-stage.example.invalid:5000").read_bytes(), b"s3cret", content)
            self.assertEqual(self.calls("crane-calls")[0], "auth login stage.example.invalid:5000 --username builder --password-stdin", content)
            self.assertIn("::add-mask::s3cret\n", result.stdout, content)
            self.assertNotIn("::add-mask::s3cret\r", result.stdout, content)
            for config in ("docker-build", "docker-publish"):
                self.assertEqual(list(json.loads((self.root / "build-service" / config / "config.json").read_text())["auths"]),
                                 ["stage.example.invalid:5000"], content)
        for content in (b"", b"\n", b"no-colon\n", b":password\n", b"user:\n"):
            self.auth.write_bytes(content)
            result = self.connect()
            self.assertNotEqual(result.returncode, 0, content)
            self.assertRegex(result.stdout, r"credential file (is empty|must hold user:password)", content)

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
            tool["version"] = "1.2.3"
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
                output = 'if [ "$1" = buildx ]; then echo "github.com/docker/buildx v1.2.3 abc"; else echo "Docker version 1.2.3, build abc"; fi' if name == "docker" else 'echo "1.2.3"'
                tool.write_text("#!/bin/sh\n" + output + "\n")
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
            if tool.get("repo"):
                self.assertNotIn("latest", tool["tag"])
                self.assertNotIn("url", tool)
            else:
                self.assertRegex(tool["url"], r"^https://download\.docker\.com/")
                self.assertNotIn("latest", tool["url"])
