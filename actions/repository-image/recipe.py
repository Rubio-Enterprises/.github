from __future__ import annotations

import sys
import tarfile
from pathlib import Path


def extract_recipe(archive: Path, destination: Path, version: str) -> None:
    with tarfile.open(archive, "r:gz") as package:
        members = [member for member in package if member.name == "package/image/Dockerfile"]
        if not members:
            raise ValueError(
                f"Standards Release {version} has no image/Dockerfile; repository images require "
                "2.1.0 or newer. Remove the older STANDARDS_VERSION Release Pin or raise it to 2.1.0+."
            )
        if len(members) != 1 or not members[0].isreg() or not 0 < members[0].size <= 256 * 1024:
            raise ValueError("Package image/Dockerfile must be one nonempty regular file, at most 256 KiB")
        recipe = package.extractfile(members[0]).read()
        recipe.decode("utf-8")
    with destination.open("xb") as output:
        output.write(recipe)


if __name__ == "__main__":
    try:
        extract_recipe(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1) from None
