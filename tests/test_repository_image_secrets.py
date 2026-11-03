from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_repository_image_workflow import ASSETS, script

POLICY = ASSETS / "trivy-secret.yaml"

DEFAULT_SKIP_PATTERNS = (
    "**/.git/**",
    "**/node_modules/**",
    "**/go.mod",
    "**/go.sum",
    "**/package-lock.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/Pipfile.lock",
    "**/Gemfile.lock",
    "**/*.jpg",
    "**/*.png",
    "**/*.gif",
    "**/*.doc",
    "**/*.pdf",
    "**/*.bin",
    "**/*.svg",
    "**/*.socket",
    "**/*.deb",
    "**/*.rpm",
    "**/*.zip",
    "**/*.gz",
    "**/*.gzip",
    "**/*.tar",
)
CACHE_SKIP_PATTERNS = (
    "**/pnpm-store/**",
    "**/_cacache/**",
    "**/.cache/uv/**",
    "**/.cache/pip/**",
    "**/.cache/mise/npm/aube/store/**",
    "**/.cache/mise/downloads-cas/**",
)

ROOTED_PUBLIC_CACHE_SKIP_PATTERNS = (
    "usr/local/share/mise/installs/**",
    "home/*/.local/share/mise/installs/**",
    "root/.local/share/mise/installs/**",
    "mnt/devcontainer-cache/mise/installs/**",
    "var/cache/devcontainer/gomod/**",
    "home/*/.cache/gomod/**",
    "mnt/devcontainer-cache/gomod/**",
    "home/*/go/pkg/mod/**",
    "root/go/pkg/mod/**",
    "home/*/.bun/install/cache/**",
    "root/.bun/install/cache/**",
    "var/cache/devcontainer/bun/**",
    "mnt/devcontainer-cache/bun/**",
    "home/*/.local/share/pnpm/store/**",
    "root/.local/share/pnpm/store/**",
    "var/cache/devcontainer/uv/**",
    "mnt/devcontainer-cache/uv/**",
    "home/*/.cache/cargo/registry/**",
    "home/*/.local/share/rustup/toolchains/**",
    "home/*/.local/share/uv/python/*-*-*/**",
)


RUBY_PUBLIC_CACHE_SKIP_PATTERNS = (
    "usr/local/bundle/gems/**",
    "usr/local/bundle/cache/*.gem",
    "usr/local/bundle/*/*/gems/**",
    "usr/local/bundle/*/*/cache/*.gem",
    "home/*/.gem/gems/**",
    "home/*/.gem/cache/*.gem",
    "root/.gem/gems/**",
    "root/.gem/cache/*.gem",
    "home/*/.gem/*/*/gems/**",
    "home/*/.gem/*/*/cache/*.gem",
    "root/.gem/*/*/gems/**",
    "root/.gem/*/*/cache/*.gem",
    "home/*/.local/share/gem/*/*/gems/**",
    "home/*/.local/share/gem/*/*/cache/*.gem",
    "root/.local/share/gem/*/*/gems/**",
    "root/.local/share/gem/*/*/cache/*.gem",
    "home/*/.cache/*/bundle/*/*/gems/**",
    "home/*/.cache/*/bundle/*/*/cache/*.gem",
    "root/.cache/*/bundle/*/*/gems/**",
    "root/.cache/*/bundle/*/*/cache/*.gem",
    "workspaces/*/.bundle/*/*/gems/**",
    "workspaces/*/.bundle/*/*/cache/*.gem",
)


class SecretPolicyTests(unittest.TestCase):
    def test_scan_preserves_all_defaults_and_package_only_exclusions(self):
        policy = POLICY.read_text()
        patterns = tuple(re.findall(r'^  - "([^"]+)"$', policy, re.MULTILINE))
        self.assertEqual(patterns, DEFAULT_SKIP_PATTERNS + CACHE_SKIP_PATTERNS + ROOTED_PUBLIC_CACHE_SKIP_PATTERNS + RUBY_PUBLIC_CACHE_SKIP_PATTERNS)

    def test_trivy_download_is_amd64_and_checksum_verified_before_extraction(self):
        install = script("Install checksum-pinned Trivy")
        self.assertIn("version=0.76.1", install)
        self.assertIn("c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0", install)
        self.assertIn("Linux-64bit.tar.gz", install)
        self.assertNotIn("ARM64", install)
        self.assertLess(install.index("sha256sum --check --strict"), install.index("tar -xzf"))
        self.assertNotIn("latest", install)
        self.assertNotIn("checksums.txt", install)


