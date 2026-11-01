from __future__ import annotations

import fnmatch
import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "actions/repository-image"
CACHE_NAMES = ("UV_CACHE_DIR", "npm_config_cache", "pnpm_config_store_dir", "GOMODCACHE", "MISE_CACHE_DIR", "CARGO_HOME")
SDK_HOME_NAMES = ("RUSTUP_HOME", "UV_PYTHON_INSTALL_DIR")
PACKAGE_SUFFIXES = {
    "UV_CACHE_DIR": "/archive-v0/package/token.py",
    "npm_config_cache": "/_cacache/content-v2/sha512/package",
    "pnpm_config_store_dir": "/v11/files/ff/package",
    "GOMODCACHE": "/example.com/package@v1.0.0/token.go",
    "MISE_CACHE_DIR": "/downloads-cas/blobs/package",
    "CARGO_HOME": "/registry/src/index.crates.io-1949cf8c6b5b557f/openssl-0.10.81/src/pkey_ctx.rs",
    "RUSTUP_HOME": "/toolchains/stable-x86_64-unknown-linux-gnu/lib/rustlib/fixture.pem",
    "UV_PYTHON_INSTALL_DIR": "/cpython-3.14.6-linux-x86_64-gnu/lib/python3.14/test/fixture.pem",
}
CONFIG_SAMPLES = (
    "home/vscode/.cache/npm/.npmrc", "home/vscode/.cache/mise/config.toml",
    "home/vscode/.cache/go/credentials.env", "home/vscode/.cache/cargo/config.toml",
    "home/vscode/.cache/cargo/credentials", "home/vscode/.cache/cargo/credentials.toml",
    "home/vscode/.local/share/rustup/settings.toml", "home/vscode/.local/share/uv/python/config.toml",
    "home/vscode/.ssh/id_ed25519", "workspaces/repo/credentials.env",
    "workspaces/repo/home/vscode/.cache/cargo/registry/credentials.env",
)


def cache_environment(filename="Dockerfile"):
    dockerfile = (ASSETS / filename).read_text().replace("\\\n", " ")
    environment = {}
    for line in dockerfile.splitlines():
        if line.startswith("ENV "):
            environment.update(item.split("=", 1) for item in shlex.split(line[4:]))
    return environment


class RepositoryImageCaches(unittest.TestCase):
    def test_mutable_cache_trees_follow_native_home_uid_remapping(self):
        for filename in ("Dockerfile", "Dockerfile.v2"):
            environment = cache_environment(filename)
            for name in CACHE_NAMES:
                with self.subTest(dockerfile=filename, cache=name):
                    self.assertIn(name, environment)
                    self.assertTrue(environment[name].startswith("/home/vscode/.cache/"), environment[name])
            for name in SDK_HOME_NAMES:
                with self.subTest(dockerfile=filename, sdk=name):
                    self.assertTrue(environment[name].startswith("/home/vscode/.local/share/"), environment[name])
            self.assertEqual(environment["UV_LINK_MODE"], "copy")
            dockerfile = (ASSETS / filename).read_text()
            self.assertIn("runuser -u vscode -- mkdir -p", dockerfile)
            self.assertNotIn("chmod 777", dockerfile)
        self.assertIn("USER vscode\nRUN", (ASSETS / "Dockerfile").read_text())

    def test_new_cache_paths_retain_package_only_secret_scan_exclusions(self):
        for filename in ("Dockerfile", "Dockerfile.v2"):
            environment = cache_environment(filename)
            for workflow in ("repository-image.yml", "devcontainer-image.yml"):
                source = (ROOT / ".github/workflows" / workflow).read_text()
                policy = source.split("skip-patterns:\n", 1)[1].split("exclude-block:", 1)[0]
                patterns = re.findall(r'^\s*- "([^"]+)"$', policy, re.MULTILINE)
                for name, suffix in PACKAGE_SUFFIXES.items():
                    with self.subTest(dockerfile=filename, workflow=workflow, cache=name):
                        sample = (environment[name] + suffix).lstrip("/")
                        self.assertTrue(any(fnmatch.fnmatchcase(sample, pattern) for pattern in patterns), sample)
                for sample in CONFIG_SAMPLES:
                    self.assertFalse(any(fnmatch.fnmatchcase(sample, pattern) for pattern in patterns), sample)

    def test_built_dev_and_ci_artifacts_probe_original_and_remapped_users(self):
        build = (ASSETS / "build.sh").read_text()
        self.assertIn('for artifact in "$IMAGE" "$CI_IMAGE"; do\n  for uid in 1000 1001; do', build)
        self.assertIn('-s -- "$uid" < "$ASSETS/cache-smoke.sh"', build)
        smoke = (ASSETS / "cache-smoke.sh").read_text()
        self.assertIn('chown -R "$uid:$uid" /home/vscode', smoke)
        self.assertEqual(smoke.count("runuser -u vscode"), 2)
        for name in CACHE_NAMES + SDK_HOME_NAMES:
            self.assertIn("$" + name, smoke)
        self.assertNotIn("chmod", smoke)
        self.assertNotIn("sudo", smoke)
