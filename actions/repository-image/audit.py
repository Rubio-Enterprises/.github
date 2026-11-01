import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath


def bundle_hashes(standards):
    if standards.is_file():
        fingerprints = {hashlib.sha256(standards.read_bytes()).digest()}
        with tarfile.open(standards) as package:
            for member in package:
                if member.isfile() and member.size >= 128 and member.name != "package/LICENSE":
                    fingerprints.add(hashlib.sha256(package.extractfile(member).read()).digest())
        return fingerprints
    return {
        hashlib.sha256(file.read_bytes()).digest()
        for file in (standards / "config").rglob("*")
        if file.is_file() and file.stat().st_size >= 128
    }


def audit(archive, standards, pin, source=None):
    package_mode = standards.is_file()
    if package_mode == bool(pin):
        raise ValueError("Expected a legacy pin or an unpinned package archive")
    fingerprints = bundle_hashes(standards)
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
    with tarfile.open(archive) as saved:
        manifests = json.load(saved.extractfile("manifest.json"))
        if len(manifests) != 1:
            raise ValueError("Expected one local architecture image")
        manifest = manifests[0]
        config = saved.extractfile(manifest["Config"]).read()
        if (pin and pin.encode() in config) or any(value.startswith("STANDARDS_PIN=") for value in json.loads(config).get("config", {}).get("Env", [])):
            raise ValueError("Build pin persisted in image configuration/history")
        if package_mode and any(value.startswith(("NODE_AUTH_TOKEN=", "STANDARDS_VERSION="))
                                for value in json.loads(config).get("config", {}).get("Env", [])):
            raise ValueError("Build-only package environment persisted in image configuration")
        for layer in manifest["Layers"]:
            with tarfile.open(fileobj=saved.extractfile(layer), mode="r|*") as contents:
                for member in contents:
                    parts = PurePosixPath(member.name.lstrip("/")).parts
                    if ".standards-config" in parts and (member.isfile() or member.issym() or member.islnk()):
                        raise ValueError("Shared Config Directory persisted in a layer")
                    project_git = len(parts) > 2 and parts[0] == "workspaces" and parts[2] == ".git"
                    project_profile = len(parts) == 3 and parts[0] == "workspaces" and parts[2] == "mise.repository-image.toml"
                    if package_mode and (".standards" in parts or project_git or project_profile) and (member.isfile() or member.issym() or member.islnk()):
                        raise ValueError("Package or temporary Git state persisted in a layer")
                    if member.issym() or member.islnk():
                        target = PurePosixPath(member.linkname).parts
                        if ".standards-config" in target:
                            raise ValueError("Persistent link to build-only standards checkout")
                        if ".standards" in target:
                            location = PurePosixPath(member.name.lstrip("/")).parts
                            wiring = (package_mode and member.issym() and len(location) == 6 and location[0] == "workspaces"
                                      and location[2:] == (".config", "mise", "conf.d", "standards.toml")
                                      and member.linkname == "../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml")
                            if not wiring:
                                raise ValueError("Unqualified link to Standards Package")
                    if member.isfile():
                        data = contents.extractfile(member)
                        checksum = hashlib.sha256()
                        previous = b""
                        while block := data.read(1024 * 1024):
                            if pin and pin.encode() in previous + block:
                                raise ValueError("Build pin persisted in a layer")
                            previous = block[-len(pin):]
                            checksum.update(block)
                        if member.size >= 128 and checksum.digest() in fingerprints:
                            raise ValueError("Shared Config Bundle content persisted in a layer")
    print("Image layer/config audit passed (no matched content rendered)")


if __name__ == "__main__":
    try:
        audit(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]) if len(sys.argv) > 4 else None)
    except (ValueError, KeyError, tarfile.TarError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
