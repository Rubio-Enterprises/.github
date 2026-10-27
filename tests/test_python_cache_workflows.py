from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_native_config_workflows import NATIVE_WORKFLOWS
from test_ruby_workflows import ROOT, extract_run_block, extract_step

STEP = "Fingerprint the uv-resolved python for the mise cache key"
FIXTURE = ROOT / "tests/fixtures/native-python-cache"


class PythonCacheFingerprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.uv = shutil.which("uv")
        if cls.uv is None:
            raise unittest.SkipTest(
                "uv is required for the offline interpreter fixture"
            )
        cls.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "CONDA_PREFIX"}
        }
        cls.env["PATH"] = f"{Path(cls.uv).resolve().parent}{os.pathsep}/usr/bin:/bin"
        lookup_env = {**cls.env, "UV_OFFLINE": "true", "UV_NO_CONFIG": "true"}
        if "UV_PYTHON_INSTALL_DIR" in os.environ:
            lookup_env["UV_PYTHON_INSTALL_DIR"] = os.environ["UV_PYTHON_INSTALL_DIR"]
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    cls.uv,
                    "python",
                    "find",
                    "--no-project",
                    "--managed-python",
                    "--system",
                ],
                cwd=tmp,
                env=lookup_env,
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            raise unittest.SkipTest("an installed uv-managed Python >=3.14 is required")
        cls.python = Path(result.stdout.strip()).resolve()
        cls.version = subprocess.check_output(
            [
                str(cls.python),
                "-c",
                "import platform; print(platform.python_version())",
            ],
            text=True,
        ).strip()
        if tuple(map(int, cls.version.split(".")[:2])) < (3, 14):
            raise unittest.SkipTest("an installed uv-managed Python >=3.14 is required")
        state = tempfile.TemporaryDirectory()
        cls.addClassCleanup(state.cleanup)
        cls.managed = Path(state.name) / "managed"
        cls.managed.mkdir()
        installation = cls.python.parent.parent
        shutil.copytree(installation, cls.managed / installation.name, symlinks=True)

    def run_fingerprint(
        self,
        name: str,
        model: str = "native",
        *,
        requires_python: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        text = NATIVE_WORKFLOWS[name]
        step = extract_step(text, STEP)
        env = self.env.copy()
        self.assertIn('UV_NO_PROJECT: "true"', step)
        env["UV_NO_PROJECT"] = "true"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            consumer = root / "consumer"
            shutil.copytree(FIXTURE, consumer)
            (consumer / ".copier-answers.yml").write_text(
                f"config_model: {model}\nhas_python: true\n"
            )
            if requires_python is not None:
                manifest = consumer / "pyproject.toml"
                manifest.write_text(
                    manifest.read_text().replace(">=3.10,<3.14", requires_python)
                )
            original_pin = (consumer / ".mise.toml").read_bytes()
            original_manifest = (consumer / "pyproject.toml").read_bytes()
            output = root / "output"
            env.update(
                HOME=str(root / "home"),
                XDG_CONFIG_HOME=str(root / "config"),
                UV_PYTHON_INSTALL_DIR=str(self.managed),
                UV_PYTHON_BIN_DIR=str(root / "bin"),
                UV_CACHE_DIR=str(root / "cache"),
                UV_OFFLINE="true",
                UV_PYTHON_DOWNLOADS="manual",
                GITHUB_OUTPUT=str(output),
            )
            result = subprocess.run(
                ["bash", "-c", extract_run_block(text, STEP)],
                cwd=consumer,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual((consumer / ".mise.toml").read_bytes(), original_pin)
            self.assertEqual(
                (consumer / "pyproject.toml").read_bytes(), original_manifest
            )
            self.assertFalse((consumer / ".python-version").exists())
            self.assertFalse((consumer / ".venv").exists())
            return result, output.read_text() if output.exists() else ""

    def test_native_seed_ignores_the_consumer_python_upper_bound(self) -> None:
        for name in NATIVE_WORKFLOWS:
            with self.subTest(workflow=name):
                result, output = self.run_fingerprint(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f"version={self.version}\n")


    def test_rendered_seed_still_resolves_a_compatible_interpreter(self) -> None:
        for name in NATIVE_WORKFLOWS:
            with self.subTest(workflow=name):
                result, output = self.run_fingerprint(
                    name,
                    "rendered",
                    requires_python=">=3.10,<3.15",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f"version={self.version}\n")
