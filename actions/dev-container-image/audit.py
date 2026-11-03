import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
# tarfile reads gzip, bzip2 and xz but not zstd before Python 3.14, and the job always has Node
ZSTD_DECOMPRESS = "process.stdin.pipe(require('node:zlib').createZstdDecompress()).pipe(process.stdout)"


def _streams(paths):
    """Yield each layer blob as a readable byte stream, decompressing zstd blobs one at a time."""
    for path in paths:
        with path.open("rb") as blob:
            zstd = blob.peek(len(ZSTD_MAGIC))[: len(ZSTD_MAGIC)] == ZSTD_MAGIC
            if not zstd:
                yield blob
                continue
        # A fresh descriptor: the child reads from the file's start, wherever buffering left this one
        with path.open("rb") as raw:
            process = subprocess.Popen(["node", "-e", ZSTD_DECOMPRESS], stdin=raw, stdout=subprocess.PIPE)
        try:
            yield process.stdout
        finally:
            process.kill()
            process.wait()
            process.stdout.close()


def bundle_hashes(package_archive):
    if not package_archive.is_file():
        raise ValueError("Expected the verified Standards Package archive")
    fingerprints = {hashlib.sha256(package_archive.read_bytes()).digest()}
    with tarfile.open(package_archive) as package:
        for member in package:
            if member.isfile() and member.size >= 128 and member.name != "package/LICENSE":
                fingerprints.add(hashlib.sha256(package.extractfile(member).read()).digest())
    return fingerprints


def _fingerprints(package_archive, source):
    fingerprints = bundle_hashes(package_archive)
    if not fingerprints:
        raise ValueError("Shared Config Bundle is missing")
    if source is not None:
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Expected prepared source directory")
        for file in source.rglob("*"):
            if (file.is_symlink() or not file.is_file() or file.stat().st_size < 128
                    or {".git", ".standards-config", ".standards"}.intersection(file.relative_to(source).parts)):
                continue
            checksum = hashlib.sha256()
            with file.open("rb") as data:
                while block := data.read(1024 * 1024):
                    checksum.update(block)
            fingerprints.discard(checksum.digest())
    return fingerprints


def _inspect(config, layers, fingerprints):
    env = json.loads(config).get("config", {}).get("Env", [])
    if any(value.startswith("STANDARDS_PIN=") for value in env):
        raise ValueError("Build pin persisted in image configuration/history")
    if any(value.startswith(("NODE_AUTH_TOKEN=", "STANDARDS_VERSION=")) for value in env):
        raise ValueError("Build-only package environment persisted in image configuration")
    for layer in layers:
        with tarfile.open(fileobj=layer, mode="r|*") as contents:
            for member in contents:
                parts = PurePosixPath(member.name.lstrip("/")).parts
                if ".standards-config" in parts and (member.isfile() or member.issym() or member.islnk()):
                    raise ValueError("Shared Config Directory persisted in a layer")
                project_git = len(parts) > 2 and parts[0] == "workspaces" and parts[2] == ".git"
                project_profile = len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "mise.repository-image.toml"
                if (".standards" in parts or project_git or project_profile) and (member.isfile() or member.issym() or member.islnk()):
                    raise ValueError("Package or temporary Git state persisted in a layer")
                if member.issym() or member.islnk():
                    target = PurePosixPath(member.linkname).parts
                    if ".standards-config" in target:
                        raise ValueError("Persistent link to build-only standards checkout")
                    if ".standards" in target:
                        location = PurePosixPath(member.name.lstrip("/")).parts
                        wiring = (member.issym() and len(location) == 6 and location[0] == "workspaces"
                                  and location[2:] == (".config", "mise", "conf.d", "standards.toml")
                                  and member.linkname == "../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml")
                        if not wiring:
                            raise ValueError("Unqualified link to Standards Package")
                if member.isfile():
                    data = contents.extractfile(member)
                    checksum = hashlib.sha256()
                    while block := data.read(1024 * 1024):
                        checksum.update(block)
                    if member.size >= 128 and checksum.digest() in fingerprints:
                        raise ValueError("Shared Config Bundle content persisted in a layer")


def audit(archive, package_archive, source=None):
    """Audit a saved image archive, or a directory of `config.json` and ordered `layer-*` blobs.

    The directory form holds only the layers above the public base image; the caller has already proven the base layers
    byte-identical to the published base, so the audit skips them instead of downloading them. A layer blob may be
    uncompressed or compressed with gzip, bzip2, xz or zstd.
    """
    archive = Path(archive)
    fingerprints = _fingerprints(package_archive, source)
    if archive.is_dir():
        layers = sorted(archive.glob("layer-*"))
        if not (archive / "config.json").is_file() or not layers:
            raise ValueError("Expected image config and at least one layer")
        streams = _streams(layers)
        try:
            _inspect((archive / "config.json").read_bytes(), streams, fingerprints)
        finally:
            streams.close()
    else:
        with tarfile.open(archive) as saved:
            manifests = json.load(saved.extractfile("manifest.json"))
            if len(manifests) != 1:
                raise ValueError("Expected one local architecture image")
            manifest = manifests[0]
            _inspect(saved.extractfile(manifest["Config"]).read(), (saved.extractfile(layer) for layer in manifest["Layers"]), fingerprints)
    print("Image layer/config audit passed (no matched content rendered)")


if __name__ == "__main__":
    try:
        audit(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)
    except (ValueError, KeyError, tarfile.TarError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
