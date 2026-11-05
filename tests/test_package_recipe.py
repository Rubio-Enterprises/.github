from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "actions/dev-container-image"
FROZEN_RECIPE = ROOT / "tests/fixtures/frozen-repository-image-v2.Dockerfile" 


class PackageRecipe(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "standards.tgz"
        self.destination = self.root / "Dockerfile"

    def package(self, members):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content, kind in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind == tarfile.REGTYPE:
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
                else:
                    member.linkname = "../../outside"
                    archive.addfile(member)

    def extract(self, version="2.1.0"):
        return subprocess.run([sys.executable, str(ASSETS / "recipe.py"), str(self.archive),
                               str(self.destination), version], capture_output=True, text=True)

    def test_extracts_only_the_exact_recipe_without_materializing_other_members(self):
        recipe = b"FROM scratch AS ci\n"
        self.package([("package/image/Dockerfile", recipe, tarfile.REGTYPE),
                      ("../outside", b"private package content", tarfile.REGTYPE),
                      ("package/config/private", b"private package content", tarfile.REGTYPE)])
        result = self.extract()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.destination.read_bytes(), recipe)
        self.assertEqual({path.name for path in self.root.iterdir()}, {"standards.tgz", "Dockerfile"})
        self.assertFalse((self.root.parent / "outside").exists())
        self.assertNotIn("private package content", result.stdout + result.stderr)

    def test_missing_recipe_names_the_release_pin_remedy_without_fallback(self):
        self.package([("package/config/private", b"private", tarfile.REGTYPE)])
        result = self.extract("2.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Standards Release 2.0.0 has no image/Dockerfile", result.stderr)
        self.assertIn("STANDARDS_VERSION Release Pin", result.stderr)
        self.assertIn("2.1.0+", result.stderr)
        self.assertFalse(self.destination.exists())

    def test_rejects_duplicate_links_empty_oversized_and_invalid_recipes(self):
        valid = ("package/image/Dockerfile", b"FROM scratch AS ci\n", tarfile.REGTYPE)
        cases = [[valid, valid]] + [[("package/image/Dockerfile", b"", kind)] for kind in
                                    (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE)]
        cases += [[("package/image/Dockerfile", value, tarfile.REGTYPE)] for value in
                  (b"", b"x" * (256 * 1024 + 1), b"\xff")]
        for members in cases:
            with self.subTest(members=[(name, len(data), kind) for name, data, kind in members]):
                self.package(members)
                result = self.extract()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.destination.exists())
        self.archive.write_bytes(b"invalid archive")
        self.assertNotEqual(self.extract().returncode, 0)

    def test_never_overwrites_an_existing_recipe_or_follows_its_symlink(self):
        self.package([("package/image/Dockerfile", b"FROM scratch AS ci\n", tarfile.REGTYPE)])
        protected = self.root / "protected"
        protected.write_text("keep")
        self.destination.symlink_to(protected)
        self.assertNotEqual(self.extract().returncode, 0)
        self.assertEqual(protected.read_text(), "keep")

    def test_production_has_no_frozen_fixture_or_v2_dockerfile_fallback(self):
        self.assertFalse((ASSETS / "Dockerfile.v2").exists())
        for path in ASSETS.iterdir():
            if path.is_file():
                self.assertNotIn("frozen-repository-image", path.read_text())
                self.assertNotIn("Dockerfile.v2", path.read_text())

    @unittest.skipUnless(os.environ.get("STANDARDS_IMAGE_PACKAGE"), "set STANDARDS_IMAGE_PACKAGE to the verified release tarball")
    def test_released_ci_recipe_matches_the_frozen_environment_and_setup_contract(self):
        archive = Path(os.environ["STANDARDS_IMAGE_PACKAGE"])
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
        self.assertEqual(integrity, os.environ["STANDARDS_IMAGE_INTEGRITY"])
        with tarfile.open(archive, "r:gz") as package:
            released = package.extractfile("package/image/Dockerfile").read().decode()
        frozen = FROZEN_RECIPE.read_text()

        def instructions(text):
            return [re.sub(r"\s+", " ", line.strip()) for line in text.replace("\\\n", " ").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]

        released_lines = instructions(released)
        environment = released_lines[released_lines.index("FROM ${BASE} AS environment") + 1:
                                     released_lines.index("FROM environment AS local")]
        ci = released_lines[released_lines.index("FROM environment AS ci") + 1:]
        actual = environment + ci
        expected = instructions(frozen)
        self.assertEqual([line for line in actual if line.startswith("RUN ")],
                         [line for line in expected if line.startswith("RUN ")])

        def env(lines):
            result = {}
            for line in lines:
                if line.startswith("ENV "):
                    result.update(item.split("=", 1) for item in shlex.split(line[4:]))
            return result

        self.assertEqual(env(actual), env(expected))
        for key in ("USER ", "WORKDIR "):
            self.assertEqual([line for line in actual if line.startswith(key)][-1],
                             [line for line in expected if line.startswith(key)][-1])
        self.assertIn("ARG REPO", ci)
