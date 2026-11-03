from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from test_repository_image_build_service import SERVICE
from test_repository_image_workflow import ASSETS, script

MOUNT = ASSETS / "ghcr/mount-layers.mjs"
TOKEN = "ghs_registry_secret"
BASE, BASE_CI, DEV = "org/devcontainers/base", "org/devcontainers/base-ci", "org/repo/devcontainer"
CI = "org/repo/devcontainer-ci"


def digest(char: str) -> str:
    return "sha256:" + char * 64


class FakeRegistry(ThreadingHTTPServer):
    """A registry with a bearer token service and cross-repository mounts, standing in for GHCR."""

    def __init__(self, blobs: dict[str, set[str]], mount_status: int = 201, realm_host: str | None = None):
        super().__init__(("127.0.0.1", 0), Handler)
        self.blobs = blobs
        self.mount_status = mount_status
        self.realm_host = realm_host
        self.requests: list[tuple[str, str]] = []
        self.token_requests: list[dict] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def authorized(self) -> bool:
        if self.headers.get("authorization") == f"Bearer {TOKEN}-bearer":
            return True
        realm = self.server.realm_host or self.server.host
        self.send_response(401)
        self.send_header("www-authenticate", f'Bearer realm="http://{realm}/token",service="fake"')
        self.send_header("content-length", "0")
        self.end_headers()
        return False

    def respond(self, status: int, body: bytes = b""):
        self.send_response(status)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/token":
            self.server.token_requests.append({"authorization": self.headers.get("authorization"), "query": parse_qs(url.query)})
            self.respond(200, json.dumps({"token": TOKEN + "-bearer"}).encode())
        else:
            self.respond(404)

    def do_HEAD(self):
        if not self.authorized():
            return
        match = re.fullmatch(r"/v2/(.+)/blobs/(sha256:[a-f0-9]{64})", self.path)
        self.server.requests.append(("HEAD", self.path))
        self.respond(200 if match and match[2] in self.server.blobs.get(match[1], set()) else 404)

    def do_POST(self):
        if not self.authorized():
            return
        url = urlparse(self.path)
        query = parse_qs(url.query)
        self.server.requests.append(("POST", self.path))
        match = re.fullmatch(r"/v2/(.+)/blobs/uploads/", url.path)
        source = query.get("from", [""])[0]
        wanted = query.get("mount", [""])[0]
        if match and self.server.mount_status == 201 and wanted in self.server.blobs.get(source, set()):
            self.server.blobs.setdefault(match[1], set()).add(wanted)
            self.respond(201)
        else:
            self.respond(202)


