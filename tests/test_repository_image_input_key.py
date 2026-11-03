from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "actions/repository-image"
TEXT = (ROOT / ".github/workflows/repository-image.yml").read_text()
KEY = (ASSETS / "key.mjs").as_uri()
IMAGE = (ASSETS / "image.mjs").as_uri()
spec = importlib.util.spec_from_file_location("image_recipe", ASSETS / "recipe.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)

KEY_LABEL = "org.rubio-enterprises.image.input-key"
BUILT_LABEL = "org.rubio-enterprises.image.built-at"
BASE = "ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64
BASE_CI = "ghcr.io/rubio-enterprises/devcontainers/base-ci@sha256:" + "b" * 64
# The starter's setup wiring, verbatim from @rubio-enterprises/standards starter/mise.toml.
STARTER = '''min_version = "2026.10.19"

[tools]
node = "24.21.0"

[tasks.setup]
description = "Install the standards package, its tools and the git hooks, then every setup:* task"
depends = ["standards:install"]
run = ['test -z "$(mise ls --missing)" || mise install', "mise exec -- lefthook install"]
depends_post = [{ task = "setup:*", optional = true }]

[tasks."standards:install"]
description = "Install the latest Standards Release (or the STANDARDS_VERSION Release Pin) into .standards/"
run = \'\'\'npm install --prefix .standards --no-save --prefer-online --fetch-retries=0 --no-audit --no-fund --ignore-scripts --loglevel=error "--@rubio-enterprises:registry=${STANDARDS_REGISTRY:-https://npm.pkg.github.com}" '--//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}' "@rubio-enterprises/standards@${STANDARDS_VERSION:-latest}"\'\'\'

[hooks]
enter = "true"
'''
NPM_SETUP = '\n[tasks."setup:deps"]\ndescription = "deps"\nrun = "npm ci --no-audit --no-fund"\n'
PACKAGE_INPUTS = {"relevant": {"package/mise/standards.toml": "1"}, "all": {"package/mise/standards.toml": "1", "package/config/x": "2"},
                  "tasks": ["check", "fmt"], "fallback": False}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


class Repository:
    def __init__(self, root: Path, files: dict[str, str]):
        self.path = root
        git(root.parent, "init", "-q", "-b", "main", str(root))
        self.write(files)

    def write(self, files: dict[str, str | None]):
        for name, content in files.items():
            target = self.path / name
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if name == ".config/mise/conf.d/standards.toml":
                target.unlink(missing_ok=True)
                target.symlink_to(content)
            else:
                target.write_text(content)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "--allow-empty", "-m", "change")
        return git(self.path, "rev-parse", "HEAD")


def lock(name="repo", version="1.0.0", scripted=()):
    packages = {"": {"name": name, "version": version, "devDependencies": {"left-pad": "1.3.0"}},
                "node_modules/left-pad": {"version": "1.3.0"}}
    for item in scripted:
        packages[f"node_modules/{item}"] = {"version": "1.0.0", "hasInstallScript": True}
    return json.dumps({"name": name, "version": version, "lockfileVersion": 3, "packages": packages}, indent=2)


def v2_files(setup=NPM_SETUP, manifest=None):
    return {
        "mise.toml": STARTER + setup,
        ".config/mise/conf.d/standards.toml": "../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml",
        ".github/.devcontainer/devcontainer.json": '{"name": "repo"}\n',
        "package.json": json.dumps(manifest or {"name": "repo", "version": "1.0.0", "devDependencies": {"left-pad": "1.3.0"}}, indent=2),
        "package-lock.json": lock(),
        "src/index.ts": "export const a = 1;\n",
        "README.md": "# repo\n",
    }


class KeyHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def node(self, source):
        result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def key(self, repo, sha=None, package=None, bases=None, config=".github/.devcontainer/devcontainer.json"):
        arguments = {"repo": str(repo.path), "sha": sha or git(repo.path, "rev-parse", "HEAD"),
                     "package": package or PACKAGE_INPUTS, "bases": bases or {"base": BASE, "baseCi": BASE_CI},
                     "config": config}
        return self.node(f"""
        import {{ execFileSync }} from 'node:child_process';
        import {{ inputKey, trackedFiles, blobReader }} from {json.dumps(KEY)};
        const a = {json.dumps(arguments)};
        const git = (args, input) => execFileSync('git', ['-C', a.repo, ...args], {{ input }});
        const result = inputKey({{ files: trackedFiles(git, a.sha), read: blobReader(git), packageInputs: a.package,
          config: a.config, bases: a.bases, repository: 'Rubio-Enterprises/repo',
          images: {{ image: 'ghcr.io/rubio-enterprises/repo/devcontainer', ciImage: 'ghcr.io/rubio-enterprises/repo/devcontainer-ci' }},
          cli: '0.89.0', sha: a.sha }});
        console.log(JSON.stringify(result));
        """)

    def repo(self, name="repo", **kwargs):
        return Repository(self.root / name, v2_files(**kwargs))


