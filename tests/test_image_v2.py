from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import test_repository_image_workflow as legacy

ASSETS = legacy.ASSETS
LINK = ".config/mise/conf.d/standards.toml"
TARGET = "../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml"


class ImageV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "GITHUB_WORKSPACE": str(self.repo), "RUNNER_TEMP": str(self.root),
                    "GITHUB_OUTPUT": str(self.root / "outputs"), "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"),
                    "GIT_CONFIG_NOSYSTEM": "1", "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        for key in ["CONFIG", "DATA", "STATE", "CACHE"]:
            self.env[f"MISE_{key}_DIR"] = str(self.root / key.lower())
        self.env["MISE_GLOBAL_CONFIG_FILE"] = str(self.root / "global.toml")
        (self.repo / "mise.toml").write_text('[tasks.setup]\ndepends = ["standards:install"]\n[tasks."standards:install"]\nrun = "true"\n')
        (self.repo / LINK).parent.mkdir(parents=True)
        (self.repo / LINK).symlink_to(TARGET)
        (self.repo / ".github/.devcontainer").mkdir(parents=True)
        (self.repo / ".github/.devcontainer/devcontainer.json").write_text('{"features":{}}')
        self.private = b"internal package-only content\n" * 20
        self.package = self.root / "package.tgz"
        with tarfile.open(self.package, "w:gz") as package:
            for name, data in {"package/config/private.toml": self.private,
                               "package/package.json": b'{"name":"@rubio-enterprises/standards","version":"2.0.0"}'}.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                package.addfile(member, io.BytesIO(data))
        self.integrity = "sha512-" + base64.b64encode(hashlib.sha512(self.package.read_bytes()).digest()).decode()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=self.env, text=True).strip()

    def commit(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "test: v2 fixture")
        return self.git("rev-parse", "HEAD")

    def invoke(self, operation, **env):
        return subprocess.run(["node", str(ASSETS / "image.mjs"), operation], cwd=self.repo,
                              env={**self.env, **env}, capture_output=True, text=True)

    def outputs(self):
        return dict(line.split("=", 1) for line in (self.root / "outputs").read_text().splitlines())

    def stub(self, name, body):
        file = self.bin / name
        file.write_text("#!/usr/bin/env python3\n" + body)
        file.chmod(0o755)
        return str(file)

    def test_cold_shape_and_release_pin_without_loading_mise(self):
        result = self.invoke("inspect")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs(), {"shape": "v2", "release": "latest"})
        self.assertFalse((self.repo / ".standards").exists())
        with (self.repo / "mise.toml").open("a") as config:
            config.write('[env]\nSTANDARDS_VERSION = "2.0.0"\n')
        self.assertEqual(self.invoke("inspect").returncode, 0)
        self.assertEqual(self.outputs()["release"], "2.0.0")
        with (self.repo / "mise.toml").open("a") as config:
            config.write('STANDARDS_TEST_GATE = "off"\n')
        self.assertEqual(self.invoke("inspect").returncode, 0)

    def test_mixed_partial_or_unsafe_shapes_fail_closed(self):
        for name in [".copier-answers.yml", ".config/mise-bootstrap.toml", ".mise.toml", "mise.repository-image.toml"]:
            file = self.repo / name
            file.write_text("")
            self.assertNotEqual(self.invoke("inspect").returncode, 0, name)
            file.unlink()
        link = self.repo / LINK
        for target in ["/outside/standards.toml", "../../../.standards-config/config/mise/base.toml", "other.toml"]:
            link.unlink()
            link.symlink_to(target)
            self.assertNotEqual(self.invoke("inspect").returncode, 0, target)
        link.unlink()
        link.write_text("[tools]\n")
        self.assertNotEqual(self.invoke("inspect").returncode, 0)
        link.unlink()
        self.assertNotEqual(self.invoke("inspect").returncode, 0)

    def test_prepare_preserves_wiring_and_uses_release_identity_not_git_pin(self):
        sha = self.commit()
        cli = self.stub("devcontainer", 'print(\'{"configuration":{"features":{}}}\')\n')
        result = self.invoke("prepare", GITHUB_SHA=sha, CONFIG=".github/.devcontainer/devcontainer.json",
                             DEVCONTAINER=cli, BASE="base", STANDARDS_SHAPE="v2", STANDARDS_PIN="bad",
                             STANDARDS_VERSION="2.0.0", STANDARDS_INTEGRITY=self.integrity)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = Path(self.outputs()["context"])
        for tree in ["src", "pins"]:
            self.assertEqual(os.readlink(context / tree / LINK), TARGET)
            self.assertFalse((context / tree / ".standards-config").exists())
        self.assertFalse((context / "expected/pin").exists())
        self.assertEqual(json.loads((context / "expected/package.json").read_text()),
                         {"version": "2.0.0", "integrity": self.integrity})
        self.assertEqual(self.outputs()["shape"], "v2")
        self.assertIn('STANDARDS_VERSION = "file:/run/standards.tgz"', (context / "expected/profile.toml").read_text())
        self.assertEqual((context / "pins/mise.repository-image.toml").read_bytes(), (context / "expected/profile.toml").read_bytes())

    def test_tracked_package_payload_and_changed_shape_are_rejected(self):
        (self.repo / ".standards").mkdir()
        (self.repo / ".standards/payload").write_text("private")
        sha = self.commit()
        result = self.invoke("prepare", GITHUB_SHA=sha, CONFIG=".github/.devcontainer/devcontainer.json")
        self.assertIn("Tracked Git or standards", result.stderr)
        self.assertFalse(list(self.root.glob("repository-image-*")))
        result = self.invoke("prepare", GITHUB_SHA=sha, CONFIG=".github/.devcontainer/devcontainer.json", STANDARDS_SHAPE="legacy")
        self.assertIn("shape changed", result.stderr)

    def test_registry_operations_verify_identity_and_never_expand_auth_in_argv(self):
        context = self.root / "context"
        (context / "expected").mkdir(parents=True)
        (context / "expected/package.json").write_text(json.dumps({"version": "2.0.0", "integrity": self.integrity}))
        self.stub("npm", '''import json,os,sys,shutil
from pathlib import Path
Path(os.environ['CALLS']).write_text(json.dumps(sys.argv[1:]))
if os.getenv('FAIL_NPM'):
 print(os.environ['NODE_AUTH_TOKEN'], file=sys.stderr)
 sys.exit(1)
if sys.argv[1] == 'view':
 print(json.dumps({'version': '2.0.0', 'dist.integrity': os.environ['INTEGRITY']}))
else:
 directory = Path(sys.argv[sys.argv.index('--pack-destination') + 1])
 shutil.copyfile(os.environ['PACKAGE'], directory / 'package.tgz')
 if os.getenv('TAMPER'): (directory / 'package.tgz').write_text('tampered')
 print(json.dumps([{'name':'@rubio-enterprises/standards','version':'2.0.0',
   'integrity':os.environ['INTEGRITY'],'filename':'package.tgz'}]))
''')
        env = {"CALLS": str(self.root / "calls"), "INTEGRITY": self.integrity, "PACKAGE": str(self.package),
               "NODE_AUTH_TOKEN": "credential-never-print", "STANDARDS_RELEASE": "latest", "CONTEXT": str(context)}
        for operation in ["release", "pack"]:
            result = self.invoke(operation, **env)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (self.root / "calls").read_text()
            self.assertNotIn(env["NODE_AUTH_TOKEN"], calls + result.stdout + result.stderr)
            self.assertIn("${NODE_AUTH_TOKEN}", calls)
        self.assertEqual((context / "package/standards.tgz").read_bytes(), self.package.read_bytes())
        (context / "package/standards.tgz").unlink()
        (context / "package").rmdir()
        self.assertIn("integrity mismatch", self.invoke("pack", **env, TAMPER="1").stderr)
        result = self.invoke("release", **env, FAIL_NPM="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(env["NODE_AUTH_TOKEN"], result.stdout + result.stderr)

    def archive(self, files, config=None, links=None):
        return legacy.Fixtures.make_archive(self, files, config, links)

    def test_all_layer_audit_catches_prefix_copies_cache_archive_and_temporary_git(self):
        for name, data in {"workspaces/repo/.standards/payload": b"small",
                           "home/vscode/.cache/npm/_cacache/blob": self.package.read_bytes(),
                           "elsewhere/copied-config": self.private,
                           "workspaces/repo/.git/config": b"temporary",
                           "workspaces/repo/mise.repository-image.toml": b""}.items():
            with self.subTest(path=name), self.assertRaises(ValueError):
                legacy.audit_module.audit(self.archive({name: data}), self.package, "")
        for name in ["NODE_AUTH_TOKEN", "STANDARDS_VERSION", "STANDARDS_PIN"]:
            with self.subTest(env=name), self.assertRaises(ValueError):
                legacy.audit_module.audit(self.archive({}, {"config": {"Env": [name + "=fixture"]}}), self.package, "")

    def test_audit_allows_only_the_exact_runtime_wiring_symlink(self):
        name = "workspaces/repo/" + LINK
        legacy.audit_module.audit(self.archive({}, links={name: (TARGET, tarfile.SYMTYPE)}), self.package, "")
        for location, target, kind in [("other/" + LINK, TARGET, tarfile.SYMTYPE),
                                       (name, TARGET, tarfile.LNKTYPE), (name, TARGET + ".other", tarfile.SYMTYPE)]:
            with self.assertRaises(ValueError):
                legacy.audit_module.audit(self.archive({}, links={location: (target, kind)}), self.package, "")

    @unittest.skipUnless(shutil.which("mise"), "native mise unavailable")
    def test_transient_native_profile_overrides_the_repository_release_pin(self):
        with (self.repo / "mise.toml").open("a") as config:
            config.write('[env]\nSTANDARDS_VERSION = "2.0.0"\n[tasks.probe]\nrun = "printenv STANDARDS_VERSION"\n')
        (self.repo / "mise.repository-image.toml").write_text('[env]\nSTANDARDS_VERSION = "file:/run/standards.tgz"\n')
        result = subprocess.run(["mise", "run", "probe"], cwd=self.repo,
                                env={**self.env, "MISE_ENV": "repository-image", "MISE_TRUSTED_CONFIG_PATHS": str(self.repo)},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "file:/run/standards.tgz")

    def test_native_build_mounts_private_state_without_baking_or_build_auth(self):
        dockerfile = (ASSETS / "Dockerfile.v2").read_text()
        self.assertEqual(dockerfile.count("type=secret,id=standards_package"), 2)
        self.assertEqual(dockerfile.count("type=tmpfs,target=/run/npm-cache"), 2)
        self.assertIn("type=tmpfs,target=/workspaces/${REPO}/.git", dockerfile)
        self.assertIn("mise run setup", dockerfile)
        self.assertNotIn("standards-bootstrap", dockerfile)
        self.assertNotIn("STANDARDS_PIN", dockerfile)
        self.assertNotIn("ARG NODE_AUTH_TOKEN", dockerfile)
        self.assertNotIn("ENV NODE_AUTH_TOKEN", dockerfile)
        self.assertEqual(dockerfile.count("cat /run/expected/package.json"), 2)
        self.assertEqual(dockerfile.count("source=profile.toml"), 1)
        self.assertEqual(dockerfile.count("MISE_ENV=repository-image"), 2)
        workflow = legacy.TEXT
        self.assertIn("if: needs.prepare.outputs.shape != 'v2'", workflow)
        self.assertIn("STANDARDS_INTEGRITY: ${{ needs.prepare.outputs.integrity }}", workflow)
        build_step = workflow.split("- name: Build, rebase and audit both architecture artifacts", 1)[1].split("- name: Scan both", 1)[0]
        self.assertNotIn("NODE_AUTH_TOKEN", build_step)
        self.assertIn("npm_config_globalconfig: ${{ runner.temp }}/standards-registry-global.npmrc", workflow)
