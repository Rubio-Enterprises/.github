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
FROZEN_RECIPE = ROOT / "tests/fixtures/frozen-repository-image-v2.Dockerfile"
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
        self.assertIn("ARCH: amd64", TEXT)
        self.assertNotIn("matrix.arch", TEXT)
        executable = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("arm64", executable)
        self.assertNotIn("ARM64", TEXT)
        self.assertNotIn("strategy:", TEXT.split("\n  merge:\n", 1)[0])
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
                 "Prepare central generic build inputs", "Build, audit and verify the image pair",
                 "Scan the dev artifact for secrets", "Push the verified image pair"]
        positions = [TEXT.index(f"- name: {name}") for name in names]
        self.assertEqual(positions, sorted(positions))
        scan = script("Scan the dev artifact for secrets")
        self.assertNotIn('for artifact', scan)
        self.assertIn('ref="$(< "$CONTEXT/dev.ref")"', scan)
        self.assertNotIn('ci.ref', scan)
        self.assertIn('--image-src remote --insecure --scanners secret --image-config-scanners secret', scan)
        self.assertIn('"$ref"', scan)
        build = (ASSETS / "build.sh").read_text()
        self.assertIn('pull --insecure "$ref" /context/artifact.tar', build)
        self.assertNotIn('docker image save', build)
        self.assertIn('--config \'\' --ignorefile \'\'', scan)
        self.assertIn('if [[ "$status" != 0 ]]; then exit "$status"; fi', scan)
        self.assertNotIn(".Match", scan)
        self.assertNotIn(".Content", scan)
        self.assertIn('needs: [prepare, build]', TEXT)
        self.assertIn('needs: [prepare, merge]', TEXT)
        self.assertIn('delete-only-untagged-versions: "true"', TEXT)
        self.assertIn('min-versions-to-keep: 5', TEXT)
        self.assertIn('ci-image: ${{ steps.image.outputs.image }}-ci', TEXT)
        for name in ("Resolve image and publication policy", "Scan the dev artifact for secrets",
                     "Publish single-platform indices and cache aliases"):
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

    def test_legacy_reusable_is_retired(self):
        self.assertFalse((ROOT / ".github/workflows/devcontainer-image.yml").exists())
        self.assertFalse((ROOT / "docs/reusable-workflows/devcontainer-image.md").exists())


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

    def resolve(self, base=None, base_ci=None, tamper=None, arches=("amd64", "arm64"), bad_amd64=False):
        """Run resolve against an in-memory registry; returns (process, outputs)."""
        def blob(value):
            data = json.dumps(value).encode()
            return "sha256:" + hashlib.sha256(data).hexdigest(), data

        content, indices = {}, {}
        base_digest = None
        for kind in ["base", "base-ci"]:
            entries = []
            for arch in arches:
                args = self.pair(arch)
                image_config = args[2] if kind == "base" else args[3]
                if bad_amd64 and arch == "amd64":
                    image_config["architecture"] = "arm64"
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

        def corrupt_unused_arm64(content, indices):
            for kind in ["base", "base-ci"]:
                index = json.loads(content[f"{kind}/manifests/latest"])
                entry = next(entry for entry in index["manifests"] if entry["platform"]["architecture"] == "arm64")
                content[f"{kind}/manifests/{entry['digest']}"] = b"unused malformed arm64 manifest"

        result, values, _ = self.resolve(tamper=corrupt_unused_arm64)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(values), 2)
        result, values, _ = self.resolve(arches=("amd64",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(values), 2)

        name = "ghcr.io/rubio-enterprises/devcontainers/"
        for label, kwargs in [("tampered blob", {"tamper": corrupt}),
                              ("missing amd64", {"arches": ("arm64",)}),
                              ("duplicate amd64", {"arches": ("amd64", "amd64")}),
                              ("wrong amd64 config", {"bad_amd64": True}),
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

    def test_provenance_rejects_missing_or_wrong_base_on_either_artifact_and_arch(self):
        key = "org.opencontainers.image.base.digest"
        for arch in ["amd64", "arm64"]:
            expected = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
            images = [{"annotations": {key: digest}} for digest in expected]
            self.assertEqual(self.node("validateProvenance", [*images, *expected]).returncode, 0, arch)
            for artifact in range(2):
                for value in [None, "sha256:" + "c" * 64, "malformed"]:
                    damaged = json.loads(json.dumps(images))
                    damaged[artifact]["annotations"] = {} if value is None else {key: value}
                    result = self.node("validateProvenance", [*damaged, *expected])
                    self.assertNotEqual(result.returncode, 0, (arch, artifact, value))
                    self.assertIn("base digest annotation", result.stderr)

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

    def test_single_dev_secret_scan_is_fail_closed_before_either_push(self):
        binary = self.root / "trivy/trivy"
        binary.parent.mkdir()
        binary.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$RUNNER_TEMP/calls"\ncase "${*: -1}" in */ci@*) exit "${CI_STATUS:-0}";; *) exit "${DEV_STATUS:-0}";; esac\n')
        binary.chmod(0o755)
        self.env.update(IMAGE="ghcr.io/rubio-enterprises/repo/devcontainer", CI_IMAGE="ghcr.io/rubio-enterprises/repo/devcontainer-ci",
                        CONTEXT=str(self.root), ARCH="amd64", GITHUB_SHA="a" * 40,
                        PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        digest = "sha256:" + "b" * 64
        for name in ["dev", "ci"]:
            (self.root / f"{name}.ref").write_text(f"127.0.0.1:12345/repository/{name}@{digest}\n")
        self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$*" != *"--user $(id -u):$(id -g)"* ]]; then echo 'crane cannot read runner-owned auth' >&2; exit 13; fi
for arg in "$@"; do
  if [[ "$arg" = copy ]]; then touch "$RUNNER_TEMP/pushed"; exit "${COPY_STATUS:-0}"; fi
  if [[ "$arg" = digest ]]; then printf '%s\\n' "${PUBLISHED_DIGEST:-sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb}"; exit; fi
done
exit 2
''')
        source = script("Scan the dev artifact for secrets") + script("Push the verified image pair")
        for dev in (1, 2, 0):
            (self.root / "pushed").unlink(missing_ok=True)
            (self.root / "calls").unlink(missing_ok=True)
            result = subprocess.run(["bash", "-e", "-c", source], env={**self.env, "DEV_STATUS": str(dev), "CI_STATUS": "99"}, capture_output=True, text=True)
            self.assertEqual(result.returncode, dev, result.stderr)
            self.assertEqual((self.root / "pushed").exists(), not dev)
            calls = (self.root / "calls").read_text().splitlines()
            self.assertEqual(len(calls), 1)
            self.assertNotIn(".Match", calls[0])
            self.assertIn("--image-src remote --insecure", calls[0])
            self.assertIn("/dev@" + digest, calls[0])
            self.assertNotIn("/ci@", calls[0])
        copies = (self.root / "docker-calls").read_text()
        for name in ["dev", "ci"]:
            self.assertIn("copy " + (self.root / f"{name}.ref").read_text().strip(), copies)
        self.assertNotIn("push ghcr.io", copies)
        for overrides in [{"COPY_STATUS": "2"}, {"PUBLISHED_DIGEST": "sha256:" + "c" * 64}]:
            result = subprocess.run(["bash", "-e", "-c", source], env={**self.env, **overrides}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, overrides)

    def test_build_orchestration_with_native_architecture_and_rebase_fixtures(self):
        context = self.root / "context"
        for directory in ["features", "pins", "src", "empty", "expected", "verify"]:
            (context / directory).mkdir(parents=True, exist_ok=True)
        (context / "package").mkdir()
        (context / "package/Dockerfile").write_text("FROM scratch AS ci\n")
        with tarfile.open(context / "package/standards.tgz", "w:gz") as package:
            member = tarfile.TarInfo("package/config/fixture")
            member.size = 7
            package.addfile(member, io.BytesIO(b"fixture"))
        standards = self.root / "workspace/.standards-config/config"
        standards.mkdir(parents=True)
        (standards / "private.yml").write_text("private fixture\n" * 20)
        safe_archive = self.make_archive({"workspace/source": b"safe"})
        docker = self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$1" = info ]]; then echo "$DOCKER_ARCH"; exit; fi
if [[ "$1" = port ]]; then echo 127.0.0.1:12345; exit; fi
if [[ "$1 $2" = 'image save' ]]; then cp "$RUNNER_TEMP/artifact.tar" "${*: -1}"; exit; fi
for arg in "$@"; do
  if [[ "$arg" = pull && "$1" = run ]]; then
    if [[ "$*" != *"--user $(id -u):$(id -g)"* ]]; then echo 'crane cannot write runner-owned context' >&2; exit 13; fi
    cp "$RUNNER_TEMP/artifact.tar" "$CONTEXT/artifact.tar"; exit
  fi
  if [[ "$arg" = digest ]]; then printf 'sha256:%064d\\n' 0; exit; fi
  if [[ "$arg" = manifest || "$arg" = config ]]; then
    ref="${*: -1}"
    case "$ref" in
      *base-ci@*) name=base-ci;;
      *base@*) name=base;;
      */dev:*|*/dev@*) name=dev;;
      */ci:*|*/ci@*) name=ci;;
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
        for item, char in [(args[2], "a"), (args[3], "b")]:
            item["annotations"] = {"org.opencontainers.image.base.digest": "sha256:" + char * 64}
        for shape, native in [("legacy", "x86_64"), ("legacy", "amd64"), ("v2", "x86_64"), ("v2", "amd64")]:
            arch = "amd64"
            (context / "expected/shape").write_text(shape)
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
            build_calls = [call for call in calls.splitlines() if call.startswith("build ")]
            self.assertEqual(len(build_calls), 2)
            for call in build_calls:
                recipe = context / "package/Dockerfile" if shape == "v2" else ASSETS / "Dockerfile"
                self.assertIn("--file " + str(recipe), call)
                self.assertIn("--build-context pins=" + str(context / "pins"), call)
                self.assertIn("--build-context expected=" + str(context / "expected"), call)
                if shape == "v2":
                    self.assertIn("--target ci", call)
                    self.assertIn("--secret id=standards_package,src=" + str(context / "package/standards.tgz"), call)
                    self.assertTrue(call.endswith(str(context / "build")))
                    self.assertNotIn("--build-context standards=", call)
                    self.assertNotIn("--build-context src=", call)
                else:
                    self.assertNotIn("--target", call)
                    self.assertIn("--build-context standards=", call)
            self.assertIn("--security-opt no-new-privileges --user 1000", calls)
            self.assertNotIn("push ghcr.io", calls)
            self.assertNotIn("--build-arg STANDARDS_PIN", calls)
            self.assertIn("--platform linux/" + arch, (self.root / "cli-call").read_text())
            self.assertIn("pull --insecure " + (context / "dev.ref").read_text().strip(), calls)
            self.assertNotIn("pull --insecure " + (context / "ci.ref").read_text().strip(), calls)
            self.assertEqual(result.stdout.count("Audited registry artifact:"), 1)
            cache_calls = [call for call in calls.splitlines() if "-s -- " in call]
            self.assertEqual(len(cache_calls), 3)
            for call, (image, uid) in zip(cache_calls, [(env["IMAGE"], 1000), (env["IMAGE"], 1001), (env["CI_IMAGE"], 1000)]):
                self.assertIn(f"{image}:sha-{env['GITHUB_SHA']}-amd64 -s -- {uid}", call)
            self.assertNotIn("image save", calls)
            self.assertNotIn("rm -f repository-image-", calls)
            for name in ["dev", "ci"]:
                good = (fixtures / f"{name}.json").read_text()
                damaged = json.loads(good)
                damaged.pop("annotations")
                (fixtures / f"{name}.json").write_text(json.dumps(damaged))
                rejected = subprocess.run(["bash", str(ASSETS / "build.sh")], env=env, capture_output=True, text=True)
                self.assertNotEqual(rejected.returncode, 0, (arch, name))
                self.assertIn("base digest annotation", rejected.stderr)
                (fixtures / f"{name}.json").write_text(good)
            (self.root / "docker-calls").unlink()
            result = subprocess.run(["bash", str(ASSETS / "build.sh")], env={**env, "DOCKER_ARCH": "aarch64"}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("build --", (self.root / "docker-calls").read_text())
            (self.root / "docker-calls").unlink()
        self.assertTrue(safe_archive.exists())

    def test_publication_wraps_each_amd64_child_in_a_one_entry_index(self):
        self.stub("docker", '''printf '%s\\n' "$*" >> "$RUNNER_TEMP/docker-calls"
if [[ "$*" == *'inspect --raw'* ]]; then
  printf '%s\\n' "${INDEX_JSON:?}"
fi
''')
        image = "ghcr.io/rubio-enterprises/repo/devcontainer"
        sha = "a" * 40
        index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                 "manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}]}
        env = {**self.env, "IMAGE": image, "CI_IMAGE": image + "-ci", "GITHUB_SHA": sha,
               "INDEX_JSON": json.dumps(index), "PATH": str(self.root) + os.pathsep + os.environ["PATH"]}
        source = script("Publish single-platform indices and cache aliases")
        result = subprocess.run(["bash", "-e", "-c", source], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / "docker-calls").read_text().splitlines()
        self.assertEqual(len(calls), 8)
        for artifact, offset in [(image, 0), (image + "-ci", 4)]:
            self.assertEqual(calls[offset], f"buildx imagetools create --prefer-index=true --tag {artifact}:main --tag {artifact}:sha-{sha} {artifact}:sha-{sha}-amd64")
            self.assertEqual(calls[offset + 1], f"buildx imagetools create --prefer-index=false --tag {artifact}:main-amd64 {artifact}:sha-{sha}-amd64")
            for tag, call in zip(("main", f"sha-{sha}"), calls[offset + 2:offset + 4]):
                self.assertEqual(call, f"buildx imagetools inspect --raw {artifact}:{tag}")
        self.assertNotIn("arm64", "\n".join(calls))
        for bad in [dict(index, manifests=[]), dict(index, manifests=index["manifests"] * 2),
                    dict(index, mediaType="application/vnd.oci.image.manifest.v1+json"),
                    dict(index, manifests=[{"platform": {"os": "linux", "architecture": "arm64"}}])]:
            result = subprocess.run(["bash", "-e", "-c", source], env={**env, "INDEX_JSON": json.dumps(bad)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, bad)

    def test_image_name_and_config_validation_are_preserved(self):
        source = script("Resolve image and publication policy")
        env = {**self.env, "GITHUB_REPOSITORY_OWNER": "Rubio-Enterprises",
               "GITHUB_REPOSITORY": "Rubio-Enterprises/Repo", "CONFIG": ".github/.devcontainer/devcontainer.json",
               "PUBLISH": "false", "IMAGE_INPUT": ""}
        for image in ("", "ghcr.io/Rubio-Enterprises/Repo/qual"):
            result = subprocess.run(["bash", "-e", "-c", source], env={**env, "IMAGE_INPUT": image}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("image=ghcr.io/rubio-enterprises/repo/qual", (self.root / "output").read_text())
        for overrides in [{"IMAGE_INPUT": value} for value in (
            "evil.io/rubio-enterprises/repo", "ghcr.io/other/repo", "ghcr.io/rubio-enterprises/repo:tag",
            "ghcr.io/rubio-enterprises/repo@sha256:" + "a" * 64, "ghcr.io/rubio-enterprises/repo\\npublish=true")
        ] + [{"CONFIG": value} for value in ("", "/etc/config", "../config", "dir/../config", "dir/..")]:
            result = subprocess.run(["bash", "-e", "-c", source], env={**env, **overrides}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, overrides)

    def test_legacy_image_build_pin_remains_strict(self):
        for pin in ("", "a" * 39, "A" * 40, "a" * 40 + "\\nother=value", "a" * 40):
            result = subprocess.run(["bash", "-e", "-c", script("Require the image build pin")],
                                    env={**self.env, "STANDARDS_PIN": pin}, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, pin == "a" * 40)

    def test_trivy_failed_checksum_prevents_extraction(self):
        self.stub("curl", 'touch "$RUNNER_TEMP/trivy.tar.gz"\n')
        self.stub("sha256sum", 'cat > "$RUNNER_TEMP/checksum-input"; exit 1\n')
        self.stub("tar", 'touch "$RUNNER_TEMP/extracted"\n')
        result = subprocess.run(["bash", "-e", "-c", script("Install checksum-pinned Trivy")],
                                env={**self.env, "PATH": str(self.root) + os.pathsep + os.environ["PATH"]}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "extracted").exists())
        self.assertTrue((self.root / "checksum-input").read_text().startswith("c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0  "))

    def test_workflow_native_gate_rejects_arm64_before_building(self):
        self.stub("docker", 'echo "${DOCKER_ARCH:?}"\n')
        for arch, native, success in [("amd64", "x86_64", True), ("amd64", "amd64", True),
                                       ("amd64", "aarch64", False), ("arm64", "aarch64", False)]:
            result = subprocess.run(["bash", "-e", "-c", script("Verify native Docker architecture")],
                                    env={**self.env, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                                         "ARCH": arch, "DOCKER_ARCH": native}, capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, (arch, native))

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
