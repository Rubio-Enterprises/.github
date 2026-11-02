from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import tomllib
from pathlib import Path

RECIPE = "package/image/Dockerfile"


def recipe_bytes(package: tarfile.TarFile, version: str) -> bytes:
    members = [member for member in package.getmembers() if member.name == RECIPE]
    if not members:
        raise ValueError(
            f"Standards Release {version} has no image/Dockerfile; repository images require "
            "2.1.0 or newer. Remove the older STANDARDS_VERSION Release Pin or raise it to 2.1.0+."
        )
    if len(members) != 1 or not members[0].isreg() or not 0 < members[0].size <= 256 * 1024:
        raise ValueError("Package image/Dockerfile must be one nonempty regular file, at most 256 KiB")
    recipe = package.extractfile(members[0]).read()
    recipe.decode("utf-8")
    return recipe


def extract_recipe(archive: Path, destination: Path, version: str) -> None:
    with tarfile.open(archive, "r:gz") as package:
        recipe = recipe_bytes(package, version)
    with destination.open("xb") as output:
        output.write(recipe)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def image_inputs(archive: Path, version: str) -> dict:
    """Content of a Standards Release that can shape a repository image, without its version.

    The image build reads the recipe and, through the package wiring symlink, mise/standards.toml.
    Everything else (configs, lefthook, starter, skills) is read only by checks, never by setup;
    the whole package is still reported for repositories whose setup reads arbitrary files.
    """
    with tarfile.open(archive, "r:gz") as package:
        recipe = recipe_bytes(package, version)
        everything: dict[str, str] = {}
        toml = None
        for member in package.getmembers():
            if member.isdir():
                continue
            if member.issym() or member.islnk():
                everything[member.name] = "link:" + member.linkname
                continue
            if not member.isreg():
                raise ValueError("Package members must be regular files, links or directories")
            data = package.extractfile(member).read()
            if member.name == "package/package.json":
                manifest = json.loads(data)
                manifest.pop("version", None)
                data = json.dumps(manifest, sort_keys=True).encode()
            if member.name == "package/mise/standards.toml":
                toml = tomllib.loads(data.decode("utf-8"))
            everything[member.name] = _digest(data)
    if toml is None:
        raise ValueError("Package mise/standards.toml is missing")
    relevant = {name: digest for name, digest in everything.items()
                if name.startswith("package/mise/") or name == "package/package.json"}
    relevant[RECIPE] = _digest(recipe)
    envs = toml.get("env", [])
    envs = envs if isinstance(envs, list) else [envs]
    serialized = json.dumps({key: value for key, value in toml.items() if key != "tasks"}, default=str)
    fallback = bool(
        toml.get("task_config")
        or any(key != "path" for env in envs if isinstance(env, dict) for key in (env.get("_") or {}))
        or any(key not in ("enter", "leave", "cd") for key in toml.get("hooks", {}))
        or any(isinstance(value, dict) and "postinstall" in value for value in toml.get("tools", {}).values())
        or "exec(" in serialized
        or "read_file(" in serialized
    )
    return {"relevant": relevant, "all": everything, "tasks": sorted(toml.get("tasks", {})), "fallback": fallback}


if __name__ == "__main__":
    try:
        if sys.argv[1] == "inputs":
            print(json.dumps(image_inputs(Path(sys.argv[2]), sys.argv[3]), sort_keys=True))
        else:
            extract_recipe(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, tarfile.TarError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from None