class Mount(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def manifest(self, name: str, *layers: str) -> Path:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({"layers": [{"digest": layer, "size": 1000000} for layer in layers]}))
        return path

    def serve(self, **kwargs) -> FakeRegistry:
        registry = FakeRegistry(**kwargs)
        self.addCleanup(registry.server_close)
        self.addCleanup(registry.shutdown)
        return registry

    def run_mount(self, registry: FakeRegistry, target: str, manifest: Path, *sources: str, image_host: str | None = None, **env):
        host = image_host or registry.host
        return subprocess.run(
            ["node", str(MOUNT), f"{host}/{target}:tag", str(manifest), *sources],
            env={**os.environ, "GITHUB_ACTOR": "octocat", "REGISTRY_TOKEN": TOKEN, **env}, capture_output=True, text=True)

    def test_base_layers_are_mounted_and_the_repository_layers_are_left_to_the_copy(self):
        base, feature, repository = digest("1"), digest("2"), digest("3")
        registry = self.serve(blobs={BASE: {base}})
        result = self.run_mount(registry, DEV, self.manifest("dev", base, feature, repository),
                                f"{registry.host}/{BASE}@{digest('f')}={self.manifest('base', base)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(registry.blobs[DEV], {base})
        self.assertIn(f"Mounted {base}", result.stdout)
        self.assertIn("1 layers mounted (1.0 MB), 0 already present (0.0 MB), 2 to upload (2.0 MB)", result.stdout)
        # Layers no source holds are never asked about, so no upload session is left behind for them
        self.assertEqual(sorted(path for _, path in registry.requests if feature in path or repository in path), [])
        self.assertIn(("POST", f"/v2/{DEV}/blobs/uploads/?mount={base}&from={BASE.replace('/', '%2F')}"), registry.requests)

    def test_the_ci_image_mounts_base_ci_layers_and_the_dev_images_repository_layers(self):
        base, base_ci, feature, repository = digest("1"), digest("4"), digest("2"), digest("3")
        registry = self.serve(blobs={BASE_CI: {base_ci}, DEV: {base, feature, repository}})
        result = self.run_mount(
            registry, CI, self.manifest("ci", base_ci, feature, repository),
            f"{registry.host}/{BASE_CI}@{digest('e')}={self.manifest('base-ci', base_ci)}",
            f"{registry.host}/{DEV}={self.manifest('dev', base, feature, repository)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(registry.blobs[CI], {base_ci, feature, repository})
        self.assertIn("3 layers mounted (3.0 MB), 0 already present (0.0 MB), 0 to upload (0.0 MB)", result.stdout)

    def test_a_layer_the_target_already_holds_is_not_mounted_again(self):
        base = digest("1")
        registry = self.serve(blobs={BASE: {base}, DEV: {base}})
        result = self.run_mount(registry, DEV, self.manifest("dev", base, base), f"{registry.host}/{BASE}={self.manifest('base', base)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([method for method, _ in registry.requests], ["HEAD"])
        self.assertIn("0 layers mounted (0.0 MB), 1 already present (1.0 MB)", result.stdout)

    def test_a_refused_mount_is_reported_and_leaves_the_upload_to_the_copy(self):
        base = digest("1")
        registry = self.serve(blobs={BASE: {base}}, mount_status=202)
        result = self.run_mount(registry, DEV, self.manifest("dev", base), f"{registry.host}/{BASE}={self.manifest('base', base)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"::warning::GHCR did not mount {base} from {BASE} (202)", result.stdout)
        self.assertIn("0 layers mounted (0.0 MB), 0 already present (0.0 MB), 1 to upload (1.0 MB)", result.stdout)

    def test_sources_on_another_registry_or_the_target_itself_are_ignored(self):
        base = digest("1")
        registry = self.serve(blobs={BASE: {base}, DEV: {base}})
        result = self.run_mount(registry, CI, self.manifest("ci", base),
                                f"other.example.invalid/{BASE}={self.manifest('base', base)}",
                                f"{registry.host}/{CI}={self.manifest('self', base)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping mount source other.example.invalid", result.stdout)
        self.assertEqual(registry.requests, [])

    def test_credentials_go_only_to_the_registrys_own_token_service_and_stay_out_of_the_log(self):
        base = digest("1")
        registry = self.serve(blobs={BASE: {base}})
        result = self.run_mount(registry, DEV, self.manifest("dev", base), f"{registry.host}/{BASE}={self.manifest('base', base)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        (request,) = registry.token_requests
        self.assertEqual(request["query"]["scope"], [f"repository:{DEV}:push,pull", f"repository:{BASE}:pull"])
        self.assertEqual(request["query"]["service"], ["fake"])
        self.assertTrue(request["authorization"].startswith("Basic "))
        self.assertNotIn(TOKEN, result.stdout.replace(f"::add-mask::{TOKEN}-bearer", "") + result.stderr)
        self.assertIn(f"::add-mask::{TOKEN}-bearer", result.stdout)

        foreign = self.serve(blobs={BASE: {base}}, realm_host="127.0.0.1:9")
        result = self.run_mount(foreign, DEV, self.manifest("dev2", base), f"{foreign.host}/{BASE}={self.manifest('base2', base)}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("::warning::Cross-repository mounts skipped: refusing to send credentials to http://127.0.0.1:9", result.stdout)
        self.assertEqual(foreign.token_requests, [])

    def test_an_unreachable_registry_or_unreadable_manifest_never_fails_the_publication(self):
        base = digest("1")
        unreachable = self.run_mount(self.serve(blobs={}), DEV, self.manifest("dev", base), f"127.0.0.1:9/{BASE}={self.manifest('base', base)}",
                                     image_host="127.0.0.1:9")
        self.assertEqual(unreachable.returncode, 0, unreachable.stderr)
        self.assertIn("::warning::Cross-repository mounts skipped", unreachable.stdout)
        registry = self.serve(blobs={})
        missing = self.run_mount(registry, DEV, self.root / "absent.json")
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("::warning::Cross-repository mounts skipped", missing.stdout)
        (self.root / "bad.json").write_text(json.dumps({"layers": [{"digest": "sha256:../../x", "size": 1}]}))
        invalid = self.run_mount(registry, DEV, self.root / "bad.json")
        self.assertIn("invalid layer digest", invalid.stdout)
        self.assertEqual(registry.requests, [])

    def test_a_malformed_invocation_fails_loudly(self):
        registry = self.serve(blobs={})
        for args, env in [((f"{registry.host}/{DEV}",), {}), ((f"{registry.host}/{DEV}", str(self.manifest("m")), "no-separator"), {}),
                          ((f"{registry.host}/{DEV}", str(self.manifest("m"))), {"REGISTRY_TOKEN": ""})]:
            result = subprocess.run(["node", str(MOUNT), *args], env={**os.environ, "GITHUB_ACTOR": "octocat", "REGISTRY_TOKEN": TOKEN, **env},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, (args, result.stdout))


class InputKey(unittest.TestCase):
    def test_the_mount_script_lives_outside_the_hashed_assets_so_no_image_input_key_changes(self):
        digest_script = f"import {{ assetsDigest }} from {json.dumps((ASSETS / 'key.mjs').as_uri())}; console.log(assetsDigest(process.argv[1]));"
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repository-image"
            shutil.copytree(ASSETS, copy, ignore=shutil.ignore_patterns("__pycache__"))
            with_mount = subprocess.run(["node", "--input-type=module", "-e", digest_script, str(copy)], capture_output=True, text=True)
            shutil.rmtree(copy / "ghcr")
            without = subprocess.run(["node", "--input-type=module", "-e", digest_script, str(copy)], capture_output=True, text=True)
        self.assertEqual(with_mount.returncode, 0, with_mount.stderr)
        self.assertEqual(with_mount.stdout, without.stdout)
        self.assertEqual({path.name for path in (ASSETS / "ghcr").iterdir()}, {"mount-layers.mjs"})


class Workflow(unittest.TestCase):
    PUSH = script("Push the verified image pair", SERVICE)

    def test_each_copy_is_preceded_by_its_mounts_inside_the_step_that_holds_the_token(self):
        step = SERVICE.split("- name: Push the verified image pair", 1)[1].split("- name:", 1)[0]
        for variable in ("ASSETS", "CONTEXT", "BASE", "BASE_CI", "IMAGE", "CI_IMAGE", "REGISTRY_TOKEN"):
            self.assertIn(f"{variable}: ${{{{", step)
        self.assertLess(self.PUSH.index("mount-layers.mjs"), self.PUSH.index("crane copy"))
        self.assertIn('node "$ASSETS/ghcr/mount-layers.mjs" "$artifact" "$CONTEXT/verify/$name.json" "${sources[@]}"', self.PUSH)
        self.assertIn('sources=("$BASE=$CONTEXT/verify/base.json")', self.PUSH)
        self.assertIn('sources=("$BASE_CI=$CONTEXT/verify/base-ci.json" "$IMAGE=$CONTEXT/verify/dev.json")', self.PUSH)
        # The dev image goes first, so its repository layers exist in GHCR when the CI image mounts them
        self.assertLess(self.PUSH.index('for artifact in "$IMAGE" "$CI_IMAGE"'), self.PUSH.index("mount-layers.mjs"))
        self.assertEqual(SERVICE.count("mount-layers.mjs"), 1)
        self.assertEqual(SERVICE.count("github.token"), 2)

    def test_the_step_mounts_then_copies_for_dev_then_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin = root / "bin"
            bin.mkdir()
            for name, body in {
                "crane": 'printf "crane %s\\n" "$*" >> "$RUNNER_TEMP/order"\ncase "$1" in digest) printf "%s\\n" "sha256:' + "b" * 64 + '" ;; auth) cat >/dev/null ;; esac',
                "node": 'printf "node %s\\n" "$*" >> "$RUNNER_TEMP/order"',
            }.items():
                (bin / name).write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body + "\n")
                (bin / name).chmod(0o755)
            for name in ("dev", "ci"):
                (root / f"{name}.ref").write_text(f"stage.example.invalid:5000/repo/{name}@{digest('b')}\n")
            env = {**os.environ, "PATH": f"{bin}:{os.environ['PATH']}", "RUNNER_TEMP": str(root), "ASSETS": "/assets", "CONTEXT": str(root),
                   "BASE": "ghcr.io/org/devcontainers/base@" + digest("1"), "BASE_CI": "ghcr.io/org/devcontainers/base-ci@" + digest("2"),
                   "IMAGE": "ghcr.io/org/repo/devcontainer", "CI_IMAGE": "ghcr.io/org/repo/devcontainer-ci", "REGISTRY_TOKEN": "t",
                   "GITHUB_ACTOR": "octocat", "GITHUB_SHA": "a" * 40, "ARCH": "amd64"}
            result = subprocess.run(["bash", "-e", "-c", self.PUSH], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            order = [line for line in (root / "order").read_text().splitlines() if not line.startswith(("crane auth", "crane digest"))]
        self.assertEqual(order, [
            f"node /assets/ghcr/mount-layers.mjs ghcr.io/org/repo/devcontainer {root}/verify/dev.json "
            f"ghcr.io/org/devcontainers/base@{digest('1')}={root}/verify/base.json",
            f"crane copy stage.example.invalid:5000/repo/dev@{digest('b')} ghcr.io/org/repo/devcontainer:sha-{'a' * 40}-amd64",
            f"node /assets/ghcr/mount-layers.mjs ghcr.io/org/repo/devcontainer-ci {root}/verify/ci.json "
            f"ghcr.io/org/devcontainers/base-ci@{digest('2')}={root}/verify/base-ci.json "
            f"ghcr.io/org/repo/devcontainer={root}/verify/dev.json",
            f"crane copy stage.example.invalid:5000/repo/ci@{digest('b')} ghcr.io/org/repo/devcontainer-ci:sha-{'a' * 40}-amd64",
        ])


if __name__ == "__main__":
    unittest.main()