class InputKey(KeyHarness):
    def test_setup_input_rule_skips_source_docs_and_release_version_changes(self):
        repo = self.repo()
        first = self.key(repo)
        self.assertEqual((first["rule"], first["reasons"]), ("inputs", []))
        self.assertRegex(first["key"], r"^sha256:[a-f0-9]{64}$")
        repo.write({"src/index.ts": "export const a = 2;\n", "README.md": "# changed\n", "docs/new.md": "x\n",
                    ".devcontainer/local/devcontainer.json": "{}\n", "CHANGELOG.md": "## 1.1.0\n",
                    ".release-please-manifest.json": '{".": "1.1.0"}\n'})
        self.assertEqual(self.key(repo)["key"], first["key"])
        manifest = json.loads((repo.path / "package.json").read_text())
        manifest["version"] = "1.1.0"
        repo.write({"package.json": json.dumps(manifest, indent=2), "package-lock.json": lock(version="1.1.0")})
        self.assertEqual(self.key(repo)["key"], first["key"], "release-please version-only commit")

    def test_every_setup_input_changes_the_key(self):
        repo = self.repo()
        first = self.key(repo)["key"]
        changes = {
            "lockfile": {"package-lock.json": lock().replace('"1.3.0"', '"1.3.1"')},
            "manifest dependency": {"package.json": json.dumps({"name": "repo", "version": "1.0.0", "devDependencies": {"left-pad": "1.3.1"}})},
            "manifest name": {"package.json": json.dumps({"name": "other", "version": "1.0.0", "devDependencies": {"left-pad": "1.3.0"}})},
            "tool pin": {"mise.toml": STARTER.replace("24.21.0", "24.22.0") + NPM_SETUP},
            "mise lock": {"mise.lock": "[tools]\n"},
            ".config": {".config/nextest.toml": "[profile.ci]\n"},
            "nested lockfile": {"app/package-lock.json": lock("app")},
            "features": {".github/.devcontainer/devcontainer.json": '{"name": "repo", "features": {}}\n'},
            "features lock": {".github/.devcontainer/devcontainer-lock.json": "{}\n"},
            "npmrc": {".npmrc": "fund=false\n"},
            "patch": {"patches/left-pad.patch": "diff\n"},
            "tool version file": {".python-version": "3.13\n"},
        }
        for label, files in changes.items():
            with self.subTest(label):
                before = git(repo.path, "rev-parse", "HEAD")
                repo.write(files)
                self.assertNotEqual(self.key(repo)["key"], first, label)
                git(repo.path, "reset", "-q", "--hard", before)
        self.assertEqual(self.key(repo)["key"], first)

    def test_bases_package_content_and_config_path_change_the_key_but_package_version_does_not(self):
        repo = self.repo()
        first = self.key(repo)["key"]
        self.assertNotEqual(self.key(repo, bases={"base": BASE, "baseCi": BASE_CI.replace("b" * 64, "c" * 64)})["key"], first)
        self.assertNotEqual(self.key(repo, bases={"base": BASE.replace("a" * 64, "d" * 64), "baseCi": BASE_CI})["key"], first)
        changed = dict(PACKAGE_INPUTS, relevant={"package/mise/standards.toml": "changed"})
        self.assertNotEqual(self.key(repo, package=changed)["key"], first)
        unrelated = dict(PACKAGE_INPUTS, all={**PACKAGE_INPUTS["all"], "package/config/x": "changed"})
        self.assertEqual(self.key(repo, package=unrelated)["key"], first, "inputs rule ignores checks-only package files")
        self.assertNotEqual(self.key(repo, config="other/devcontainer.json")["key"], first)

    def test_whole_tree_rule_for_setup_that_can_read_any_file(self):
        cases = {
            "unknown command": {"setup": '\n[tasks."setup:gen"]\nrun = "node scripts/gen.mjs"\n'},
            "shell command": {"setup": '\n[tasks."setup:deps"]\nrun = "npm ci; ./x"\n'},
            "env key": {"setup": '\n[tasks."setup:deps"]\nrun = "npm ci"\nenv = { A = "1" }\n'},
            "lifecycle script": {"manifest": {"name": "repo", "version": "1", "scripts": {"prepare": "tsc"}}},
            "local dependency": {"manifest": {"name": "repo", "version": "1", "dependencies": {"x": "file:vendor/x.tgz"}}},
            "go": {"setup": '\n[tasks."setup:go"]\nrun = "go list -deps -test ./... > /dev/null"\n'},
            "bundler": {"setup": '\n[tasks."setup:deps"]\nrun = "bundle install"\n'},
            "starter setup changed": {"setup": "\n[tasks.\"setup:x\"]\nrun = \"cargo fetch --locked\"\n[tasks.\"standards:install\"]\nrun = \"npm i\"\n"},
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                repo = self.repo(label.replace(" ", "-"), **kwargs)
                first = self.key(repo)
                self.assertEqual(first["rule"], "tree", label)
                self.assertTrue(first["reasons"])
                repo.write({"src/index.ts": "export const a = 3;\n"})
                self.assertNotEqual(self.key(repo)["key"], first["key"], label)

    def test_package_install_scripts_and_installable_python_projects(self):
        ignored = self.repo("ignored", setup='\n[tasks."setup:deps"]\nrun = "npm ci --ignore-scripts --no-audit --no-fund"\n',
                            manifest={"name": "repo", "version": "1", "scripts": {"postinstall": "node x"}})
        self.assertEqual(self.key(ignored)["rule"], "inputs")
        allowed = self.repo("allowed")
        allowed.write({"package-lock.json": lock(scripted=["esbuild", "@scope/x/node_modules/sharp"])})
        self.assertEqual(self.key(allowed)["reasons"], [])
        unknown = self.repo("unknown")
        unknown.write({"package-lock.json": lock(scripted=["@prisma/client"])})
        self.assertEqual(self.key(unknown)["reasons"], ["install script of @prisma/client"])
        bun = self.repo("bun", setup='\n[tasks."setup:deps"]\nrun = "bun install --frozen-lockfile"\n')
        bun.write({"bun.lock": '{"lockfileVersion": 1, "workspaces": {}, "packages": {\n'
                               '"esbuild": ["esbuild@0.28.2", "", {}, "sha512-x"],\n"core-js": ["core-js@3.0.0", "", {}, "sha512-y"],\n}}\n'})
        self.assertEqual(self.key(bun)["reasons"], [], "core-js is not bun-trusted; esbuild is reviewed")
        bun.write({"bun.lock": '{"packages": {"simple-git-hooks": ["simple-git-hooks@2.0.0", "", {}, "sha512-x"],}}\n'})
        self.assertIn("install script of simple-git-hooks", self.key(bun)["reasons"])
        uv = self.repo("uv", setup='\n[tasks."setup:py"]\nrun = "uv sync --frozen"\n')
        uv.write({"pyproject.toml": '[project]\nname = "x"\nversion = "1"\n'})
        self.assertEqual(self.key(uv)["rule"], "inputs", "virtual project")
        uv.write({"pyproject.toml": '[project]\nname = "x"\nversion = "1"\n[build-system]\nrequires = ["hatchling"]\n'})
        self.assertEqual(self.key(uv)["reasons"], ["installable project pyproject.toml"])
        req = self.repo("req", setup='\n[tasks."setup:py"]\nrun = "uv venv --clear .venv && uv pip install --python .venv -r requirements-dev.txt"\n')
        req.write({"requirements-dev.txt": "pytest==8.0 \\\n  --hash=sha256:00\n-r requirements.txt\n", "requirements.txt": "httpx>=0.27  # client\n"})
        self.assertEqual(self.key(req)["rule"], "inputs")
        req.write({"requirements.txt": "-e .\n"})
        self.assertEqual(self.key(req)["rule"], "tree")

    def test_pnpm_build_policy_is_read_conservatively(self):
        source = f"""
        import {{ pnpmBuildPolicy }} from {json.dumps(KEY)};
        const cases = {json.dumps([
            "packages:\n  - apps/*\nallowBuilds:\n  \"@swc/core\": true\n  esbuild: true\n  core-js: false # notice\n",
            "onlyBuiltDependencies:\n  - esbuild\n  - 'sharp'\n",
            "allowBuilds: { esbuild: true }\n",
            "dangerouslyAllowAllBuilds: true\n",
            "allowBuilds:\n  - esbuild\n",
        ])};
        console.log(JSON.stringify(cases.map(text => {{ const r = pnpmBuildPolicy(text); return r && [...r]; }})));
        """
        self.assertEqual(self.node(source), [["@swc/core", "esbuild"], ["esbuild", "sharp"], None, None, None])

    def test_mise_configuration_that_reaches_beyond_pins_selects_the_tree(self):
        for label, files in {
            "file task": {"mise-tasks/setup/gen": "#!/bin/sh\n"},
            "env source": {".mise.local.toml": '[env]\n_.source = "scripts/env.sh"\n'},
            "install hook": {"mise.local.toml": '[hooks]\npostinstall = "./x"\n'},
            "template exec": {".config/mise/conf.d/extra.toml": '[env]\nA = "{{ exec(command=\'cat x\') }}"\n'},
            "local tool": {".config/mise/config.toml": '[tools]\n"cargo:x" = "path:./crates/x"\n'},
            "includes": {".config/mise/config.toml": '[task_config]\nincludes = ["tasks.toml"]\n'},
        }.items():
            with self.subTest(label):
                repo = self.repo(label.replace(" ", "-"))
                repo.write(files)
                self.assertEqual(self.key(repo)["rule"], "tree", label)
        repo = self.repo("path-only")
        repo.write({".config/mise/config.toml": '[env]\n_.path = ["bin"]\n'})
        self.assertEqual(self.key(repo)["rule"], "inputs")
        self.assertEqual(self.key(repo, package=dict(PACKAGE_INPUTS, tasks=["setup:extra"]))["rule"], "tree")

    def test_forced_tree_reasons_select_the_whole_tree(self):
        repo = self.repo()
        inputs = self.key(repo)
        forced = self.node(f"""
        import {{ execFileSync }} from 'node:child_process';
        import {{ inputKey, trackedFiles, blobReader }} from {json.dumps(KEY)};
        const git = (args, input) => execFileSync('git', ['-C', {json.dumps(str(repo.path))}, ...args], {{ input }});
        const sha = git(['rev-parse', 'HEAD']).toString().trim();
        console.log(JSON.stringify(inputKey({{ files: trackedFiles(git, sha), read: blobReader(git),
          packageInputs: {json.dumps(PACKAGE_INPUTS)}, config: '.github/.devcontainer/devcontainer.json',
          bases: {{}}, repository: 'r', images: {{}}, cli: '0.89.0', sha, treeReasons: ['self-hosted standards source'] }})));
        """)
        self.assertEqual((inputs["rule"], forced["rule"]), ("inputs", "tree"))
        self.assertEqual(forced["reasons"], ["self-hosted standards source"])

    def test_export_subst_ties_the_key_to_the_commit(self):
        repo = self.repo()
        repo.write({".gitattributes": "version.txt export-subst\n", "version.txt": "$Format:%H$\n"})
        first = self.key(repo)
        repo.write({"src/index.ts": "export const a = 4;\n"})
        self.assertNotEqual(self.key(repo)["key"], first["key"])

    def test_normalization_removes_only_release_versions(self):
        source = f"""
        import {{ normalizeManifest }} from {json.dumps(KEY)};
        const n = (name, value) => normalizeManifest(name, Buffer.from(JSON.stringify(value)));
        console.log(JSON.stringify([
          n('package.json', {{ name: 'a', version: '1' }}) === n('package.json', {{ name: 'a', version: '2' }}),
          n('package.json', {{ name: 'a', version: '1' }}) === n('package.json', {{ name: 'b', version: '1' }}),
          n('package-lock.json', {{ version: '1', packages: {{ '': {{ version: '1' }}, 'node_modules/x': {{ version: '1' }} }} }}) ===
            n('package-lock.json', {{ version: '2', packages: {{ '': {{ version: '2' }}, 'node_modules/x': {{ version: '1' }} }} }}),
          n('package-lock.json', {{ packages: {{ 'node_modules/x': {{ version: '1' }} }} }}) ===
            n('package-lock.json', {{ packages: {{ 'node_modules/x': {{ version: '2' }} }} }}),
          normalizeManifest('package.json', Buffer.from('not json')) === undefined,
          normalizeManifest('Cargo.toml', Buffer.from('{{}}')) === undefined,
        ]));
        """
        self.assertEqual(self.node(source), [True, False, True, False, True, True])

    def test_nested_package_json_versions_are_not_normalized(self):
        repo = self.repo()
        repo.write({"app/package.json": json.dumps({"name": "app", "version": "1.0.0"})})
        first = self.key(repo)["key"]
        repo.write({"app/package.json": json.dumps({"name": "app", "version": "1.0.1"})})
        self.assertNotEqual(self.key(repo)["key"], first)


class PackageInputs(unittest.TestCase):
    def archive(self, root: Path, members: dict[str, bytes]):
        path = root / "standards.tgz"
        with tarfile.open(path, "w:gz") as package:
            for name, data in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                package.addfile(info, io.BytesIO(data))
        return path

    def members(self, version="2.1.0", config=b"a", toml=b'[tools]\njq = "1"\n[tasks.check]\nrun = "x"\n', recipe=b"FROM x\n"):
        return {"package/package.json": json.dumps({"name": "@rubio-enterprises/standards", "version": version}).encode(),
                "package/image/Dockerfile": recipe, "package/mise/standards.toml": toml, "package/config/x.yml": config}

    def test_relevant_content_excludes_version_and_checks_only_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = recipe.image_inputs(self.archive(root, self.members()), "2.1.0")
            self.assertEqual(sorted(first["relevant"]), ["package/image/Dockerfile", "package/mise/standards.toml", "package/package.json"])
            self.assertEqual((first["tasks"], first["fallback"]), (["check"], False))
            bumped = recipe.image_inputs(self.archive(root, self.members(version="2.2.0", config=b"b")), "2.2.0")
            self.assertEqual(bumped["relevant"], first["relevant"])
            self.assertNotEqual(bumped["all"], first["all"])
            for change in [{"recipe": b"FROM y\n"}, {"toml": b'[tools]\njq = "2"\n'}]:
                other = recipe.image_inputs(self.archive(root, self.members(**change)), "2.1.0")
                self.assertNotEqual(other["relevant"], first["relevant"], change)
            for toml in [b'[env]\n_.source = "x"\n', b'[task_config]\nincludes = ["x"]\n', b'[hooks]\npostinstall = "x"\n']:
                self.assertTrue(recipe.image_inputs(self.archive(root, self.members(toml=toml)), "2.1.0")["fallback"], toml)
            members = self.members()
            del members["package/image/Dockerfile"]
            with self.assertRaisesRegex(ValueError, "2.1.0 or newer"):
                recipe.image_inputs(self.archive(root, members), "2.0.0")


def blob(value):
    data = json.dumps(value).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest(), data


class Publication(KeyHarness):
    def registry(self, key="sha256:" + "e" * 64, built=None, bases=(BASE, BASE_CI), index_extra=None, labels=True):
        content = {}
        for kind, base in zip(["devcontainer", "devcontainer-ci"], bases):
            cfg_labels = {KEY_LABEL: key, BUILT_LABEL: built or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())} if labels else {}
            config_digest, config_data = blob({"config": {"Labels": cfg_labels}})
            child_digest, child_data = blob({"config": {"digest": config_digest},
                                             "annotations": {"org.opencontainers.image.base.digest": base.split("@")[1]}})
            index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                     "manifests": [{"digest": child_digest, "platform": {"os": "linux", "architecture": "amd64"}}] + (index_extra or [])}
            index_digest, index_data = blob(index)
            prefix = f"https://ghcr.io/v2/rubio-enterprises/repo/{kind}"
            content[f"{prefix}/manifests/main"] = index_data
            content[f"{prefix}/manifests/{child_digest}"] = child_data
            content[f"{prefix}/blobs/{config_digest}"] = config_data
            content[kind] = json.dumps({"index": index_digest, "child": child_digest}).encode()
        return {k: base64.b64encode(v).decode() for k, v in content.items()}

    def published(self, responses, key="sha256:" + "e" * 64):
        return self.node(f"""
        const responses = {json.dumps(responses)};
        globalThis.fetch = async (url, options = {{}}) => {{
          if (url.startsWith('https://ghcr.io/token?')) {{
            const auth = options.headers?.Authorization;
            return auth === 'Basic ' + Buffer.from('actor:token').toString('base64')
              ? new Response(JSON.stringify({{ token: 't' }})) : new Response('', {{ status: 401 }});
          }}
          const body = responses[url];
          return body ? new Response(Buffer.from(body, 'base64')) : new Response('', {{ status: 404 }});
        }};
        const {{ publishedPair }} = await import({json.dumps(IMAGE)});
        console.log(JSON.stringify(await publishedPair({{ image: 'ghcr.io/rubio-enterprises/repo/devcontainer',
          ciImage: 'ghcr.io/rubio-enterprises/repo/devcontainer-ci', key: {json.dumps(key)}, base: {json.dumps(BASE)},
          baseCi: {json.dumps(BASE_CI)}, credential: 'actor:token' }})));
        """)

    def test_matching_pair_is_aliased_with_both_digests(self):
        responses = self.registry()
        result = self.published(responses)
        for kind, name in [("dev", "devcontainer"), ("ci", "devcontainer-ci")]:
            self.assertEqual(result["pair"][kind], json.loads(base64.b64decode(responses[name])))

    def test_any_doubt_about_the_published_pair_rebuilds(self):
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 8 * 86400))
        cases = {
            "different key": (self.registry(), "sha256:" + "f" * 64, "different inputs"),
            "different base-ci": (self.registry(bases=(BASE, BASE_CI.replace("b" * 64, "c" * 64))), None, "different base"),
            "older than seven days": (self.registry(built=old), None, "older than 7 days"),
            "unlabelled": (self.registry(labels=False), None, "different inputs"),
            "missing": ({}, None, "unreadable"),
            "two platforms": (self.registry(index_extra=[{"digest": "sha256:" + "0" * 64, "platform": {"os": "linux", "architecture": "arm64"}}]),
                              None, "unreadable"),
        }
        for label, (responses, key, reason) in cases.items():
            with self.subTest(label):
                result = self.published(responses, key or "sha256:" + "e" * 64)
                self.assertNotIn("pair", result)
                self.assertIn(reason, result["reason"])


