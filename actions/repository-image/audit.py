import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath


def bundle_hashes(standards):
    return {
        hashlib.sha256(file.read_bytes()).digest()
        for file in (standards / "config").rglob("*")
        if file.is_file() and file.stat().st_size >= 128
    }


def audit(archive, standards, pin):
    fingerprints = bundle_hashes(standards)
    if not fingerprints:
        raise ValueError("Shared Config Bundle is missing")
    with tarfile.open(archive) as saved:
        manifests = json.load(saved.extractfile("manifest.json"))
        if len(manifests) != 1:
            raise ValueError("Expected one local architecture image")
        manifest = manifests[0]
        config = saved.extractfile(manifest["Config"]).read()
        if pin.encode() in config or any(value.startswith("STANDARDS_PIN=") for value in json.loads(config).get("config", {}).get("Env", [])):
            raise ValueError("Build pin persisted in image configuration/history")
        for layer in manifest["Layers"]:
            with tarfile.open(fileobj=saved.extractfile(layer), mode="r|*") as contents:
                for member in contents:
                    parts = PurePosixPath(member.name).parts
                    if ".standards-config" in parts and (member.isfile() or member.issym() or member.islnk()):
                        raise ValueError("Shared Config Directory persisted in a layer")
                    if member.issym() or member.islnk():
                        if ".standards-config" in PurePosixPath(member.linkname).parts:
                            raise ValueError("Persistent link to build-only standards checkout")
                    if member.isfile():
                        data = contents.extractfile(member)
                        checksum = hashlib.sha256()
                        previous = b""
                        while block := data.read(1024 * 1024):
                            if pin.encode() in previous + block:
                                raise ValueError("Build pin persisted in a layer")
                            previous = block[-len(pin):]
                            checksum.update(block)
                        if member.size >= 128 and checksum.digest() in fingerprints:
                            raise ValueError("Shared Config Bundle content persisted in a layer")
    print("Image layer/config audit passed (no matched content rendered)")


if __name__ == "__main__":
    try:
        audit(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (ValueError, KeyError, tarfile.TarError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
