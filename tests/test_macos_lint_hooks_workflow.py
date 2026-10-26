from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_ruby_workflows as ruby_contract
from test_ruby_workflows import (
    LINT_TEXT,
    ROOT,
    extract_run_block,
    extract_step,
)

PREPARE_STEP = "Prepare macOS hook tools"
RUBY_STEP = "Prepare macOS Ruby dependencies and tool cache"


class MacOSRoutingTests(unittest.TestCase):
    def test_route_uses_org_map_with_hosted_fallback(self) -> None:
        self.assertIn(
            "MACOS_RUNNER: ${{ toJSON(fromJSON(vars.RUNNERS || '{}').macos "
            "|| fromJSON('[\"macos-26\"]')) }}",
            LINT_TEXT,
        )
        self.assertNotIn("macos-15-intel", LINT_TEXT)
        self.assertIn("timeout-minutes: 30", LINT_TEXT.split("  lint-hooks:", 1)[1])

    def test_swift_and_homebrew_keep_multiline_self_hosted_route(self) -> None:
        router = ruby_contract.LintRubyRoutingTests()
        for facet in ("has_swift", "has_homebrew_formulae"):
            with self.subTest(facet=facet):
                output = router.route(
                    f"{facet}: true\nhas_ruby: true\nlint_hooks_workload_class: heavy\n",
                    MACOS_RUNNER='[\n  "self-hosted",\n  "macos-tart"\n]',
                )
                self.assertEqual(output["runner"], '["self-hosted","macos-tart"]')
                self.assertEqual(output["has_ruby"], "true")

    def test_no_bare_hosted_macos_route_in_other_workflows(self) -> None:
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            with self.subTest(workflow=path.name):
                self.assertNotRegex(
                    path.read_text(),
                    r"(?m)^\s*(?:runs-on|[A-Z_]*RUNNER):\s*['\"]?(?:\[\s*['\"])?macos-",
                )

    def test_platform_setup_precedes_ruby_and_mise(self) -> None:
        self.assertIn("if: runner.os == 'macOS'", extract_step(LINT_TEXT, PREPARE_STEP))
        ruby = extract_step(LINT_TEXT, RUBY_STEP)
        self.assertIn(
            "if: runner.os == 'macOS' && needs.detect.outputs.has_ruby == 'true'", ruby
        )
        self.assertLess(LINT_TEXT.index(PREPARE_STEP), LINT_TEXT.index(RUBY_STEP))
        self.assertLess(LINT_TEXT.index(RUBY_STEP), LINT_TEXT.index("uses: ruby/setup-ruby@"))
        self.assertLess(LINT_TEXT.index(PREPARE_STEP), LINT_TEXT.index("uses: jdx/mise-action@"))
        self.assertIn("steps.uv-python.outputs.version", LINT_TEXT)
        for hook in (
            "Run lefthook pre-commit across all files",
            "Run lefthook pre-commit on changed fork files",
            "Run the full knowledgebase check",
        ):
            self.assertLess(LINT_TEXT.index(PREPARE_STEP), LINT_TEXT.index(hook))
        prepare = extract_step(LINT_TEXT, PREPARE_STEP)
        self.assertNotIn("continue-on-error:", prepare)
        self.assertNotIn("always()", prepare)