class Alias(unittest.TestCase):
    def run_alias(self, root: Path, digests: dict[str, str], served: dict[str, str] | None = None):
        crane = root / "crane-stub"
        log = root / "calls"
        state = root / "tags.json"
        state.write_text(json.dumps(served or {}))
        index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                 "manifests": [{"digest": "sha256:" + "1" * 64, "platform": {"os": "linux", "architecture": "amd64"}}]}
        crane.write_text(f"""#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
open({json.dumps(str(log))}, "a").write(" ".join(args) + "\\n")
tags = json.load(open({json.dumps(str(state))}))
if args[0] == "auth":
    assert sys.stdin.read() == "token"
elif args[0] == "tag":
    tags[args[1].split("@")[0] + ":" + args[2]] = args[1].split("@")[1]
    json.dump(tags, open({json.dumps(str(state))}, "w"))
elif args[0] == "digest":
    print(tags[args[1]])
elif args[0] == "manifest":
    print(json.dumps({json.dumps(index)}))
""")
        crane.chmod(0o755)
        env = {"GITHUB_SHA": "c" * 40, "RUNNER_TEMP": str(root), "GITHUB_ACTOR": "actor", "REGISTRY_TOKEN": "token",
               "IMAGE": "ghcr.io/rubio-enterprises/repo/devcontainer", **digests}
        source = f"""
        const {{ alias }} = await import({json.dumps(IMAGE)});
        await alias({json.dumps(env)}, async () => {json.dumps(str(crane))});
        """
        result = subprocess.run(["node", "--input-type=module", "-e", source], capture_output=True, text=True)
        return result, log.read_text().splitlines() if log.exists() else []

    def test_alias_tags_commit_by_digest_and_verifies(self):
        child = "sha256:" + "1" * 64
        digests = {"DEV_INDEX": "sha256:" + "2" * 64, "DEV_CHILD": child, "CI_INDEX": "sha256:" + "3" * 64, "CI_CHILD": child}
        with tempfile.TemporaryDirectory() as tmp:
            result, calls = self.run_alias(Path(tmp), digests)
            self.assertEqual(result.returncode, 0, result.stderr)
            sha = "c" * 40
            for name, index in [("devcontainer", digests["DEV_INDEX"]), ("devcontainer-ci", digests["CI_INDEX"])]:
                image = f"ghcr.io/rubio-enterprises/repo/{name}"
                self.assertIn(f"tag {image}@{index} sha-{sha}", calls)
                self.assertIn(f"tag {image}@{child} sha-{sha}-amd64", calls)
            self.assertFalse(any(":main" in call for call in calls), "aliasing never moves :main")
            self.assertEqual(calls[0], "auth login ghcr.io --username actor --password-stdin")
            self.assertEqual(list(Path(tmp).glob("repository-image-alias-*")), [])
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.run_alias(Path(tmp), {**digests, "DEV_CHILD": "sha256:" + "9" * 64})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("child changed", result.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.run_alias(Path(tmp), {**digests, "CI_INDEX": "main"})
            self.assertNotEqual(result.returncode, 0)


class WorkflowContracts(unittest.TestCase):
    def job(self, name):
        return TEXT.split(f"\n  {name}:\n", 1)[1].split("\n\n  ", 1)[0]

    def test_build_runs_only_when_the_published_pair_cannot_stand_in(self):
        build = self.job("build")
        self.assertIn("if: needs.prepare.outputs.skip != 'true'", build)
        self.assertNotIn("inputs.build-service", TEXT)
        self.assertIn("INPUT_KEY: ${{ needs.prepare.outputs.input-key }}", build)

    def test_prepare_compares_inputs_without_docker_or_write_access(self):
        prepare = self.job("prepare")
        self.assertIn("contents: read\n      packages: read", prepare)
        self.assertNotIn("packages: write", prepare)
        step = prepare.split("- name: Compare image inputs with the published pair", 1)[1].split("- name:", 1)[0]
        self.assertNotIn("if:", step)
        self.assertIn("FORCE: ${{ github.event_name != 'push' }}", step)
        self.assertIn("operation: key", step)
        self.assertIn("base: ${{ steps.bases.outputs.base }}", step)
        cli = re.search(r"DEVCONTAINER_CLI_VERSION: (\S+)", step).group(1)
        self.assertIn(f"@devcontainers/cli@{cli}", TEXT)
        self.assertLess(prepare.index("operation: key"), prepare.index("- name: Remove registry cache and logs"))

    def test_alias_job_runs_only_on_a_match_on_the_small_route(self):
        alias = self.job("alias")
        self.assertIn("if: needs.prepare.outputs.skip == 'true'", alias)
        self.assertIn(".small || fromJSON('[\"ubuntu-slim\"]')", alias)
        self.assertIn("permissions:\n      packages: write\n", alias)
        self.assertNotIn("contents:", alias)
        self.assertNotIn("docker", alias.lower().replace("docker_config", ""))
        self.assertIn("operation: alias", alias)

    def test_pinned_crane_is_checksum_verified(self):
        image = (ASSETS / "image.mjs").read_text()
        self.assertIn("go-containerregistry_Linux_x86_64.tar.gz", image)
        self.assertRegex(image, r"sha256: '[a-f0-9]{64}'")
        self.assertIn("crane checksum mismatch", image)

    def test_bun_default_trusted_list_is_a_plain_name_list(self):
        names = (ASSETS / "bun-default-trusted.txt").read_text().splitlines()
        self.assertGreater(len(names), 300)
        self.assertIn("@prisma/client", names)
        self.assertTrue(all(re.fullmatch(r"(@[a-z0-9._-]+/)?[a-z0-9._-]+", name) for name in names))


if __name__ == "__main__":
    unittest.main()
