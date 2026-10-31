"""Shared workflow-text helpers, and the one digest-pinned Ruby action."""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUBY_SETUP_PATTERN = re.compile(
    r"ruby/setup-ruby@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+"
)


def ruby_setup_pin(workflow_text: str) -> str:
    """Read the single versioned, digest-pinned Ruby action from a workflow."""
    pins = re.findall(r"^ *uses: (ruby/setup-ruby@.*)$", workflow_text, re.MULTILINE)
    if len(pins) != 1 or not RUBY_SETUP_PATTERN.fullmatch(pins[0]):
        raise AssertionError(f"expected exactly one versioned 40-hex Ruby pin, found {pins}")
    return pins[0]


def extract_run_block(workflow_text: str, step_name: str) -> str:
    """Extract and dedent the literal shell body for a named workflow step."""
    lines = workflow_text.splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    run_index = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index].strip() == "run: |"
    )
    indent = len(lines[run_index]) - len(lines[run_index].lstrip()) + 2
    body: list[str] = []
    for line in lines[run_index + 1 :]:
        if line and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line[indent:] if line else "")
    return "\n".join(body) + "\n"


def extract_step(workflow_text: str, step_name: str) -> str:
    """Extract one list-item step, excluding adjacent comments and steps."""
    lines = workflow_text.splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith(" " * indent + "- name:")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def run_script(script: str, repo: Path, **extra_env: str) -> dict[str, str]:
    """Run an extracted Actions shell body and parse its GITHUB_OUTPUT."""
    output = repo / "github-output"
    env = os.environ.copy()
    env.update(extra_env, GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


class RubyPinTests(unittest.TestCase):
    def test_only_testflight_sets_up_ruby_with_a_versioned_digest_pin(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        actual = {
            path.name
            for path in workflow_dir.glob("*.yml")
            if "ruby/setup-ruby@" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(actual, {"testflight.yml"})
        ruby_setup_pin((workflow_dir / "testflight.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