class MacOSSetupTests(unittest.TestCase):
    def run_prepare(
        self, *, arm: bool = True, brew_present: bool = True,
        answers: str | None = "has_swift: true\n", xcode_exit: int = 0,
        update_exit: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            if answers is not None:
                (repo / ".copier-answers.yml").write_text(answers)
            path_file = repo / "github-path"
            path_file.touch()
            log = repo / "log"
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            prefix = repo / ("arm" if arm else "intel")
            (prefix / "bin").mkdir(parents=True)
            if brew_present:
                brew = prefix / "bin/brew"
                brew.write_text(
                    '#!/bin/bash\n'
                    'case "$1" in\n'
                    f'  shellenv) printf \'export PATH="{prefix}/bin:$PATH"\\n\' ;;\n'
                    f'  --prefix) printf \'{prefix}\\n\' ;;\n'
                    '  update)\n'
                    '    echo "brew $* tag=${HOMEBREW_UPDATE_TO_TAG:-}" >> "$LOG"\n'
                    f'    [ {update_exit} -eq 0 ] || exit {update_exit}\n'
                    '    touch "$UPDATED" ;;\n'
                    '  --version)\n'
                    '    echo "brew --version" >> "$LOG"\n'
                    '    if [ -e "$UPDATED" ]; then echo "Homebrew 7.0.0"; '
                    'else echo "Homebrew 6.0.22"; fi ;;\n'
                    '  *) exit 99 ;;\n'
                    'esac\n'
                )
                brew.chmod(0o755)
            for command, status in (("xcodebuild", xcode_exit), ("xcrun", 0)):
                stub = bin_dir / command
                stub.write_text(
                    f'#!/bin/bash\nprintf "%s\\n" "{command} $*" >> "$LOG"\nexit {status}\n'
                )
                stub.chmod(0o755)
            script = extract_run_block(LINT_TEXT, PREPARE_STEP)
            script = script.replace("/opt/homebrew/bin/brew", str(repo / "arm/bin/brew"))
            script = script.replace("/usr/local/bin/brew", str(repo / "intel/bin/brew"))
            result = subprocess.run(
                ["/bin/bash", "-c", script], cwd=repo, capture_output=True, text=True,
                env={"PATH": f"{bin_dir}:/usr/bin:/bin", "GITHUB_PATH": str(path_file),
                     "LOG": str(log), "UPDATED": str(repo / "updated"),
                     "HOMEBREW_UPDATE_TO_TAG": "0"},
            )
            return result, path_file.read_text(), log.read_text() if log.exists() else ""

    def test_non_login_arm_and_intel_shells_export_brew_paths(self) -> None:
        for arm in (True, False):
            with self.subTest(arm=arm):
                result, paths, log = self.run_prepare(arm=arm)
                self.assertEqual(result.returncode, 0, result.stderr)
                suffix = "arm" if arm else "intel"
                self.assertTrue(paths.splitlines()[0].endswith(f"/{suffix}/bin"))
                self.assertTrue(paths.splitlines()[1].endswith(f"/{suffix}/sbin"))
                self.assertIn("xcodebuild -version", log)
                self.assertIn("xcrun --find swift", log)

    def test_formula_only_does_not_require_xcode(self) -> None:
        result, _, log = self.run_prepare(answers="has_homebrew_formulae: true\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.splitlines(), ["brew update tag=1", "brew --version"])

    def test_formula_update_uses_stable_tag_before_reporting_version(self) -> None:
        for arm in (True, False):
            for extra_facet in ("", "has_swift: true\n", "has_ruby: true\n"):
                with self.subTest(arm=arm, extra_facet=extra_facet):
                    result, paths, log = self.run_prepare(
                        arm=arm, answers="has_homebrew_formulae: true\n" + extra_facet,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        log.splitlines()[:2], ["brew update tag=1", "brew --version"]
                    )
                    self.assertIn("Homebrew 7.0.0", result.stdout)
                    self.assertEqual(len(paths.splitlines()), 2)

    def test_only_declared_literal_true_updates_homebrew(self) -> None:
        for answers in (
            None, "", "has_swift: true\n", "archetype: swift\n", "has_ruby: true\n",
            "has_homebrew_formulae: false\n", "has_homebrew_formulae: 'true'\n",
            "has_homebrew_formulae: trueish\n", "# has_homebrew_formulae: true\n",
        ):
            with self.subTest(answers=answers):
                result, _, log = self.run_prepare(answers=answers)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("brew update", log)
                self.assertIn("Homebrew 6.0.22", result.stdout)
        result, _, log = self.run_prepare(answers="has_homebrew_formulae:\ttrue  \n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("brew update tag=1", log)

    def test_failed_formula_update_stops_preparation(self) -> None:
        result, _, log = self.run_prepare(
            answers="has_homebrew_formulae: true\nhas_swift: true\n", update_exit=17,
        )
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(log.splitlines(), ["brew update tag=1"])
        self.assertNotIn("Homebrew", result.stdout)
        result, _, log = self.run_prepare(
            answers="has_homebrew_formulae: true\n", brew_present=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Homebrew is required", result.stderr)
        self.assertEqual(log, "")

    def test_legacy_swift_facet_checks_xcode(self) -> None:
        result, _, log = self.run_prepare(answers="archetype: swift\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("xcodebuild -version", log)

    def test_missing_brew_and_missing_xcode_fail_closed(self) -> None:
        result, _, _ = self.run_prepare(brew_present=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Homebrew is required", result.stderr)
        result, _, log = self.run_prepare(xcode_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("xcrun", log)

    def test_ruby_prebuild_prerequisites_and_writable_cache(self) -> None:
        for writable in (False, True):
            with self.subTest(writable=writable), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                cache = repo / "hostedtoolcache"
                if writable:
                    cache.mkdir()
                bin_dir = repo / "bin"
                bin_dir.mkdir()
                log = repo / "log"
                stubs = {
                    "brew": '#!/bin/bash\necho "brew $*" >> "$LOG"\n',
                    "sudo": '#!/bin/bash\necho "sudo $*" >> "$LOG"\n'
                            'if [ "$1" = mkdir ]; then "$@"; fi\n',
                }
                for name, script in stubs.items():
                    stub = bin_dir / name
                    stub.write_text(script)
                    stub.chmod(0o755)
                script = extract_run_block(LINT_TEXT, RUBY_STEP).replace(
                    "/Users/runner/hostedtoolcache", str(cache)
                )
                result = subprocess.run(
                    ["/bin/bash", "-c", script], cwd=repo, capture_output=True, text=True,
                    env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "LOG": str(log)},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("brew install openssl@3 libyaml gmp", log.read_text())
                self.assertEqual("sudo mkdir" in log.read_text(), not writable)
                self.assertEqual("sudo chown" in log.read_text(), not writable)
                self.assertTrue(cache.is_dir())


if __name__ == "__main__":
    unittest.main()
