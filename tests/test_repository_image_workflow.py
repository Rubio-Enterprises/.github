from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "actions/repository-image"
TEXT = (ROOT / ".github/workflows/repository-image.yml").read_text()
MODULE = (ASSETS / "image.mjs").as_uri()
spec = importlib.util.spec_from_file_location("image_audit", ASSETS / "audit.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def script(name):
    lines = TEXT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line[indent + 2:] if line.strip() else "")
    return "\n".join(body) + "\n"


def config(arch="amd64"):
    return {"os": "linux", "architecture": arch, "rootfs": {"diff_ids": ["base"]},
            "config": {"Env": ["UV_LINK_MODE=copy"], "Labels": {"devcontainer.metadata": "[]"}}}


def manifest(*layers):
    return {"layers": [{"digest": name, "size": 1} for name in layers]}


class Contracts(unittest.TestCase):
    def test_permissions_events_native_routes_and_debounce(self):
        self.assertIn("permissions: {}", TEXT)
        self.assertNotIn("pull_request_target", TEXT)
        self.assertNotIn("workflow_run", TEXT)
        self.assertIn("github.event_name == 'pull_request'", TEXT)
        self.assertIn("github.event.repository.default_branch", TEXT)
        self.assertIn("cancel-in-progress: true", TEXT)
        self.assertIn("group: repository-image-${{ github.repository }}-${{ github.ref }}", TEXT)
        self.assertIn("contents: read\n      packages: read", TEXT)
        self.assertIn("contents: read\n      packages: write", TEXT)
        self.assertIn(".docker || fromJSON('[\"ubuntu-latest\"]')", TEXT)
        self.assertIn(".arm || fromJSON('[\"ubuntu-24.04-arm\"]')", TEXT)
        self.assertIn("steps: *build-steps", TEXT)
        self.assertNotIn("continue-on-error", TEXT)
        self.assertNotIn("qemu", TEXT.lower())
        prepare = TEXT.split("\n  prepare:\n", 1)[1].split("\n  validate:\n", 1)[0]
        self.assertIn(".small || fromJSON('[\"ubuntu-slim\"]')", prepare)
        self.assertNotIn("docker info", prepare.lower())

    def test_assets_are_owned_by_the_shared_action_and_refs_pinned(self):
        action = (ASSETS / "action.yml").read_text()
        self.assertIn('$GITHUB_ACTION_PATH/image.mjs', action)
        self.assertNotIn("./actions/repository-image", TEXT)
        for ref in re.findall(r"uses: (\S+)", TEXT):
            if ref.startswith("Rubio-Enterprises/.github/actions/"):
                self.assertIn(ref, ["Rubio-Enterprises/.github/actions/repository-image@main",
                                    "Rubio-Enterprises/.github/actions/standards-bootstrap@main"])
            else:
                self.assertRegex(ref, r"@[a-f0-9]{40}$")
        self.assertIn("persist-credentials: false", TEXT)

    def test_build_scan_publish_and_retention(self):
        names = ["Provision native shared config", "Require the image build pin",
                 "Prepare central generic build inputs", "Build, rebase and audit both architecture artifacts",
                 "Scan both local artifacts for secrets", "Push the scanned architecture image"]
        positions = [TEXT.index(f"- name: {name}") for name in names]
        self.assertEqual(positions, sorted(positions))
        scan = script("Scan both local artifacts for secrets")
        self.assertIn('for artifact in "$IMAGE" "$CI_IMAGE"', scan)
        self.assertIn('--image-src docker --scanners secret --image-config-scanners secret', scan)
        self.assertIn('--config \'\' --ignorefile \'\'', scan)
        self.assertIn('if [[ "$status" != 0 ]]; then exit "$status"; fi', scan)
        self.assertNotIn(".Match", scan)
        self.assertNotIn(".Content", scan)
        self.assertIn('needs: [prepare, build]', TEXT)
        self.assertIn('needs: [prepare, merge]', TEXT)
        self.assertIn('delete-only-untagged-versions: "true"', TEXT)
        self.assertIn('min-versions-to-keep: 5', TEXT)
        self.assertIn('ci-image: ${{ steps.image.outputs.image }}-ci', TEXT)
        for name in ("Resolve image and publication policy", "Scan both local artifacts for secrets",
                     "Publish multi-architecture tags and cache aliases"):
            self.assertNotIn("${{", script(name))

    def test_generic_dockerfile_never_copies_standards_or_declares_pin(self):
        dockerfile = (ASSETS / "Dockerfile").read_text()
        self.assertNotIn("ARG STANDARDS_PIN", dockerfile)
        self.assertNotIn("ENV STANDARDS_PIN", dockerfile)
        self.assertEqual(dockerfile.count("from=standards"), 2)
        self.assertIn('from=pins', dockerfile)
        self.assertIn('COPY --chown=vscode:vscode --from=src', dockerfile)
        self.assertIn("UV_LINK_MODE=copy", dockerfile)
        self.assertNotIn("COPY --from=standards", dockerfile)
        self.assertNotIn("mise bootstrap --only", dockerfile)

    def test_runner_payload_smoke_does_not_load_a_cold_consumer_config(self):
        build = (ASSETS / "build.sh").read_text()
        smoke = build.split('--entrypoint bash "$CI_IMAGE:sha-$GITHUB_SHA-$ARCH" -euc', 1)[1].split('\nfor artifact', 1)[0]
        self.assertIn('workspace="$PWD"', smoke)
        self.assertLess(smoke.index('cd /'), smoke.index('mise --version'))
        self.assertIn('/usr/bin/gh --version', smoke)
        self.assertIn('/usr/bin/jq --version', smoke)
        self.assertIn('test ! -e "$workspace/.standards/node_modules"', smoke)

    def test_scan_policy_and_checksum_pins_match_legacy_contract(self):
        from test_devcontainer_image_workflow import script as legacy_script
        scan = script("Scan both local artifacts for secrets")
        old = legacy_script("Scan the local image for secrets")
        for text in [scan, old]:
            policy = text.split("<<'TRIVY'\n", 1)[1].split("\nTRIVY", 1)[0]
            if text == scan:
                new_policy = policy
            else:
                self.assertEqual(new_policy, policy)
        self.assertEqual(script("Install checksum-pinned Trivy"), legacy_script("Install checksum-pinned Trivy"))


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, "RUNNER_TEMP": str(self.root), "GITHUB_OUTPUT": str(self.root / "output"),
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        for name in ("CONFIG_DIR", "DATA_DIR", "STATE_DIR", "CACHE_DIR", "GLOBAL_CONFIG_FILE"):
            self.env[f"MISE_{name}"] = str(self.root / name)

    def node(self, function, arguments):
        source = f"import {{ {function} }} from {json.dumps(MODULE)}; {function}(...{json.dumps(arguments)});"
        return subprocess.run(["node", "--input-type=module", "-e", source],
                              env=self.env, capture_output=True, text=True)

    def test_base_reference_rejects_untrusted_registry_mutable_tags_and_injection(self):
        name = "ghcr.io/rubio-enterprises/devcontainers/base"
        for ref in [name + ":latest", name + "@sha256:" + "a" * 64]:
            self.assertEqual(self.node("baseReference", [ref, "base"]).returncode, 0)
        for ref in ["other/base:latest", name + ":v1", name + "\nbase=other", name + "@sha256:short"]:
            self.assertNotEqual(self.node("baseReference", [ref, "base"]).returncode, 0)
        for kind in ["base", "base-ci"]:
            for registry in ["ghcrXio", "ghcr-io"]:
                ref = f"{registry}/rubio-enterprises/devcontainers/{kind}@sha256:" + "a" * 64
                self.assertNotEqual(self.node("baseReference", [ref, kind]).returncode, 0)

    def test_extra_features_definition_rejects_legacy_and_local_build_inputs(self):
        self.assertEqual(self.node("validateDefinition", [{"name": "repo", "features": {
            "ghcr.io/devcontainers-extra/features/apt-packages:1.0.6": {"packages": "python3"}}}]).returncode, 0)
        for definition in [{"build": {"dockerfile": "Dockerfile"}}, {"image": "other"},
                           {"features": []}, {"features": {"./evil": {}}},
                           {"features": {"ghcr.io/rubio-enterprises/devcontainer-features/baseline:3.0.1": {}}}]:
            self.assertNotEqual(self.node("validateDefinition", [definition]).returncode, 0)

    def pair(self, arch="amd64"):
        base, ci = config(arch), config(arch)
        ci["config"]["Labels"]["org.opencontainers.image.base.digest"] = "sha256:" + "a" * 64
        ci["rootfs"]["diff_ids"].append("runner")
        return [manifest("base"), manifest("base", "runner"), base, ci, "sha256:" + "a" * 64, arch]

    def test_base_pair_validates_both_arches_label_and_filesystem_prefix(self):
        for arch in ["amd64", "arm64"]:
            self.assertEqual(self.node("validatePair", self.pair(arch)).returncode, 0)
        for field in ["architecture", "label", "compressed", "uncompressed"]:
            args = self.pair()
            if field == "architecture": args[3]["architecture"] = "arm64"
            if field == "label": args[3]["config"]["Labels"]["org.opencontainers.image.base.digest"] = "other"
            if field == "compressed": args[1]["layers"][0]["digest"] = "other"
            if field == "uncompressed": args[3]["rootfs"]["diff_ids"][0] = "other"
            self.assertNotEqual(self.node("validatePair", args).returncode, 0, field)

    def resolve(self, base=None, base_ci=None, tamper=None):
        """Run resolve against an in-memory registry; returns (process, outputs)."""
        def blob(value):
            data = json.dumps(value).encode()
            return "sha256:" + hashlib.sha256(data).hexdigest(), data

        content, indices = {}, {}
        base_digest = None
        for kind in ["base", "base-ci"]:
            entries = []
            for arch in ["amd64", "arm64"]:
                args = self.pair(arch)
                image_config = args[2] if kind == "base" else args[3]
                if kind == "base-ci":
                    image_config["config"]["Labels"]["org.opencontainers.image.base.digest"] = base_digest
                config_digest, config_data = blob(image_config)
                image = dict(args[0] if kind == "base" else args[1], config={"digest": config_digest})
                image_digest, image_data = blob(image)
                content[f"{kind}/blobs/{config_digest}"] = config_data
                content[f"{kind}/manifests/{image_digest}"] = image_data
                entries.append({"digest": image_digest, "platform": {"os": "linux", "architecture": arch}})
            index_digest, index_data = blob({"manifests": entries})
            content[f"{kind}/manifests/latest"] = content[f"{kind}/manifests/{index_digest}"] = index_data
            indices[kind] = index_digest
            if kind == "base":
                base_digest = index_digest
        if tamper:
            tamper(content, indices)
        responses = {f"https://ghcr.io/v2/rubio-enterprises/devcontainers/{key}": base64.b64encode(value).decode()
                     for key, value in content.items()}
        name = "ghcr.io/rubio-enterprises/devcontainers/"
        env = {"BASE": base or name + "base:latest", "BASE_CI": base_ci or name + "base-ci:latest"}
        source = f"""
        const responses = {json.dumps(responses)};
        globalThis.fetch = async (url, options = {{}}) => {{
          if (url.startsWith('https://ghcr.io/token?')) return new Response(JSON.stringify({{ token: 't' }}));
          if (options.headers?.Authorization !== 'Bearer t') return new Response('', {{ status: 401 }});
          const body = responses[url];
          return body ? new Response(Buffer.from(body, 'base64')) : new Response('', {{ status: 404 }});
        }};
        const {{ resolve }} = await import({json.dumps(MODULE)});
        await resolve({json.dumps(env)});
        """
        result = subprocess.run(["node", "--input-type=module", "-e", source], env=self.env, capture_output=True, text=True)
        output = self.root / "output"
        values = dict(line.split("=", 1) for line in output.read_text().splitlines()) if output.exists() else {}
        output.unlink(missing_ok=True)
        return result, values, indices

    def test_resolve_pins_both_indices_through_the_registry_api_without_docker(self):
        bin_dir = self.root / "bin"  # node alone: no docker or crane on PATH
        bin_dir.mkdir()
        (bin_dir / "node").symlink_to(shutil.which("node"))
        self.env["PATH"] = str(bin_dir)
        result, values, indices = self.resolve()
        self.assertEqual(result.returncode, 0, result.stderr)
        name = "ghcr.io/rubio-enterprises/devcontainers/"
        self.assertEqual(values, {"base": f"{name}base@{indices['base']}", "base-ci": f"{name}base-ci@{indices['base-ci']}"})
        pinned, again, _ = self.resolve(values["base"], values["base-ci"])
        self.assertEqual((pinned.returncode, again), (0, values))

    def test_resolve_rejects_tampered_content_and_mismatched_pairs(self):
        def corrupt(content, indices):
            key = next(key for key in content if key.startswith("base-ci/blobs/"))
            content[key] = content[key].replace(b"linux", b"Linux")

        def one_arch(content, indices):
            index = json.loads(content["base/manifests/latest"])
            index["manifests"] = index["manifests"][:1]
            content["base/manifests/latest"] = json.dumps(index).encode()

        name = "ghcr.io/rubio-enterprises/devcontainers/"
        for label, kwargs in [("tampered blob", {"tamper": corrupt}), ("one architecture", {"tamper": one_arch}),
                              ("unknown digest", {"base": f"{name}base@sha256:" + "0" * 64})]:
            result, values, _ = self.resolve(**kwargs)
            self.assertNotEqual(result.returncode, 0, label)
            self.assertEqual(values, {}, label)

    def rebased(self):
        dev, ci = config(), config()
        dev["rootfs"]["diff_ids"] = ["base", "feature", "repo"]
        ci["rootfs"]["diff_ids"] = ["base", "runner", "feature", "repo"]
        return [manifest("base"), manifest("base", "runner"), manifest("base", "feature", "repo"),
                manifest("base", "runner", "feature", "repo"), dev, ci, "amd64"]

    def test_rebase_preserves_exact_layers_metadata_runtime_config_and_platform(self):
        self.assertEqual(self.node("validateRebase", self.rebased()).returncode, 0)
        reordered = self.rebased()
        reordered[5]["config"] = dict(reversed(list(reordered[5]["config"].items())))
        self.assertEqual(self.node("validateRebase", reordered).returncode, 0)
        for field in ["prefix", "layer", "config", "metadata", "uv", "architecture", "diff_ids"]:
            args = self.rebased()
            if field == "prefix": args[3]["layers"][0]["digest"] = "other"
            if field == "layer": args[3]["layers"][-1]["digest"] = "other"
            if field == "config": args[5]["config"]["User"] = "root"
            if field == "metadata":
                for item in args[4:6]: item["config"]["Labels"] = {}
            if field == "uv":
                for item in args[4:6]: item["config"]["Env"] = []
            if field == "architecture": args[5]["architecture"] = "arm64"
            if field == "diff_ids": args[5]["rootfs"]["diff_ids"][-1] = "other"
            self.assertNotEqual(self.node("validateRebase", args).returncode, 0, field)

    def make_archive(self, files, image_config=None, links=None):
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode="w") as contents:
            for name, body in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(body)
                contents.addfile(member, io.BytesIO(body))
            for name, (target, kind) in (links or {}).items():
                member = tarfile.TarInfo(name)
                member.type, member.linkname = kind, target
                contents.addfile(member)
        archive = self.root / "artifact.tar"
        with tarfile.open(archive, "w") as saved:
            entries = {"manifest.json": json.dumps([{"Config": "config.json", "Layers": ["layer.tar"]}]).encode(),
                       "config.json": json.dumps(image_config or config()).encode(), "layer.tar": layer.getvalue()}
            for name, body in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(body)
                saved.addfile(member, io.BytesIO(body))
        return archive

    def test_source_provenance_uses_clean_tracked_snapshot_not_untracked_workspace(self):
        owned = "Repository-owned bridge fixture\n" * 20
        private = "Bundle-only fixture\n" * 20
        src, result = self.prepare_consumer("source-provenance", {}, {"treefmt.toml": owned}, {
            ".standards-config/config/treefmt.toml": owned,
            ".standards-config/config/private.yml": private,
            "untracked-policy.yml": private,
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        source = Path(outputs["context"]) / "src"
        self.assertEqual((source / "treefmt.toml").read_text(), owned)
        self.assertTrue((src / "untracked-policy.yml").is_file())
        self.assertFalse((source / "untracked-policy.yml").exists())
        self.assertFalse((source / ".standards-config").exists())
        standards = src / ".standards-config"
        archive = self.make_archive({"repo/treefmt.toml": owned.encode()})
        with self.assertRaisesRegex(ValueError, "Shared Config Bundle content persisted"):
            audit_module.audit(archive, standards, "b" * 40)
        audit_module.audit(archive, standards, "b" * 40, source)
        with self.assertRaisesRegex(ValueError, "Shared Config Bundle content persisted"):
            audit_module.audit(self.make_archive({"elsewhere/copied-policy": private.encode()}), standards, "b" * 40, source)

    def test_source_provenance_never_waives_checkout_paths_links_or_pin_guards(self):
        standards = self.root / "standards"
        (standards / "config").mkdir(parents=True)
        source = self.root / "source"
        source.mkdir()
        owned = b"Repository-owned bridge fixture\n" * 20
        (standards / "config/treefmt.toml").write_bytes(owned)
        (source / "treefmt.toml").write_bytes(owned)
        pin = "a" * 40
        with self.assertRaisesRegex(ValueError, "Shared Config Directory persisted"):
            audit_module.audit(self.make_archive({"repo/.standards-config/config/treefmt.toml": owned}), standards, pin, source)
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE]:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "Persistent link"):
                audit_module.audit(self.make_archive({}, links={"repo/bridge": (".standards-config/config/treefmt.toml", kind)}), standards, pin, source)
        pinned = (pin + "\n").encode() * 5
        (source / "pin.txt").write_bytes(pinned)
        (standards / "config/pin.txt").write_bytes(pinned)
        with self.assertRaisesRegex(ValueError, "Build pin persisted in a layer"):
            audit_module.audit(self.make_archive({"repo/pin.txt": pinned}), standards, pin, source)
        for image_config in [{"config": {"Env": ["STANDARDS_PIN=other"]}}, {"history": [{"created_by": pin}]}]:
            with self.assertRaisesRegex(ValueError, "Build pin persisted in image configuration/history"):
                audit_module.audit(self.make_archive({}, image_config), standards, pin, source)

    def test_source_provenance_never_follows_symlinks_or_includes_checkout_metadata(self):
        standards = self.root / "standards"
        (standards / "config").mkdir(parents=True)
        private = b"Bundle-only fixture\n" * 20
        (standards / "config/private.yml").write_bytes(private)
        source = self.root / "source"
        (source / ".standards-config/config").mkdir(parents=True)
        (source / ".standards-config/config/private.yml").write_bytes(private)
        (source / ".git").mkdir()
        (source / ".git/copied-policy").write_bytes(private)
        (source / "linked-policy").symlink_to(standards / "config/private.yml")
        linked_source = self.root / "linked-source"
        linked_source.symlink_to(source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Expected prepared source directory"):
            audit_module.audit(self.make_archive({"elsewhere/copied-policy": private}), standards, "a" * 40, linked_source)
        with self.assertRaisesRegex(ValueError, "Shared Config Bundle content persisted"):
            audit_module.audit(self.make_archive({"elsewhere/copied-policy": private}), standards, "a" * 40, source)

    def test_build_audit_receives_prepared_source_not_the_working_checkout(self):
        build = (ASSETS / "build.sh").read_text()
        self.assertIn('python3 "$ASSETS/audit.py" "$CONTEXT/artifact.tar" "$standards" "$pin" "$CONTEXT/src"', build)
        self.assertIn('standards="$GITHUB_WORKSPACE/.standards-config"', build)

    def test_layer_audit_catches_deleted_bundle_paths_content_and_pin_in_config_history(self):
        standards = self.root / "standards"
        (standards / "config").mkdir(parents=True)
        private = b"private bundle fixture\n" * 20
        (standards / "config/private.yml").write_bytes(private)
        pin = "a" * 40
        audit_module.audit(self.make_archive({"repo/source": b"safe"}), standards, pin)
        for files in [{"repo/.standards-config/config/private": b"short"},
                      {"elsewhere/copied-policy": private}, {"state/pin": pin.encode()}]:
            with self.assertRaises(ValueError): audit_module.audit(self.make_archive(files), standards, pin)
        for image_config in [{"config": {"Env": ["STANDARDS_PIN=other"]}}, {"history": [{"created_by": pin}]}]:
            with self.assertRaises(ValueError): audit_module.audit(self.make_archive({}, image_config), standards, pin)

    def stub(self, name, body):
        target = self.root / name
        target.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
        target.chmod(0o755)
        return target

    def test_two_artifact_secret_scan_is_fail_closed_before_any_push(self):
        binary = self.root / "trivy/trivy"
        binary.parent.mkdir()
        binary.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$RUNNER_TEMP/calls"\ncase "${*: -1}" in *-ci:*) exit "${CI_STATUS:-0}";; *) exit "${DEV_STATUS:-0}";; esac\n')
        binary.chmod(0o755)
        self.env.update(IMAGE="ghcr.io/rubio-enterprises/repo/devcontainer", CI_IMAGE="ghcr.io/rubio-enterprises/repo/devcontainer-ci",
                        ARCH="arm64", GITHUB_SHA="a" * 40, PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        self.stub("docker", 'touch "$RUNNER_TEMP/pushed"\n')
        source = script("Scan both local artifacts for secrets") + script("Push the scanned architecture image")
        for dev, ci in [(1, 0), (0, 1), (0, 2), (0, 0)]:
            (self.root / "pushed").unlink(missing_ok=True)
            result = subprocess.run(["bash", "-e", "-c", source], env={**self.env, "DEV_STATUS": str(dev), "CI_STATUS": str(ci)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, dev or ci, result.stderr)
            self.assertEqual((self.root / "pushed").exists(), not (dev or ci))
            self.assertNotIn(".Match", (self.root / "calls").read_text())

    def test_build_orchestration_with_native_architecture_and_rebase_fixtures(self):
        context = self.root / "context"
        for directory in ["features", "pins", "src", "empty", "expected", "verify"]:
            (context / directory).mkdir(parents=True, exist_ok=True)
        (context / "expected/shape").write_text("legacy")
        standards = self.root / "workspace/.standards-config/config"
        standards.mkdir(parents=True)
        (standards / "private.yml").write_text("private fixture\n" * 20)
        safe_archive = self.make_archive({"workspace/source": b"safe"})
        docker = self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$1" = info ]]; then echo "$DOCKER_ARCH"; exit; fi
if [[ "$1" = port ]]; then echo 127.0.0.1:12345; exit; fi
if [[ "$1 $2" = 'image save' ]]; then cp "$RUNNER_TEMP/artifact.tar" "${*: -1}"; exit; fi
for arg in "$@"; do
  if [[ "$arg" = digest ]]; then printf 'sha256:%064d\\n' 0; exit; fi
  if [[ "$arg" = manifest || "$arg" = config ]]; then
    ref="${*: -1}"
    case "$ref" in
      *base-ci@*) name=base-ci;;
      *base@*) name=base;;
      */dev:*) name=dev;;
      */ci:*) name=ci;;
      *) exit 2;;
    esac
    if [[ "$arg" = config ]]; then name="$name-config"; fi
    cat "$RUNNER_TEMP/fixtures/$name.json"
    exit
  fi
