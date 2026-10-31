from __future__ import annotations

import fnmatch
import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "actions/repository-image"
CACHE_NAMES = ("UV_CACHE_DIR", "npm_config_cache", "pnpm_config_store_dir", "GOMODCACHE", "MISE_CACHE_DIR")


def cache_environment():
    dockerfile = (ASSETS / "Dockerfile").read_text().replace("\\\n", " ")
    environment = {}
    for line in dockerfile.splitlines():
        if line.startswith("ENV "):
            environment.update(item.split("=", 1) for item in shlex.split(line[4:]))
    return environment


class RepositoryImageCaches(unittest.TestCase):
    def test_mutable_cache_trees_follow_native_home_uid_remapping(self):
        environment = cache_environment()
        for name in CACHE_NAMES:
            with self.subTest(cache=name):
                self.assertIn(name, environment)
                self.assertTrue(environment[name].startswith("/home/vscode/.cache/"), environment[name])
        self.assertEqual(environment["UV_LINK_MODE"], "copy")
        dockerfile = (ASSETS / "Dockerfile").read_text()
        self.assertIn("USER vscode\nRUN", dockerfile)
        self.assertNotIn("chmod 777", dockerfile)

    def test_new_cache_paths_retain_package_only_secret_scan_exclusions(self):
        environment = cache_environment()
        suffixes = {
            "UV_CACHE_DIR": "/archive-v0/package/token.py",
            "npm_config_cache": "/_cacache/content-v2/sha512/package",
            "pnpm_config_store_dir": "/v11/files/ff/package",
            "GOMODCACHE": "/example.com/package@v1.0.0/token.go",
            "MISE_CACHE_DIR": "/downloads-cas/blobs/package",
        }
        for workflow in ("repository-image.yml", "devcontainer-image.yml"):
            source = (ROOT / ".github/workflows" / workflow).read_text()
            policy = source.split("skip-patterns:\n", 1)[1].split("exclude-block:", 1)[0]
            patterns = re.findall(r'^\s*- "([^"]+)"$', policy, re.MULTILINE)
            for name, suffix in suffixes.items():
                with self.subTest(workflow=workflow, cache=name):
                    sample = (environment[name] + suffix).lstrip("/")
                    self.assertTrue(any(fnmatch.fnmatchcase(sample, pattern) for pattern in patterns), sample)
            for sample in ("home/vscode/.cache/npm/.npmrc", "home/vscode/.cache/mise/config.toml",
                           "home/vscode/.cache/go/credentials.env", "home/vscode/.ssh/id_ed25519",
                           "workspaces/repo/credentials.env"):
                self.assertFalse(any(fnmatch.fnmatchcase(sample, pattern) for pattern in patterns), sample)

    def test_built_dev_and_ci_artifacts_probe_original_and_remapped_users(self):
        build = (ASSETS / "build.sh").read_text()
        self.assertIn('for artifact in "$IMAGE" "$CI_IMAGE"; do\n  for uid in 1000 1001; do', build)
        self.assertIn('-s -- "$uid" < "$ASSETS/cache-smoke.sh"', build)
        smoke = (ASSETS / "cache-smoke.sh").read_text()
        self.assertIn('chown -R "$uid:$uid" /home/vscode', smoke)
        self.assertEqual(smoke.count("runuser -u vscode"), 2)
        for name in CACHE_NAMES:
            self.assertIn("$" + name, smoke)
        self.assertNotIn("chmod", smoke)
        self.assertNotIn("sudo", smoke)