@unittest.skipUnless(os.environ.get("TRIVY_TEST_BINARY"), "set TRIVY_TEST_BINARY to a verified Trivy 0.76.1 binary")
class RealTrivyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root / "image"
        self.fixture.mkdir()
        body = script("Scan the dev artifact for secrets")
        self.policy = POLICY.read_text()
        self.config = self.root / "policy.yaml"
        self.config.write_text(self.policy)
        self.template = next(line[len("template='"):-1] for line in body.splitlines() if line.startswith("template='"))
        self.token = "ghp_" + "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0hJ3lM6"

    def scan(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            os.environ["TRIVY_TEST_BINARY"], "fs", "--scanners", "secret", "--exit-code", "1",
            "--config", "", "--ignorefile", "", "--secret-config", str(self.config),
            "--cache-dir", str(self.root / "cache"), "--disable-telemetry", "--skip-version-check",
            "--format", "template", "--template", self.template, str(self.fixture),
        ], capture_output=True, text=True, check=False)

    def put(self, name: str, content: str) -> None:
        path = self.fixture / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_first_party_and_uncached_venv_credentials_fail_without_leaking(self) -> None:
        names = ("app/credentials.env", ".venv/lib/python3.13/site-packages/pkg/credentials.env")
        for name in names:
            self.put(name, "TOKEN=" + self.token + "\n")
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for name in names:
            self.assertIn(name, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_only_exact_sdk_prose_is_excluded_not_other_credentials(self) -> None:
        name = ".venv/lib/python3.12/site-packages/anthropic/resources/beta/agents/agents.py"
        prose = '\n'.join((
            'sending `{"type": "service_account"}` stores the default.',
            'send null or `{"type": "service_account"}` to restore the default.',
            'Always present; `{"type": "service_account"}` when runs carry no customer-named',
        ))
        self.put(name, prose)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.put(name, prose + '\n{"type": "service_account"}\nTOKEN=' + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("gcp-service-account", result.stdout)
        self.assertIn("github-pat", result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_package_caches_are_excluded_but_first_party_source_is_scanned(self) -> None:
        paths = (
            "mnt/devcontainer-cache/pnpm-store/v11/files/ff/" + "0" * 126,
            "mnt/devcontainer-cache/npm/_cacache/content-v2/sha512/fixture",
            "root/.cache/uv/archive-v0/fixture/pkg/credentials.env",
            "root/.cache/pip/http-v2/fixture",
            "root/.cache/mise/npm/aube/store/v1/files/fixture",
            "root/.cache/mise/downloads-cas/blobs/fixture",
        )
        self.assertEqual(len(paths), len(CACHE_SKIP_PATTERNS))
        for path in paths:
            self.put(path, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = "app/credentials.env"
        self.put(source, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(source, result.stdout)
        for path in paths:
            self.assertNotIn(path, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_repository_image_home_caches_exclude_packages_not_nearby_config(self) -> None:
        from test_repository_image_cache import CONFIG_SAMPLES, PACKAGE_SUFFIXES, cache_environment
        environment = cache_environment("frozen-repository-image-v2.Dockerfile")
        for name, suffix in PACKAGE_SUFFIXES.items():
            self.put((environment[name] + suffix).lstrip("/"), "TOKEN=" + self.token)
        self.put((environment["UV_PYTHON_INSTALL_DIR"] + "/pypy-3.11.13-linux-x86_64-gnu/lib/fixture.py").lstrip("/"), self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in CONFIG_SAMPLES:
            self.put(path, "TOKEN=" + self.token)
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for path in CONFIG_SAMPLES:
            self.assertIn(path, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    @unittest.skipUnless(os.environ.get("PUBLIC_OPENSSL_SOURCE"), "PUBLIC_OPENSSL_SOURCE must name the verified public crate source")
    def test_public_cargo_fixture_is_skipped_without_hiding_first_party_keys(self) -> None:
        from test_repository_image_cache import cache_environment
        data = Path(os.environ["PUBLIC_OPENSSL_SOURCE"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "f5a2f708afdd360135caa7c7984d639003dec6b012534b5c538fc9ca452aaaaa")
        home = cache_environment("frozen-repository-image-v2.Dockerfile").get("CARGO_HOME", "/home/vscode/.cargo")
        public = (home + "/registry/src/index.crates.io-1949cf8c6b5b557f/openssl-0.10.81/src/pkey_ctx.rs").lstrip("/")
        self.put(public, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = "workspaces/project/src/pkey_ctx.rs"
        self.put(source, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(source, result.stdout)
        self.assertNotIn(public, result.stdout)
        self.assertIn("private-key (HIGH)", result.stdout)
        self.assertNotIn("-----BEGIN ", result.stdout + result.stderr)

    def public_ruby_gem_fixtures(self) -> dict[str, str]:
        prefixes = (
            "usr/local/bundle/gems/", "usr/local/bundle/cache/",
            "usr/local/bundle/ruby/3.4.0/gems/", "usr/local/bundle/ruby/3.4.0/cache/",
            "home/vscode/.gem/gems/", "home/vscode/.gem/cache/",
            "root/.gem/gems/", "root/.gem/cache/",
            "home/vscode/.gem/ruby/3.4.0/gems/", "home/vscode/.gem/ruby/3.4.0/cache/",
            "root/.gem/ruby/3.4.0/gems/", "root/.gem/ruby/3.4.0/cache/",
            "home/vscode/.local/share/gem/ruby/3.4.0/gems/", "home/vscode/.local/share/gem/ruby/3.4.0/cache/",
            "root/.local/share/gem/ruby/3.4.0/gems/", "root/.local/share/gem/ruby/3.4.0/cache/",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/gems/",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/cache/",
            "root/.cache/app/bundle/ruby/3.4.0/gems/",
            "root/.cache/app/bundle/ruby/3.4.0/cache/",
            "workspaces/project/.bundle/ruby/3.4.0/gems/", "workspaces/project/.bundle/ruby/3.4.0/cache/",
        )
        fixtures = {(prefix + "public-package.gem" if prefix.endswith("/cache/") else
                     prefix + "public-package/lib/credential.rb"): "TOKEN=" + self.token for prefix in prefixes}
        self.assertEqual(len(fixtures), len(RUBY_PUBLIC_CACHE_SKIP_PATTERNS))
        return fixtures

    @unittest.skipUnless(os.environ.get("PUBLIC_RUBY_GEM_CLIENT"), "PUBLIC_RUBY_GEM_CLIENT must name the verified public SDK source")
    def test_public_gem_documentation_is_skipped_without_hiding_first_party_jwts(self) -> None:
        data = Path(os.environ["PUBLIC_RUBY_GEM_CLIENT"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), "d7ad9595238c4f05b573ba7ab67d34dc56154caef5c7b9adffca66a6e7934651")
        public = "home/vscode/.cache/app/bundle/ruby/3.4.0/gems/aws-sdk-core-3.261.0/lib/aws-sdk-ssooidc/client.rb"
        self.put(public, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = "workspaces/project/src/client.rb"
        self.put(source, data.decode())
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(source, result.stdout)
        self.assertNotIn(public, result.stdout)
        self.assertEqual(result.stdout.count("jwt-token (MEDIUM)"), 4)

    def public_cache_fixtures(self) -> dict[str, str]:
        private_key = "-----BEGIN " + "PRIVATE KEY-----\n" + "YWFh" * 16 + "\n-----END PRIVATE KEY-----\n"
        stripe = "sk_live_" + "aB3dE6gH9jK2mN5pQ8sT1vW4"
        go = "go/1.26.4/src/crypto/x509/platform_root_key.pem"
        module = "golang.org/toolchain@v0.0.1-go1.26.4.linux-arm64/src/crypto/x509/platform_root_key.pem"
        bun = "bun-types@1.3.14/docs/runtime/secrets.mdx"
        return {
            "usr/local/share/mise/installs/" + go: private_key,
            "home/vscode/.local/share/mise/installs/" + go: private_key,
            "root/.local/share/mise/installs/" + go: private_key,
            "mnt/devcontainer-cache/mise/installs/" + go: private_key,
            "var/cache/devcontainer/gomod/" + module: private_key,
            "home/vscode/.cache/gomod/" + module: private_key,
            "home/vscode/.cache/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/pkg/src/fixture.rs": private_key,
            "home/vscode/.local/share/rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/fixture.pem": private_key,
            "home/vscode/.local/share/uv/python/cpython-3.14.6-linux-x86_64-gnu/lib/fixture.py": private_key,
            "mnt/devcontainer-cache/gomod/" + module: private_key,
            "home/vscode/go/pkg/mod/" + module: private_key,
            "root/go/pkg/mod/" + module: private_key,
            "home/vscode/.bun/install/cache/" + bun: stripe,
            "root/.bun/install/cache/" + bun: stripe,
            "var/cache/devcontainer/bun/" + bun: stripe,
            "mnt/devcontainer-cache/bun/" + bun: stripe,
            "home/vscode/.local/share/pnpm/store/v10/files/fixture": self.token,
            "root/.local/share/pnpm/store/v10/files/fixture": self.token,
            "var/cache/devcontainer/uv/archive-v0/pkg/credentials.env": self.token,
            "mnt/devcontainer-cache/uv/archive-v0/pkg/credentials.env": self.token,
            **self.public_ruby_gem_fixtures(),
        }

    def test_rooted_public_toolchain_and_package_caches_are_excluded(self) -> None:
        fixtures = self.public_cache_fixtures()
        self.assertEqual(len(fixtures), len(ROOTED_PUBLIC_CACHE_SKIP_PATTERNS + RUBY_PUBLIC_CACHE_SKIP_PATTERNS))
        for name, content in fixtures.items():
            self.put(name, content)
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_rooted_cache_exclusions_keep_home_config_source_and_nonpackage_caches(self) -> None:
        for name, content in self.public_cache_fixtures().items():
            self.put(name, content)
        from test_repository_image_cache import CONFIG_SAMPLES
        protected = CONFIG_SAMPLES + (
            "home/vscode/.npmrc", "home/vscode/.config/mise/config.toml",
            "home/vscode/.cache/npm/.npmrc", "home/vscode/.cache/mise/config.toml",
            "home/vscode/.cache/credentials.env",
            "workspaces/project/home/vscode/.cache/gomod/credentials.env",
            "home/vscode/.bun/bunfig.toml", "home/vscode/.npm/_logs/credentials.env",
            "usr/local/share/mise/config.toml", "var/cache/devcontainer/turbo/credentials.env",
            "workspaces/project/src/credentials.ts",
            "workspaces/project/usr/local/share/mise/installs/go/credentials.env",
            "workspaces/project/var/cache/devcontainer/gomod/credentials.env",
            "workspaces/project/home/vscode/.bun/install/cache/credentials.env",
            "home/vscode/.gem/credentials", "root/.gem/credentials",
            "home/vscode/.bundle/config", "root/.bundle/config", "usr/local/bundle/config",
            "home/vscode/.gemrc", "home/vscode/.config/gem/credentials",
            "home/vscode/.gem/ruby/3.4.0/config", "usr/local/bundle/ruby/3.4.0/config",
            "home/vscode/.cache/app/bundle/install.log",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/config",
            "workspaces/project/.bundle/config", "workspaces/project/src/credentials.rb",
            "workspaces/project/.bundle/ruby/3.4.0/config",
            "workspaces/project/home/vscode/.gem/ruby/3.4.0/gems/pkg/credentials.rb",
            "workspaces/project/home/vscode/.cache/app/bundle/ruby/3.4.0/gems/pkg/credentials.rb",
            "workspaces/project/usr/local/bundle/gems/pkg/credentials.rb",
        )
        for name in protected:
            self.put(name, "TOKEN=" + self.token)
        fixtures = self.public_cache_fixtures()
        self.put("home/vscode/.ssh/id_rsa", fixtures["usr/local/share/mise/installs/go/1.26.4/src/crypto/x509/platform_root_key.pem"])
        self.put("workspaces/project/src/payment.ts", fixtures["home/vscode/.bun/install/cache/bun-types@1.3.14/docs/runtime/secrets.mdx"])
        result = self.scan()
        self.assertEqual(result.returncode, 1, result.stderr)
        for name in (*protected, "home/vscode/.ssh/id_rsa", "workspaces/project/src/payment.ts"):
            self.assertIn(name, result.stdout)
        for finding in ["github-pat (CRITICAL)", "private-key (HIGH)", "stripe-secret-token (CRITICAL)"]:
            self.assertIn(finding, result.stdout)
        for name in self.public_cache_fixtures():
            self.assertNotIn(name, result.stdout)
        self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_package_git_cache_configs_still_detect_credentials(self) -> None:
        paths = (
            "home/vscode/.cache/cargo/git/db/private-repository/config",
            "usr/local/bundle/cache/bundler/git/private-repository/config",
            "home/vscode/.gem/ruby/3.4.0/cache/bundler/git/private-repository/config",
            "home/vscode/.cache/app/bundle/ruby/3.4.0/cache/bundler/git/private-repository/config",
            "home/vscode/.bundle/cache/git/private-repository/config",
            "root/.bundle/cache/git/private-repository/config",
            "workspaces/project/.bundle/ruby/3.4.0/cache/bundler/git/private-repository/config",
        )
        protected = {name: '[remote "origin"]\nurl = https://' + self.token + '@github.com/org/repository.git\n' for name in paths}
        for name, content in protected.items():
            self.put(name, content)
        for result in (self.scan(), self.scan_image([], [], protected)):
            self.assertEqual(result.returncode, 1, result.stderr)
            for name in protected:
                self.assertIn(name, result.stdout)
            self.assertIn("github-pat (CRITICAL)", result.stdout)
            self.assertNotIn(self.token, result.stdout + result.stderr)

    def scan_image(self, environment: list[str], history: list[dict], files: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode="w") as contents:
            for name, content in {**self.public_cache_fixtures(), **(files or {})}.items():
                data = content.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                contents.addfile(member, io.BytesIO(data))
        image_config = json.dumps({
            "architecture": "arm64", "os": "linux",
            "config": {"Env": environment}, "history": history + [{"created_by": "COPY fixture /"}],
            "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(layer.getvalue()).hexdigest()]},
        }).encode()
        config_name = hashlib.sha256(image_config).hexdigest() + ".json"
        archive = self.root / "fixture-image.tar"
        entries = {config_name: image_config, "layer.tar": layer.getvalue(), "manifest.json": json.dumps([
            {"Config": config_name, "RepoTags": ["cache-fixture:latest"], "Layers": ["layer.tar"]}
        ]).encode()}
        with tarfile.open(archive, "w") as contents:
            for name, data in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                contents.addfile(member, io.BytesIO(data))
        return subprocess.run([
            os.environ["TRIVY_TEST_BINARY"], "image", "--input", str(archive),
            "--scanners", "secret", "--image-config-scanners", "secret", "--exit-code", "1",
            "--config", "", "--ignorefile", "", "--secret-config", str(self.config),
            "--cache-dir", str(self.root / "image-cache"), "--disable-telemetry", "--skip-version-check",
            "--format", "template", "--template", self.template,
        ], capture_output=True, text=True, check=False)

    def test_public_cache_skips_never_hide_image_environment_or_history(self) -> None:
        result = self.scan_image([], [])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for environment, history in [(["TOKEN=" + self.token], []),
                                     ([], [{"created_by": "RUN export TOKEN=" + self.token, "empty_layer": True}])]:
            result = self.scan_image(environment, history)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("github-pat", result.stdout)
            self.assertNotIn(self.token, result.stdout + result.stderr)

    def test_default_dependency_git_and_lockfile_paths_are_excluded(self) -> None:
        paths = (
            "node_modules/pkg/credentials.env", ".git/config",
            "pnpm-lock.yaml", "package-lock.json",
        )
        for path in paths:
            self.put(path, json.dumps({"TOKEN": self.token}))
        result = self.scan()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.token, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
