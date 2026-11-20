import base64
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import pytest

MOUNT = Path(__file__).resolve().parents[1] / "actions/repository-image/ghcr/mount-layers.mjs"
SOURCE = "rubio-enterprises/base"
TARGET = "rubio-enterprises/repo/devcontainer-ci"
BASE_LAYER = "sha256:" + "a" * 64
UNUSED_BASE_LAYER = "sha256:" + "b" * 64
PROJECT_LAYER = "sha256:" + "c" * 64
PASSWORD = "test-registry-credential"
BASIC = "Basic " + base64.b64encode(f"publisher:{PASSWORD}".encode()).decode()
BEARER = "Bearer test-scoped-token"


class Registry(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), RegistryHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.requests = []
        self.blobs = {SOURCE: {BASE_LAYER, UNUSED_BASE_LAYER}, TARGET: set()}
        self.mount_status = 201
        self.token_redirect = None
        self.mount_redirect = None


class RegistryHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, body=b"", **headers):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def record_request(self):
        url = urlsplit(self.path)
        auth = self.headers.get("Authorization")
        size = int(self.headers.get("Content-Length", "0"))
        self.server.requests.append((self.command, url.path, parse_qs(url.query), auth, size))
        return url, auth

    def do_GET(self):
        url, auth = self.record_request()
        if url.path != "/token" or auth != BASIC:
            self.respond(401)
        elif self.server.token_redirect:
            self.respond(307, Location=self.server.token_redirect)
        else:
            self.respond(200, b'{"token":"test-scoped-token"}')

    def do_HEAD(self):
        url, auth = self.record_request()
        if auth != BEARER:
            self.respond(401)
        else:
            self.respond(200 if url.path.removeprefix(f"/v2/{TARGET}/blobs/") in self.server.blobs[TARGET] else 404)

    def do_POST(self):
        url, auth = self.record_request()
        query = parse_qs(url.query)
        if auth != BEARER:
            self.respond(401)
            return
        if url.path != f"/v2/{TARGET}/blobs/uploads/":
            self.respond(404)
            return
        layer, = query.get("mount", [""])
        source, = query.get("from", [""])
        if self.server.mount_redirect:
            self.respond(307, Location=self.server.mount_redirect)
        elif self.server.mount_status == 201 and layer in self.server.blobs.get(source, set()):
            self.server.blobs[TARGET].add(layer)
            self.respond(201)
        else:
            self.respond(202)


@pytest.fixture
def serve():
    servers = []

    def start():
        registry = Registry()
        thread = Thread(target=registry.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        servers.append((registry, thread))
        return registry

    yield start
    for registry, thread in servers:
        registry.shutdown()
        thread.join()
        registry.server_close()


def run_mount(registry):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", f"""
        import {{ mountBaseLayers }} from {json.dumps(MOUNT.as_uri())};
        const manifest = (...digests) => ({{layers: digests.map(digest => ({{digest}}))}});
        await mountBaseLayers({{
          origin: process.argv[1], repository: {json.dumps(TARGET)},
          base: manifest({json.dumps(BASE_LAYER)}, {json.dumps(UNUSED_BASE_LAYER)}),
          image: manifest({json.dumps(BASE_LAYER)}, {json.dumps(PROJECT_LAYER)}),
          actor: 'publisher', password: process.env.REGISTRY_TOKEN,
        }});
        """, registry.origin],
        env={**os.environ, "REGISTRY_TOKEN": PASSWORD}, capture_output=True, text=True, timeout=10,
    )
    return result


def test_missing_base_layer_is_mounted_without_upload(serve):
    registry = serve()
    result = run_mount(registry)
    assert result.returncode == 0, result.stderr
    assert registry.blobs[TARGET] == {BASE_LAYER}
    assert [(method, path, query, size) for method, path, query, _, size in registry.requests if path != "/token"] == [
        ("HEAD", f"/v2/{TARGET}/blobs/{BASE_LAYER}", {}, 0),
        ("POST", f"/v2/{TARGET}/blobs/uploads/", {"mount": [BASE_LAYER], "from": [SOURCE]}, 0),
    ]


def test_existing_base_layer_gets_only_a_head_request(serve):
    registry = serve()
    registry.blobs[TARGET].add(BASE_LAYER)
    result = run_mount(registry)
    assert result.returncode == 0, result.stderr
    assert [(method, path, size) for method, path, _, _, size in registry.requests if path != "/token"] == [
        ("HEAD", f"/v2/{TARGET}/blobs/{BASE_LAYER}", 0),
    ]


def test_refused_mount_fails_and_names_the_layer(serve):
    registry = serve()
    registry.mount_status = 202
    result = run_mount(registry)
    assert result.returncode != 0
    assert f"refused to mount base layer {BASE_LAYER}" in result.stderr
    assert "HTTP 202" in result.stderr
    assert not registry.blobs[TARGET]
    assert all(request[-1] == 0 for request in registry.requests)


def test_registry_credential_goes_only_to_token_service(serve):
    registry = serve()
    result = run_mount(registry)
    assert result.returncode == 0, result.stderr
    assert registry.requests[0] == ("GET", "/token", {
        "service": ["ghcr.io"], "scope": [f"repository:{TARGET}:pull,push", f"repository:{SOURCE}:pull"],
    }, BASIC, 0)
    assert [request[3] for request in registry.requests[1:]] == [BEARER, BEARER]

    redirect_target = serve()
    registry.requests.clear()
    registry.token_redirect = redirect_target.origin + "/token"
    result = run_mount(registry)
    assert result.returncode != 0
    assert "token service returned HTTP 307" in result.stderr
    assert [request[1] for request in registry.requests] == ["/token"]
    assert not redirect_target.requests
    assert PASSWORD not in result.stdout + result.stderr
    assert BASIC not in result.stdout + result.stderr

    registry.token_redirect = None
    registry.blobs[TARGET].clear()
    registry.mount_redirect = redirect_target.origin + "/blobs/uploads/"
    result = run_mount(registry)
    assert result.returncode != 0
    assert f"refused to mount base layer {BASE_LAYER}" in result.stderr
    assert not redirect_target.requests
    assert BEARER not in result.stdout + result.stderr
