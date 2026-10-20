"""Contract tests for pnpm metadata-cache resolution and default-branch seeding."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    name: (ROOT / ".github" / "workflows" / f"{name}.yml").read_text()
    for name in ("lint-hooks", "typecheck-ts")
}
KEY = "pnpm-cache-dir-v1-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('pnpm-lock.yaml') }}"


def step(text: str, name: str) -> str:
    return text.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]


def resolver(text: str) -> str:
    body = step(text, "Locate the pnpm cacheDir").split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in body.splitlines()) + "\n"


class CacheDirResolutionTests(unittest.TestCase):
    def resolve(
        self,
        value: str,
        *,
        runner_os: str = "Linux",
        xdg: bool = False,
        exit_code: int = 0,
    ) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            binary = root / "pnpm"
            binary.write_text(
                '#!/bin/sh\n[ "$*" = "config get cacheDir" ] || exit 99\n'
                'printf "%s\\n" "$CONFIG_VALUE"\nexit "$CONFIG_EXIT"\n'
            )
            binary.chmod(0o755)
            output = root / "output"
            env = {
                "PATH": f"{root}:{os.environ['PATH']}",
                "HOME": str(root / "home"),
                "RUNNER_OS": runner_os,
                "GITHUB_OUTPUT": str(output),
                "CONFIG_VALUE": value,
                "CONFIG_EXIT": str(exit_code),
            }
            if xdg:
                env["XDG_CACHE_HOME"] = str(root / "xdg cache")
            result = subprocess.run(
                ["bash", "-c", resolver(WORKFLOWS["typecheck-ts"])],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            contents = output.read_text().replace(str(root), "$ROOT")
            return dict(line.split("=", 1) for line in contents.splitlines())

    def test_both_gates_execute_the_same_resolver(self) -> None:
        self.assertEqual(resolver(WORKFLOWS["lint-hooks"]), resolver(WORKFLOWS["typecheck-ts"]))

    def test_configured_absolute_path(self) -> None:
        self.assertEqual(self.resolve("/custom/cache dir"), {"ok": "true", "path": "/custom/cache dir"})

    def test_relative_path_is_resolved_against_workspace(self) -> None:
        self.assertEqual(self.resolve(".pnpm-cache")["path"], "$ROOT/.pnpm-cache")

    def test_tilde_path_is_expanded(self) -> None:
        self.assertEqual(self.resolve("~/custom cache")["path"], "$ROOT/home/custom cache")

    def test_linux_default(self) -> None:
        for missing in ("", "undefined", "null"):
            with self.subTest(missing=missing):
                self.assertEqual(self.resolve(missing), {"ok": "true", "path": "$ROOT/home/.cache/pnpm"})

    def test_macos_default(self) -> None:
        self.assertEqual(self.resolve("undefined", runner_os="macOS")["path"], "$ROOT/home/Library/Caches/pnpm")

    def test_xdg_default_precedes_platform_default(self) -> None:
        for runner_os in ("Linux", "macOS"):
            with self.subTest(runner_os=runner_os):
                self.assertEqual(self.resolve("undefined", runner_os=runner_os, xdg=True)["path"], "$ROOT/xdg cache/pnpm")

    def test_configured_path_precedes_xdg_default(self) -> None:
        self.assertEqual(self.resolve("/custom", xdg=True)["path"], "/custom")

    def test_command_failure_skips_cache_without_failing_install(self) -> None:
        self.assertEqual(self.resolve("/partial", exit_code=1), {"ok": "false"})

    def test_multiline_and_carriage_return_paths_cannot_inject_outputs(self) -> None:
        for value in ("/cache\nok=true", "/cache\rpath=bad"):
            with self.subTest(value=value):
                self.assertEqual(self.resolve(value), {"ok": "false"})

    def test_unsupported_default_skips_cache(self) -> None:
        self.assertEqual(self.resolve("undefined", runner_os="unknown"), {"ok": "false"})


class CacheLifecycleTests(unittest.TestCase):
    def test_gates_share_os_arch_lockfile_key_and_read_only_restore(self) -> None:
        for name, text in WORKFLOWS.items():
            with self.subTest(workflow=name):
                restore = step(text, "Restore the pnpm cacheDir")
                self.assertIn("if: steps.pnpm-cache-dir.outputs.ok == 'true'", restore)
                self.assertIn("uses: actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9", restore)
                self.assertIn("path: ${{ steps.pnpm-cache-dir.outputs.path }}", restore)
                self.assertIn(f"key: {KEY}", restore)
                self.assertIn("restore-keys: |\n            pnpm-cache-dir-v1-${{ runner.os }}-${{ runner.arch }}-", restore)
                self.assertLess(text.index("- name: Restore the pnpm cacheDir"), text.index("- name: Install JS deps"))
                locate = step(text, "Locate the pnpm cacheDir")
                self.assertIn("hashFiles('pnpm-lock.yaml') != ''", locate)
                self.assertLess(text.index("uses: pnpm/action-setup@"), text.index("- name: Locate the pnpm cacheDir"))

    def test_typecheck_seeds_metadata_after_install_but_before_typecheck(self) -> None:
        text = WORKFLOWS["typecheck-ts"]
        seed = step(text, "Seed the pnpm cacheDir cache")
        self.assertIn("inputs.seed-caches", seed)
        self.assertIn("steps.detect.outputs.has_typecheck == 'true'", seed)
        self.assertIn("steps.pnpm-cache-dir.outputs.ok == 'true'", seed)
        self.assertIn("uses: actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9", seed)
        self.assertIn(f"key: {KEY}", seed)
        self.assertIn("path: ${{ steps.pnpm-cache-dir.outputs.path }}", seed)
        self.assertLess(text.index("- name: Install JS deps"), text.index("- name: Seed the pnpm cacheDir cache"))
        self.assertLess(text.index("- name: Seed the pnpm cacheDir cache"), text.index("- name: Type-check (mise run typecheck)"))

    def test_typecheck_resolution_is_gated_on_task_presence(self) -> None:
        self.assertIn("steps.detect.outputs.has_typecheck == 'true'", step(WORKFLOWS["typecheck-ts"], "Locate the pnpm cacheDir"))

    def test_lint_hooks_never_writes_metadata_cache(self) -> None:
        self.assertNotIn("uses: actions/cache/save@", WORKFLOWS["lint-hooks"])
        self.assertNotIn("uses: actions/cache@", WORKFLOWS["lint-hooks"])
        self.assertNotIn("Seed the pnpm cacheDir cache", WORKFLOWS["lint-hooks"])


if __name__ == "__main__":
    unittest.main()
