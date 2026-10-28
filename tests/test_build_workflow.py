"""Contracts for the injected Build workflow (Check Catalog BD-01 and CT-01)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_ruby_workflows import ROOT, extract_run_block, extract_step

WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
PICK_STEP = "Select the declared checks"
TASK_STEP = "Require the build task"
SMOKE_STEP = "Build, start and assert the release image"
BOOTSTRAP_STEP = "Bootstrap the Shared Config Checkout"


def job(name: str) -> str:
    """Return one top-level job block."""
    start = TEXT.index(f"\n  {name}:\n")
    following = [
        TEXT.index(f"\n  {other}:\n")
        for other in ("detect", "bd-01", "ct-01")
        if TEXT.index(f"\n  {other}:\n") > start
    ]
    return TEXT[start : min(following) if following else None]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def answers_repo(tmp: str, head: str, base: str | None = None) -> Path:
    """A repository whose HEAD carries *head* answers.

    With *base*, HEAD^1 carries those answers; with base="" HEAD^1 has no
    answers file; with base=None HEAD is a root commit.
    """
    repo = Path(tmp) / "consumer"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    answers = repo / ".copier-answers.yml"
    if base is not None:
        if base:
            answers.write_text(base, encoding="utf-8")
        else:
            (repo / "seed").write_text("x", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "base")
    answers.write_text(head, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "head")
    return repo


def pick(repo: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    output = repo.parent / "github-output"
    result = subprocess.run(
        ["bash", "-c", extract_run_block(TEXT, PICK_STEP)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(repo.parent),
            "MACOS_RUNNER": '[\n  "macos-tart"\n]',
            "HEAVY_RUNNER": '["medium"]',
        },
    )
    values = {}
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            values[key] = value
    return result, values


class InjectionContractTests(unittest.TestCase):
    def test_triggers_and_permissions(self) -> None:
        on_block = TEXT.split('"on":', 1)[1].split("permissions:", 1)[0]
        self.assertEqual(
            [line.strip() for line in on_block.strip().splitlines()],
            ["pull_request:", "merge_group:"],
        )
        self.assertIn("permissions:\n  contents: read\n", TEXT)
        self.assertNotIn("pull_request_target", TEXT)

    def test_source_repository_runs_are_a_no_op(self) -> None:
        self.assertIn(
            "if: github.repository != 'Rubio-Enterprises/.github'", job("detect")
        )
        for name in ("bd-01", "ct-01"):
            self.assertIn("needs: detect", job(name))

    def test_checks_are_gated_on_their_facets_and_routes(self) -> None:
        bd = job("bd-01")
        self.assertIn("if: needs.detect.outputs.artifact == 'true'", bd)
        self.assertIn("runs-on: ${{ fromJSON(needs.detect.outputs.artifact_runner) }}", bd)
        ct = job("ct-01")
        self.assertIn("if: needs.detect.outputs.image == 'true'", ct)
        self.assertIn(
            "runs-on: ${{ fromJSON(vars.RUNNERS || '{}').docker || "
            "fromJSON('[\"ubuntu-latest\"]') }}",
            ct,
        )

    def test_no_publishing_or_signing_credentials(self) -> None:
        self.assertNotIn("docker push", TEXT)
        self.assertNotIn("login-action", TEXT)
        self.assertNotIn("push: true", TEXT)
        self.assertNotIn("secrets.", job("ct-01"))
        secrets = {
            line.split("secrets.", 1)[1].split()[0].rstrip("}")
            for line in TEXT.splitlines()
            if "secrets." in line and not line.lstrip().startswith("#")
        }
        self.assertEqual(
            secrets, {"STANDARDS_READER_APP_ID", "STANDARDS_READER_APP_PRIVATE_KEY"}
        )
        for name in ("bd-01", "ct-01"):
            self.assertIn("persist-credentials: false", job(name))


class SelectionTests(unittest.TestCase):
    def test_declared_facets_select_their_checks(self) -> None:
        cases = [
            ("has_release_artifact: true\n", "true", "false"),
            ("has_container_image: true\n", "false", "true"),
            ("has_release_artifact: true\nhas_container_image: true\n", "true", "true"),
            ("has_release_artifact: false\nhas_container_image: 'true'\n", "false", "false"),
            ("# has_release_artifact: true\n", "false", "false"),
        ]
        for answers, artifact, image in cases:
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as tmp:
                result, out = pick(answers_repo(tmp, answers))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((out["artifact"], out["image"]), (artifact, image))

    def test_nothing_declared_is_reported_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, out = pick(answers_repo(tmp, "has_go: true\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((out["artifact"], out["image"]), ("false", "false"))
        self.assertIn("::notice::", result.stdout)

    def test_removing_a_facet_keeps_its_check_until_the_removal_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = answers_repo(
                tmp,
                head="has_container_image: false\n",
                base="has_container_image: true\nhas_release_artifact: true\n",
            )
            result, out = pick(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((out["artifact"], out["image"]), ("true", "true"))

    def test_a_base_without_answers_or_a_root_commit_uses_the_head(self) -> None:
        for base in (None, ""):
            with self.subTest(base=base), tempfile.TemporaryDirectory() as tmp:
                result, out = pick(answers_repo(tmp, "has_release_artifact: true\n", base))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((out["artifact"], out["image"]), ("true", "false"))

    def test_swift_builds_route_to_macos_and_others_to_the_heavy_tier(self) -> None:
        for answers, runner in (
            ("has_release_artifact: true\nhas_swift: true\n", ["macos-tart"]),
            ("has_release_artifact: true\nhas_python: true\n", ["medium"]),
        ):
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as tmp:
                result, out = pick(answers_repo(tmp, answers))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(out["artifact_runner"]), runner)

    def test_only_a_literal_true_declares_ruby(self) -> None:
        for answers, expected in (
            ("has_release_artifact: true\nhas_ruby: true\nhas_swift: true\n", "true"),
            ("has_release_artifact: true\nhas_ruby: true\n", "true"),
            ("has_release_artifact: true\nhas_ruby: 'true'\n", "false"),
            ("has_release_artifact: true\n# has_ruby: true\n", "false"),
            ("has_release_artifact: true\n", "false"),
        ):
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as tmp:
                result, out = pick(answers_repo(tmp, answers))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(out["has_ruby"], expected)

    def test_config_metadata_does_not_change_build_selection(self) -> None:
        baseline = None
        for metadata in ("", "config_model: rendered\n", "config_model: native\n",
                         "config_model: 'native'\n", "config_model: unknown\n"):
            with self.subTest(metadata=metadata), tempfile.TemporaryDirectory() as tmp:
                result, out = pick(answers_repo(tmp, "has_release_artifact: true\n" + metadata))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("config_model", out)
                if baseline is None:
                    baseline = out
                self.assertEqual(out, baseline)


class ArtifactBuildTests(unittest.TestCase):
    def run_task_check(self, has_task: bool) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp)
            stub = bin_dir / "mise"
            stub.write_text(
                "#!/bin/sh\n"
                '[ "$1 $2 $3" = "tasks info build" ] || exit 2\n'
                f"exit {0 if has_task else 1}\n"
            )
            stub.chmod(0o755)
            return subprocess.run(
                ["bash", "-c", extract_run_block(TEXT, TASK_STEP)],
                cwd=tmp, text=True, capture_output=True, check=False,
                env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
            )

    def test_a_declared_artifact_without_a_build_task_fails(self) -> None:
        result = self.run_task_check(has_task=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no 'build' mise task", result.stderr)
        self.assertEqual(self.run_task_check(has_task=True).returncode, 0)

    def test_the_task_check_sees_the_build_steps_mise_environment(self) -> None:
        for name in (TASK_STEP, "Build the release artifact (mise run build)"):
            self.assertIn("MISE_ENV: ci", extract_step(TEXT, name), name)

    def test_build_runs_the_repository_task_after_the_native_bootstrap(self) -> None:
        # The standards-bootstrap action removes its token route before it
        # returns, so the repository's task never runs with the route.
        bd = job("bd-01")
        self.assertIn("run: mise run build", bd)
        self.assertNotIn("keep-route", extract_step(TEXT, BOOTSTRAP_STEP))
        order = [
            BOOTSTRAP_STEP,
            BOOTSTRAP_STEP,
            "Install pnpm",
            TASK_STEP,
            "Build the release artifact (mise run build)",
        ]
        positions = [bd.index(f"- name: {name}") for name in order]
        self.assertEqual(positions, sorted(positions))

    def test_ruby_is_provisioned_on_every_route_before_the_build(self) -> None:
        bd = job("bd-01")
        self.assertIn("has_ruby: ${{ steps.pick.outputs.has_ruby }}", job("detect"))
        prepare = extract_step(TEXT, "Prepare macOS Ruby dependencies and tool cache")
        self.assertIn("if: runner.os == 'macOS' && needs.detect.outputs.has_ruby == 'true'", prepare)
        self.assertIn("brew install openssl@3 libyaml gmp", prepare)
        self.assertIn("/Users/runner/hostedtoolcache", prepare)
        install = extract_step(TEXT, "Install Ruby and locked bundle")
        self.assertIn("if: needs.detect.outputs.has_ruby == 'true'", install)
        self.assertNotIn("runner.os", install)
        self.assertIn("bundler-cache: true", install)
        self.assertNotIn("ruby-version:", install)
        order = [
            "Prepare the macOS build toolchain",
            "Prepare macOS Ruby dependencies and tool cache",
            "Install Ruby and locked bundle",
            BOOTSTRAP_STEP,
            "Build the release artifact (mise run build)",
        ]
        positions = [bd.index(f"- name: {name}") for name in order]
        self.assertEqual(positions, sorted(positions))

    def test_ruby_has_no_warning_only_path(self) -> None:
        self.assertNotIn("Report unprovisioned Ruby", TEXT)
        self.assertNotIn("::warning::", job("bd-01"))

    def test_apple_builds_require_xcode(self) -> None:
        step = extract_step(TEXT, "Prepare the macOS build toolchain")
        self.assertIn("if: runner.os == 'macOS'", step)
        self.assertIn("xcodebuild -version", step)


class ContainerSmokeTests(unittest.TestCase):
    """Run the CT-01 step against a scripted docker."""

    def smoke(
        self, *, compose: bool = True, services: str = "app\ndb", security: str = "",
        up_exit: int = 0, cid: str = "abc123", health: str = "healthy",
        top: str = "PID UID\n10 10001\n11 10001",
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            if compose:
                (repo / "compose.ci.yml").write_text("services: {}\n")
            log = repo / "docker.log"
            stub = repo / "bin" / "docker"
            stub.parent.mkdir()
            stub.write_text(
                "#!/bin/bash\n"
                'echo "$*" >> "$LOG"\n'
                'case "$*" in\n'
                '  "compose -f compose.ci.yml config --services") printf "%s\\n" "$SERVICES" ;;\n'
                '  "info --format"*) printf \'["name=seccomp,profile=builtin"%s]\\n\' "$SECURITY" ;;\n'
                '  "compose -f compose.ci.yml up --build --wait --wait-timeout 300") exit "$UP_EXIT" ;;\n'
                '  "compose -f compose.ci.yml ps -q app") printf "%s\\n" "$CID" ;;\n'
                '  "inspect -f"*) printf "%s\\n" "$HEALTH" ;;\n'
                '  "top "*) printf "%s\\n" "$TOP" ;;\n'
                '  *) exit 97 ;;\n'
                "esac\n"
            )
            stub.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", extract_run_block(TEXT, SMOKE_STEP)],
                cwd=repo, text=True, capture_output=True, check=False,
                env={
                    **os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}",
                    "LOG": str(log), "SERVICES": services, "SECURITY": security,
                    "UP_EXIT": str(up_exit), "CID": cid, "HEALTH": health, "TOP": top,
                },
            )
            return result, log.read_text() if log.exists() else ""

    def test_healthy_non_root_release_image_passes_without_pushing(self) -> None:
        result, log = self.smoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("compose -f compose.ci.yml up --build --wait --wait-timeout 300", log)
        self.assertNotIn("push", log)
        self.assertIn("CT-01 passed", result.stdout)

    def test_failures_are_closed(self) -> None:
        cases = {
            "missing descriptor": ({"compose": False}, "compose.ci.yml is missing"),
            "no app service": ({"services": "web"}, "service 'app'"),
            "remapped daemon": ({"security": ',"name=userns"'}, "remaps user namespaces"),
            "rootless daemon": ({"security": ',"name=rootless"'}, "remaps user namespaces"),
            "unhealthy": ({"up_exit": 1}, ""),
            "not running": ({"cid": ""}, "no running container"),
            "no healthcheck": ({"health": ""}, "no healthcheck"),
            "starting": ({"health": "starting"}, "passing healthcheck"),
            "root process": ({"top": "PID UID\n10 0\n11 10001"}, "non-root"),
            "no processes": ({"top": "PID UID"}, "non-root"),
        }
        for label, (kwargs, message) in cases.items():
            with self.subTest(case=label):
                result, _ = self.smoke(**kwargs)
                self.assertNotEqual(result.returncode, 0, label)
                self.assertIn(message, result.stderr)

    def test_cleanup_always_runs_with_a_unique_project(self) -> None:
        cleanup = extract_step(TEXT, "Collect logs and remove the smoke project")
        self.assertIn("if: always()", cleanup)
        self.assertIn("down --volumes --remove-orphans --rmi local", cleanup)
        self.assertIn(
            "COMPOSE_PROJECT_NAME: ct01-${{ github.run_id }}-${{ github.run_attempt }}",
            job("ct-01"),
        )


if __name__ == "__main__":
    unittest.main()