done
''')
        self.stub("curl", "exit 0\n")
        cli = self.stub("devcontainer", 'printf "%s\\n" "$*" > "$RUNNER_TEMP/cli-call"\n')
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        args = self.rebased()
        for arch, native in [("amd64", "x86_64"), ("arm64", "aarch64")]:
            for item in args[4:6]: item["architecture"] = arch
            for name, data in zip(["base", "base-ci", "dev", "ci", "dev-config", "ci-config"], args[:6]):
                (fixtures / f"{name}.json").write_text(json.dumps(data))
            for name in ["base-config", "base-ci-config"]:
                (fixtures / f"{name}.json").write_text(json.dumps(config(arch)))
            env = {**self.env, "ASSETS": str(ASSETS), "CONTEXT": str(context), "ARCH": arch,
                   "BASE": "ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64,
                   "BASE_CI": "ghcr.io/rubio-enterprises/devcontainers/base-ci@sha256:" + "b" * 64,
                   "IMAGE": "ghcr.io/rubio-enterprises/repo/devcontainer", "CI_IMAGE": "ghcr.io/rubio-enterprises/repo/devcontainer-ci",
                   "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "c" * 40,
                   "GITHUB_REPOSITORY": "Rubio-Enterprises/repo", "GITHUB_SERVER_URL": "https://github.com",
                   "GITHUB_WORKSPACE": str(self.root / "workspace"), "STANDARDS_PIN": "d" * 40,
                   "DEVCONTAINER": str(cli), "DOCKER_ARCH": native,
                   "PATH": str(docker.parent) + os.pathsep + os.environ["PATH"]}
            result = subprocess.run(["bash", str(ASSETS / "build.sh")], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (self.root / "docker-calls").read_text()
            self.assertIn("--old_base " + env["BASE"] + " --new_base " + env["BASE_CI"], calls)
            self.assertIn("--cache-from " + env["IMAGE"] + ":main-" + arch, calls)
            self.assertIn("--build-arg BUILDKIT_INLINE_CACHE=1", calls)
            self.assertIn("--security-opt no-new-privileges --user 1000", calls)
            self.assertNotIn("push ghcr.io", calls)
            self.assertNotIn("--build-arg STANDARDS_PIN", calls)
            self.assertIn("--platform linux/" + arch, (self.root / "cli-call").read_text())
            (self.root / "docker-calls").unlink()
            result = subprocess.run(["bash", str(ASSETS / "build.sh")], env={**env, "DOCKER_ARCH": "wrong"}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("build --", (self.root / "docker-calls").read_text())
            (self.root / "docker-calls").unlink()
        self.assertTrue(safe_archive.exists())

    def prepare_consumer(self, name, links, extra_files=None, untracked_files=None, gitlinks=None):
        src = self.root / name
        files = {
            ".gitignore": "/.standards-config/\n",
            ".copier-answers.yml": "repo_type: native\nhas_python: true\n",
            ".mise.toml": "[tools]\n",
            ".config/mise-bootstrap.toml": "# Host bootstrap placeholder\n",
            ".github/.devcontainer/devcontainer.json": '{"name":"Repository image build","features":{}}',
            ".devcontainer/devcontainer.json": '{"image":"ghcr.io/rubio-enterprises/example/devcontainer:main"}',
            "AGENTS.md": "Consumer fixture\n",
            "main.py": "print('consumer fixture')\n",
            **(extra_files or {}),
        }
        for name, body in files.items():
            target = src / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        for name, target in links.items():
            link = src / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        def git(*args):
            return subprocess.check_output(["git", "-C", str(src), *args], env=self.env, text=True).strip()
        git("init", "-q")
        git("add", ".")
        for name in extra_files or {}:
            if name.startswith(".standards-config/"):
                git("add", "--force", "--", name)
        for name, sha in (gitlinks or {}).items():
            git("update-index", "--add", "--cacheinfo", f"160000,{sha},{name}")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "test: consumer fixture")
        policy = src / ".standards-config/config/tombi.toml"
        policy.parent.mkdir(parents=True, exist_ok=True)
        if not policy.exists():
            policy.write_text("Untracked policy fixture\n" * 20)
        for name, body in (untracked_files or {}).items():
            target = src / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        cli = self.stub("devcontainer", 'printf \'{"configuration":{"features":{},"configFilePath":{}}}\'\n')
        output = Path(self.env["GITHUB_OUTPUT"])
        output.unlink(missing_ok=True)
        result = subprocess.run(["node", str(ASSETS / "image.mjs"), "prepare"], env={**self.env,
            "GITHUB_WORKSPACE": str(src), "GITHUB_SHA": git("rev-parse", "HEAD"), "STANDARDS_PIN": "b" * 40,
            "BASE": "ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64,
            "CONFIG": ".github/.devcontainer/devcontainer.json", "DEVCONTAINER": str(cli)}, capture_output=True, text=True)
        return src, result

    def test_prepare_omits_org_gitlinks_at_pinned_sha_without_fetching_or_copying_checkout(self):
        for index, url in enumerate(["https://github.com/Rubio-Enterprises/aw-webui.git",
                                     "https://github.com/Rubio-Enterprises/.github"]):
            name, sha = 'nested/web ui%0A"quoted', "c" * 40
            modules = f'[submodule "different-name"]\n path = {json.dumps(name)}\n url = {url}\n'
            src, result = self.prepare_consumer(f"org-submodule-{index}", {}, {".gitmodules": modules},
                                                {f"{name}/untracked-secret": "not archived"}, {name: sha})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(sha, result.stdout)
            self.assertIn("web ui%250A", result.stdout)
            self.assertNotIn(url, result.stdout + result.stderr)
            outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
            source = Path(outputs["context"]) / "src"
            self.assertTrue((source / name).is_dir())
            self.assertFalse((source / name).is_symlink())
            self.assertEqual(list((source / name).iterdir()), [])
            self.assertTrue((src / name / "untracked-secret").exists())
            for kind in ["nonempty", "symlink", "missing"]:
                with self.subTest(kind=kind):
                    directory = source / name
                    if kind == "nonempty":
                        (directory / "unexpected").write_text("not allowed")
                    elif kind == "symlink":
                        (directory / "unexpected").unlink()
                        directory.rmdir()
                        directory.symlink_to(src / name)
                    else:
                        directory.unlink()
                    checked = self.node("qualifySubmodules", [str(src), str(source)])
                    self.assertNotEqual(checked.returncode, 0)
                    self.assertIn("empty non-symlink directory", checked.stderr)
            (source / ".gitmodules").unlink()
            (source / ".gitmodules").symlink_to(src / ".gitmodules")
            checked = self.node("qualifySubmodules", [str(src), str(source)])
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("regular committed .gitmodules", checked.stderr)

    def test_prepare_rejects_unqualified_gitlinks_and_metadata_mismatches(self):
        good = "https://github.com/Rubio-Enterprises/ui.git"
        urls = ["https://github.com/ActivityWatch/ui.git", "git@github.com:Rubio-Enterprises/ui.git",
                "ssh://git@github.com/Rubio-Enterprises/ui.git", "../ui.git", "./ui",
                "https://token@github.com/Rubio-Enterprises/ui.git",
                "https://github.com/Rubio-Enterprises/ui.git?token=secret",
                "https://github.com/Rubio-Enterprises/ui.git#main",
                "https://github.com/Rubio-Enterprises/ui.git/", "https://github.com/Rubio-Enterprises/../ui",
                "https://github.com/Rubio-Enterprises.evil/ui.git", "https://githubXcom/Rubio-Enterprises/ui",
                "https://github.com/rubio-enterprises/ui", "https://github.com/Rubio-Enterprises/..", ""]
        cases = [{".gitmodules": f'[submodule "ui"]\n path = ui\n url = {json.dumps(url)}\n'} for url in urls]
        cases.extend([
            {},
            {".gitmodules": "not valid git config"},
            {".gitmodules": f'[submodule "ui"]\n path = other\n url = {good}\n'},
            {".gitmodules": f'[submodule "ui"]\n path = ui\n url = {good}\n url = {good}\n'},
            {".gitmodules": f'[submodule "ui"]\n path = ui\n url = {good}\n[submodule "extra"]\n path = ui\n url = {good}\n'},
            {".gitmodules": f'[submodule "ui"]\n path = ui\n url = {good}\n',
             ".gitattributes": "ui export-ignore\n"},
        ])
        for index, files in enumerate(cases):
            with self.subTest(index=index):
                _, result = self.prepare_consumer(f"unqualified-{index}", {}, files, gitlinks={"ui": "c" * 40})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("token@", result.stdout + result.stderr)
                self.assertFalse((self.root / "output").exists())
                self.assertFalse(list(self.root.glob("repository-image-*")))

    def test_gitlink_qualification_reads_committed_modules_and_keeps_source_guards(self):
        good = '[submodule "ui"]\n path = ui\n url = https://github.com/Rubio-Enterprises/ui.git\n'
        for index, (links, files) in enumerate([
            ({"escape": "../outside"}, {".gitmodules": good}),
            ({}, {".gitmodules": good, ".standards-config/private": "tracked policy"}),
        ]):
            with self.subTest(index=index):
                _, result = self.prepare_consumer(f"gitlink-guard-{index}", links, files, gitlinks={"ui": "c" * 40})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "output").exists())
                self.assertFalse(list(self.root.glob("repository-image-*")))
        bad = good.replace("Rubio-Enterprises", "ActivityWatch")
        _, result = self.prepare_consumer("untracked-module-rewrite", {}, {".gitmodules": bad},
                                          {".gitmodules": good}, {"ui": "c" * 40})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("organization HTTPS URL", result.stderr)

    def test_prepare_omits_exact_shared_config_stub_without_changing_runtime_checkout(self):
        src, result = self.prepare_consumer("consumer", {
            "tombi.toml": ".standards-config/config/tombi.toml", "CLAUDE.md": "AGENTS.md"})
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        context = Path(outputs["context"])
        self.assertFalse(os.path.lexists(context / "src/tombi.toml"))
        self.assertFalse((context / "src/.standards-config").exists())
        self.assertEqual((context / "src/CLAUDE.md").readlink(), Path("AGENTS.md"))
        self.assertEqual((context / "src/main.py").read_text(), (src / "main.py").read_text())
        self.assertEqual((src / "tombi.toml").readlink(), Path(".standards-config/config/tombi.toml"))
        self.assertTrue((src / ".standards-config/config/tombi.toml").is_file())
        self.assertTrue((context / "pins/.config/mise-bootstrap.toml").is_file())
        self.assertFalse(os.path.lexists(context / "pins/tombi.toml"))

    def test_prepare_preserves_repository_owned_regular_tombi_config(self):
        _, result = self.prepare_consumer("regular", {}, {"tombi.toml": "[schema]\nenabled = false\n"})
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        self.assertEqual((Path(outputs["context"]) / "src/tombi.toml").read_text(), "[schema]\nenabled = false\n")

    def test_prepare_still_rejects_unknown_policy_links_and_escaping_stub_substitutions(self):
        for index, (name, target) in enumerate([
            ("tombi.toml", ".standards-config/config/other.toml"),
            ("other.toml", ".standards-config/config/tombi.toml"),
            ("nested/tombi.toml", "../.standards-config/config/tombi.toml"),
            ("tombi.toml", "./.standards-config/config/tombi.toml"),
            ("tombi.toml", ".standards-config/../.standards-config/config/tombi.toml"),
            ("tombi.toml", "../outside"),
            ("tombi.toml", str(self.root / "outside")),
        ]):
            with self.subTest(name=name, target=target):
                _, result = self.prepare_consumer(f"rejected-{index}", {name: target})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Source symlink escapes the clean context", result.stderr)
                self.assertFalse((self.root / "output").exists())
                self.assertFalse(list(self.root.glob("repository-image-*")))

    def test_prepare_still_rejects_tracked_policy_with_the_known_stub(self):
        _, result = self.prepare_consumer("tracked-policy", {"tombi.toml": ".standards-config/config/tombi.toml"},
                                          {".standards-config/config/tombi.toml": "Tracked policy fixture\n"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tracked Git or standards checkout rejected", result.stderr)
        self.assertFalse((self.root / "output").exists())
        self.assertFalse(list(self.root.glob("repository-image-*")))

    def test_prepare_uses_committed_source_and_cli_jsonc_not_workspace_untracked_files(self):
        src = self.root / "workspace"
        (src / ".github/.devcontainer").mkdir(parents=True)
        (src / ".github/.devcontainer/devcontainer.json").write_text('{// JSONC\n"features":{}}')
        (src / ".mise.toml").write_text('[tools]\n')
        (src / "source.py").write_text("print('safe')")
        (src / ".copier-answers.yml").write_text("repo_type: native\n")
        (src / ".config").mkdir()
        (src / ".config/mise-bootstrap.toml").write_text("# bootstrap\n")
        def git(*args):
            return subprocess.check_output(["git", "-C", str(src), *args], env=self.env, text=True).strip()
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "test: fixture")
        (src / ".standards-config").mkdir()
        (src / ".standards-config/private").write_text("not copied")
        (src / "untracked-secret").write_text("not copied")
        cli = self.stub("devcontainer", 'printf \'{"configuration":{"features":{},"configFilePath":{}}}\'\n')
        result = subprocess.run(["node", str(ASSETS / "image.mjs"), "prepare"], env={**self.env,
            "GITHUB_WORKSPACE": str(src), "GITHUB_SHA": git("rev-parse", "HEAD"), "STANDARDS_PIN": "b" * 40,
            "BASE": "ghcr.io/rubio-enterprises/devcontainers/base@sha256:" + "a" * 64,
            "CONFIG": ".github/.devcontainer/devcontainer.json", "DEVCONTAINER": str(cli)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        context = Path(outputs["context"])
        self.assertTrue((context / "src/source.py").exists())
        self.assertFalse((context / "src/.standards-config").exists())
        self.assertFalse((context / "src/.git").exists())
        self.assertFalse((context / "src/untracked-secret").exists())
        self.assertTrue((context / "pins/.mise.toml").exists())
        self.assertFalse((context / "pins/source.py").exists())
        self.assertEqual(outputs["assets"], str(ASSETS))
        self.assertEqual(json.loads((context / "features/devcontainer.json").read_text())["remoteUser"], "vscode")


if __name__ == "__main__":
    unittest.main()
