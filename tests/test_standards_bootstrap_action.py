"""Contracts for the `standards-bootstrap` composite action (standards ADR-0033).

The checkout must exist before mise resolves consumer includes, but its channel
comes from mise's stored TOML rather than the Copier answer. The action still
runs native bootstrap and independent readiness checks. The shell steps run
under the default bash and macOS's bash 3.2.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import ROOT, extract_run_block, extract_step

ACTION = (ROOT / "actions" / "standards-bootstrap" / "action.yml").read_text(encoding="utf-8")

MODEL = "Read the config model"
PREREQUISITES = "Check the native prerequisites"
INSTALL_MISE = "Install mise without loading repository config"
DECLARATION = "Read the Shared Config Checkout declaration"
MINT = "Mint the standards read token"
CHECKOUT = "Check out the Shared Config Checkout"
ROUTE = "Route the standards clone through the token"
BOOTSTRAP = "Install mise and bootstrap the Shared Config Checkout"
READY = "Require the Shared Config Checkout"
UNROUTE = "Remove the standards read route"
STEPS = [MODEL, PREREQUISITES, INSTALL_MISE, DECLARATION, MINT, CHECKOUT, ROUTE, BOOTSTRAP, READY, UNROUTE]
NATIVE = "steps.model.outputs.config_model == 'native'"
OWNER = "Rubio-Enterprises"
ROUTE_KEY = f"url.https://x-access-token:ghs_secret@github.com/{OWNER}/standards.insteadof"
BASHES = ["bash"] + (["/bin/bash"] if Path("/bin/bash").exists() else [])


def action_inputs() -> dict[str, str]:
    """Map each declared input to its default (empty when none)."""
    block = ACTION.split("\ninputs:\n", 1)[1].split("\noutputs:\n", 1)[0]
    inputs: dict[str, str] = {}
    for match in re.finditer(r"(?m)^  ([a-z-]+):\n((?:    .*\n|\s*\n)*)", block + "\n"):
        default = re.search(r"(?m)^    default: (.*)$", match.group(2))
        inputs[match.group(1)] = default.group(1).strip('"') if default else ""
    return inputs


def condition(name: str) -> str:
    for line in extract_step(ACTION, name).splitlines():
        if line.strip().startswith("if:"):
            return line.strip()[3:].strip()
    return ""


def run(name: str, cwd: Path, *, shell: str = "bash", **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shell, "-c", extract_run_block(ACTION, name)], cwd=cwd, text=True,
        capture_output=True, check=False, env={**os.environ, **env},
    )


def outputs(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


class InterfaceTests(unittest.TestCase):
    def test_inputs_and_defaults(self) -> None:
        self.assertEqual(action_inputs(), {
            "app-id": "", "private-key": "", "config-model": "", "mise-version": "",
            "cache-key-prefix": "mise-v1", "bootstrap-parts": "repos,tools", "keep-route": "false",
        })

    def test_config_model_output_comes_from_the_model_step(self) -> None:
        self.assertIn("value: ${{ steps.model.outputs.config_model }}", ACTION)
        self.assertIn("using: composite", ACTION)

    def test_steps_run_in_order(self) -> None:
        positions = [ACTION.index(f"- name: {step}\n") for step in STEPS]
        self.assertEqual(positions, sorted(positions))

    def test_nested_actions_are_pinned_by_digest(self) -> None:
        uses = re.findall(r"(?m)^\s+uses: (\S+)(.*)$", ACTION)
        self.assertEqual(len(uses), 4)
        for ref, comment in uses:
            with self.subTest(ref=ref):
                self.assertRegex(ref, r"^[\w-]+/[\w.-]+@[0-9a-f]{40}$")
                self.assertRegex(comment, r"^ # v\d")


class ConfigModelTests(unittest.TestCase):
    def test_input_wins_and_an_empty_input_reads_the_answers(self) -> None:
        for given, answers, expected in (
            ("", None, "rendered"),
            ("", "has_python: true\n", "rendered"),
            ("", "config_model: rendered\n", "rendered"),
            ("", "config_model: native\n", "native"),
            ("", 'config_model: "native"\n', "native"),
            ("", "# config_model: native\n", "rendered"),
            ("", "config_model: Native\n", None),
            ("native", None, "native"),
            ("rendered", "config_model: native\n", "rendered"),
            ("Native", None, None),
        ):
            for shell in BASHES:
                with self.subTest(given=given, answers=answers, shell=shell), \
                        tempfile.TemporaryDirectory() as tmp:
                    repo = Path(tmp)
                    if answers is not None:
                        (repo / ".copier-answers.yml").write_text(answers)
                    result = run(MODEL, repo, shell=shell, GIVEN_MODEL=given,
                                 GITHUB_OUTPUT=str(repo / "out"))
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("unsupported config_model", result.stderr)
                        self.assertNotIn("config_model", outputs(repo / "out"))
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(outputs(repo / "out")["config_model"], expected)

    def test_rendered_repositories_run_nothing_else(self) -> None:
        self.assertEqual(condition(MODEL), "")
        for step in STEPS[1:-1]:
            with self.subTest(step=step):
                self.assertEqual(condition(step), NATIVE)
        self.assertEqual(
            condition(UNROUTE), f"always() && {NATIVE} && inputs.keep-route != 'true'"
        )


class PrerequisiteTests(unittest.TestCase):
    def check(self, *, app_id: str = "1", key: str = "k", version: str = "",
              installed: str | None = None, shell: str = "bash", parts: str = "repos,tools"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "mise-data"
            if installed is not None:
                binary = data / "bin" / "mise"
                binary.parent.mkdir(parents=True)
                report = json.dumps({"version": f"{installed} linux-x64 (2026-10-01)"})
                binary.write_text(f"#!/bin/sh\n[ \"$*\" = 'version --json' ] || exit 2\necho '{report}'\n")
                binary.chmod(0o755)
            result = run(PREREQUISITES, root, shell=shell, APP_ID=app_id, APP_PRIVATE_KEY=key,
                         MISE_VERSION=version, MISE_DATA_DIR=str(data), BOOTSTRAP_PARTS=parts,
                         GITHUB_OUTPUT=str(root / "out"))
            return result, outputs(root / "out")

    def test_bootstrap_parts_accepts_only_the_two_supported_modes(self) -> None:
        for parts in ("repos", "repos,tools", "", "tools", "repos --skip tools", "repos; echo injected"):
            for shell in BASHES:
                with self.subTest(parts=parts, shell=shell):
                    result, out = self.check(parts=parts, shell=shell)
                    valid = parts in ("repos", "repos,tools")
                    self.assertEqual(result.returncode == 0, valid, result.stderr)
                    if not valid:
                        self.assertIn("bootstrap-parts must be repos or repos,tools", result.stderr)
                        self.assertEqual(out, {})

    def test_missing_credentials_fail_with_a_clear_error(self) -> None:
        for app_id, key in (("", "k"), ("1", ""), ("", "")):
            with self.subTest(app_id=app_id, key=key):
                result, out = self.check(app_id=app_id, key=key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("a reusable workflow's caller must forward them", result.stderr)
                self.assertEqual(out, {})

    def test_mise_version_must_meet_the_native_floor(self) -> None:
        for version, installed, expected in (
            ("2026.10.19", None, "2026.10.19"),
            ("2026.11.3", "2026.9.11", "2026.11.3"),
            ("2027.1.0", None, "2027.1.0"),
            ("", None, "2026.10.19"),
            ("", "2026.11.3", "2026.11.3"),
            ("", "2026.10.19", "2026.10.19"),
            ("2026.10.18", None, None),
            ("2026.9.30", None, None),
            ("2025.12.99", None, None),
            ("", "2026.10.5", None),
            ("latest", None, None),
            ("v2026.10.19", None, None),
        ):
            for shell in BASHES:
                with self.subTest(version=version, installed=installed, shell=shell):
                    result, out = self.check(version=version, installed=installed, shell=shell)
                    if expected is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("::error::", result.stderr)
                        self.assertEqual(out, {})
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(out, {"mise_version": expected})


class CheckoutDeclarationTests(unittest.TestCase):
    def declaration(self, config: str | None, *, present: bool = False, shell: str = "bash"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if config is not None:
                (root / ".mise.toml").write_text(config)
            (root / ".copier-answers.yml").write_text("standards_channel: stable\n")
            if present:
                (root / ".standards-config").mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "mise").write_text(
                f"#!{sys.executable}\n"
                "import json, pathlib, sys, tomllib\n"
                "assert sys.argv[1:4] == ['config', 'get', '--file'], sys.argv\n"
                "with pathlib.Path('calls').open('a') as out:\n"
                "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "assert len(sys.argv) == 6 and sys.argv[4] == '.mise.toml'\n"
                "config = tomllib.loads(pathlib.Path(sys.argv[4]).read_text())\n"
                "key = sys.argv[5].rsplit('.', 1)[1]\n"
                "print(config['bootstrap']['repos']['.standards-config'][key])\n"
            )
            (bin_dir / "mise").chmod(0o755)
            result = run(DECLARATION, root, shell=shell, PATH=f"{bin_dir}:{os.environ['PATH']}",
                         GITHUB_OUTPUT=str(root / "out"))
            calls = [json.loads(line) for line in (root / "calls").read_text().splitlines()]
            return result, outputs(root / "out"), calls

    def test_cold_and_present_checkouts_read_the_authoritative_declaration(self) -> None:
        for ref in ("stable", "canary"):
            config = (
                'include = ["git::http://127.0.0.1:9/unreachable.git//base.toml?ref=stable"]\n'
                '[bootstrap.repos]\n'
                f"'.standards-config' = {{url = 'https://github.com/{OWNER}/standards.git', ref = '{ref}'}}\n"
            )
            for present in (False, True):
                for shell in BASHES:
                    with self.subTest(ref=ref, present=present, shell=shell):
                        result, out, calls = self.declaration(config, present=present, shell=shell)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(out, {"ref": ref})
                        self.assertEqual({call[-1] for call in calls}, {
                            'bootstrap.repos.".standards-config".url',
                            'bootstrap.repos.".standards-config".ref',
                        })

    def test_table_form_and_git_suffix_optional(self) -> None:
        config = (
            '[bootstrap.repos.".standards-config"]\n'
            f'url = "https://github.com/{OWNER}/standards"\nref = "canary"\n'
        )
        result, out, _ = self.declaration(config)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out, {"ref": "canary"})

    def test_missing_malformed_or_unexpected_declarations_fail_closed(self) -> None:
        for config in (
            None, "", "not valid TOML", '[bootstrap.repos]\n".standards-config" = {}\n',
            f'[bootstrap.repos]\n".standards-config" = {{url="https://github.com/{OWNER}/standards.git"}}\n',
            '[bootstrap.repos]\n".standards-config" = {url="https://example.invalid/standards.git",ref="stable"}\n',
            f'[bootstrap.repos]\n".standards-config" = {{url="https://github.com/{OWNER}/standards.git",ref="main"}}\n',
            f'[bootstrap.repos]\n".standards-config" = {{url="https://github.com/{OWNER}/standards.git",ref="stable\\nother=output"}}\n',
        ):
            with self.subTest(config=config):
                result, out, _ = self.declaration(config)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(out, {})


class TokenAndBootstrapTests(unittest.TestCase):
    def test_mise_is_installed_without_loading_the_consumer_config(self) -> None:
        step = extract_step(ACTION, INSTALL_MISE)
        self.assertIn("uses: jdx/mise-action@", step)
        for line in ("install: false", "cache: false", "env: false",
                     "version: ${{ steps.prerequisites.outputs.mise_version }}"):
            self.assertIn(line, step)
        self.assertNotIn("bootstrap: true", step)

    def test_checkout_uses_the_declared_channel_and_full_history_before_bootstrap(self) -> None:
        step = extract_step(ACTION, CHECKOUT)
        for line in ("uses: actions/checkout@", "repository: Rubio-Enterprises/standards",
                     "ref: ${{ steps.declaration.outputs.ref }}", "path: .standards-config",
                     "fetch-depth: 0", "persist-credentials: false",
                     "token: ${{ steps.token.outputs.token }}"):
            with self.subTest(line=line):
                self.assertIn(line, step)
        declaration = extract_step(ACTION, DECLARATION)
        self.assertNotIn(".copier-answers.yml", declaration)
        self.assertLess(ACTION.index(f"- name: {CHECKOUT}\n"), ACTION.index(f"- name: {BOOTSTRAP}\n"))

    def test_token_is_read_only_and_scoped_to_standards(self) -> None:
        step = extract_step(ACTION, MINT)
        for line in ("app-id: ${{ inputs.app-id }}", "private-key: ${{ inputs.private-key }}",
                     "owner: ${{ github.repository_owner }}", "repositories: standards\n",
                     "permission-contents: read"):
            with self.subTest(line=line):
                self.assertIn(line, step + "\n")

    def test_mise_action_runs_the_native_bootstrap(self) -> None:
        step = extract_step(ACTION, BOOTSTRAP)
        self.assertIn("uses: jdx/mise-action@", step)
        for line in ("version: ${{ steps.prerequisites.outputs.mise_version }}",
                     "cache_key_prefix: ${{ inputs.cache-key-prefix }}",
                     "bootstrap: true", "bootstrap_args: --only ${{ inputs.bootstrap-parts }}",
                     "cache: ${{ inputs.bootstrap-parts != 'repos' }}",
                     "env: ${{ inputs.bootstrap-parts != 'repos' }}",
                     "MISE_GITHUB_TOKEN: ${{ github.token }}",
                     'GIT_TERMINAL_PROMPT: "0"', "GCM_INTERACTIVE: never"):
            with self.subTest(line=line):
                self.assertIn(line, step)
        self.assertNotIn("install_args", step)


class RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        self.env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "xdg"),
                    "GIT_CONFIG_NOSYSTEM": "1", "OWNER": OWNER}
        (self.home / ".gitconfig").write_text("[user]\n\tname = t\n")

    def git_config(self, *args: str) -> None:
        subprocess.run(["git", "config", "--global", *args], check=True,
                       env={**os.environ, **self.env})

    def routes(self) -> str:
        return subprocess.run(
            ["git", "config", "--global", "--get-regexp", r"^url\."], text=True,
            capture_output=True, env={**os.environ, **self.env},
        ).stdout

    def test_route_rewrites_only_the_standards_url(self) -> None:
        result = run(ROUTE, self.home, TOKEN="ghs_secret", **self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.routes(), f"{ROUTE_KEY} https://github.com/{OWNER}/standards\n")

    def test_removal_drops_only_the_standards_token_route(self) -> None:
        others = {
            "url.git@github.com:.insteadOf": "gh:",
            f"url.https://x-access-token:ghs_go@github.com/{OWNER}/scaleset.insteadOf":
                f"https://github.com/{OWNER}/scaleset",
        }
        for shell in BASHES:
            for routed in (True, False):
                for with_others in (True, False):
                    with self.subTest(shell=shell, routed=routed, others=with_others):
                        (self.home / ".gitconfig").write_text("[user]\n\tname = t\n")
                        if routed:
                            run(ROUTE, self.home, TOKEN="ghs_secret", **self.env)
                        if with_others:
                            for key, value in others.items():
                                self.git_config(key, value)
                        result = run(UNROUTE, self.home, shell=shell, **self.env)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        remaining = self.routes()
                        self.assertNotIn("ghs_secret", remaining)
                        self.assertEqual("ghs_go" in remaining, with_others)
                        self.assertEqual("git@github.com:" in remaining, with_others)


class ReadinessTests(unittest.TestCase):
    def ready(self, *, status: int, checkout: bool, shell: str = "bash"):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            bin_dir = repo / "bin"
            bin_dir.mkdir()
            (bin_dir / "mise").write_text(
                "#!/bin/sh\n"
                '[ "$*" = "bootstrap repos status --missing" ] || exit 2\n'
                f"exit {status}\n"
            )
            (bin_dir / "mise").chmod(0o755)
            if checkout:
                shared = repo / ".standards-config"
                (shared / "config" / "lefthook").mkdir(parents=True)
                (shared / "config" / "lefthook" / "base.yml").write_text("{}\n")
                for args in (["init", "-q", "-b", "stable"],
                             ["-c", "user.email=t@e", "-c", "user.name=t",
                              "commit", "-q", "--allow-empty", "-m", "x"]):
                    subprocess.run(["git", *args], cwd=shared, check=True, capture_output=True)
            summary = repo / "summary"
            result = run(READY, repo, shell=shell, PATH=f"{bin_dir}:{os.environ['PATH']}",
                         GITHUB_STEP_SUMMARY=str(summary))
            return result, summary.read_text() if summary.exists() else ""

    def test_present_checkout_passes_and_records_its_revision(self) -> None:
        for shell in BASHES:
            with self.subTest(shell=shell):
                result, summary = self.ready(status=0, checkout=True, shell=shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(summary, r"standards `stable` at `[0-9a-f]{40}`")

    def test_missing_or_stale_checkout_fails(self) -> None:
        for status, checkout, message in ((0, False, "shared config missing"), (1, True, "")):
            with self.subTest(status=status, checkout=checkout):
                result, _ = self.ready(status=status, checkout=checkout)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == "__main__":
    unittest.main()
