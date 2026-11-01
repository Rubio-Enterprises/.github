"""The Standards Setup Caches (docs/standards-setup.md).

The default-branch seed saves mise's tool installs, npm's package cache and the trivy database;
pull-request runs of the Standards Check restore them and never save (standards#821, #835).
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from test_ruby_workflows import extract_step

ROOT = Path(__file__).resolve().parents[1]
ACTION = (ROOT / 'actions/standards-setup/action.yml').read_text()
SEED = (ROOT / '.github/workflows/standards-seed.yml').read_text()
CHECK = (ROOT / '.github/workflows/standards.yml').read_text()
CACHE_SHA = '55cc8345863c7cc4c66a329aec7e433d2d1c52a9'
KEYS = "Name the Standards Setup Cache keys"
SETUP = "Install the Standards Release, its tools and the repository's dependencies"


def field(step: str, name: str) -> str:
    match = re.search(rf'^\s+{re.escape(name)}: (.*)$', step, re.M)
    assert match, f'{name} missing from:\n{step}'
    return match.group(1)


def run_block(step: str) -> str:
    lines = step.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == 'run: |')
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    body = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) < indent:
            break
        body.append(line)
    return textwrap.dedent('\n'.join(body)) + '\n'


class ActionContract(unittest.TestCase):
    def test_pull_requests_restore_and_only_the_seed_saves(self):
        for name in ("Restore mise's tool installs", "Restore npm's package cache"):
            step = extract_step(ACTION, name)
            self.assertEqual(field(step, 'uses'), f'actions/cache/restore@{CACHE_SHA} # v6')
            self.assertEqual(field(step, 'if'), "inputs.restore == 'true'")
            # Fallback keys only outside the seed, so a cache the seed saves is built fresh.
            self.assertTrue(field(step, 'restore-keys').startswith("${{ inputs.save != 'true' && "))
        for name, restored in (("Save mise's tool installs", 'mise'), ("Save npm's package cache", 'npm')):
            step = extract_step(ACTION, name)
            self.assertEqual(field(step, 'uses'), f'actions/cache/save@{CACHE_SHA} # v6')
            self.assertEqual(
                field(step, 'if'),
                f"inputs.restore == 'true' && inputs.save == 'true' && steps.{restored}.outputs.cache-hit != 'true'",
            )
        inputs = ACTION.split('\ninputs:\n', 1)[1].split('\noutputs:\n', 1)[0]
        self.assertRegex(inputs, r'  restore:\n(    .*\n)*    default: "true"')
        self.assertRegex(inputs, r'  save:\n(    .*\n)*    default: "false"')

    def test_setup_runs_unconditionally_with_the_token_after_the_restores(self):
        step = extract_step(ACTION, SETUP)
        self.assertEqual(field(step, 'run'), 'mise run setup')
        self.assertNotRegex(step, r'^\s+if:', 'setup must run in every mode')
        self.assertEqual(field(step, 'NODE_AUTH_TOKEN'), '${{ inputs.token }}')
        self.assertEqual(field(step, 'MISE_GITHUB_TOKEN'), '${{ inputs.token }}')
        order = [ACTION.index(f'- name: {name}') for name in (
            KEYS, "Restore mise's tool installs", "Restore npm's package cache", SETUP,
            "Save mise's tool installs", "Save npm's package cache",
        )]
        self.assertEqual(order, sorted(order))

    def test_saves_reuse_the_keys_and_paths_named_before_setup(self):
        for kind, label, path in (('mise', 'tool installs', 'mise-paths'), ('npm', 'package cache', 'npm-path')):
            restore = extract_step(ACTION, f"Restore {kind}'s {label}")
            save = extract_step(ACTION, f"Save {kind}'s {label}")
            self.assertEqual(field(restore, 'key'), field(save, 'key'))
            self.assertEqual(field(save, 'path'), f'${{{{ steps.keys.outputs.{path} }}}}')
            self.assertEqual(field(restore, 'path'), field(save, 'path'))
        self.assertIn("'!**/node_modules/**'", field(extract_step(ACTION, KEYS), 'NPM_LOCKS'))


class KeyScript(unittest.TestCase):
    """Run the key step's script against a fake runner environment."""

    def run_keys(self, **env):
        script = run_block(extract_step(ACTION, KEYS))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            bindir = Path(tmp) / 'bin'
            bindir.mkdir()
            (bindir / 'mise').write_text('#!/bin/sh\necho "2026.10.19 linux-x64 (2026-10-24)"\n')
            (bindir / 'uv').write_text('#!/bin/sh\nexit 2\n')
            for tool in ('mise', 'uv'):
                (bindir / tool).chmod(0o755)
            base = {
                'PATH': f"{bindir}{os.pathsep}{os.environ['PATH']}",
                'HOME': '/home/runner',
                'GITHUB_OUTPUT': str(out),
                'RUNNER_OS': 'Linux' if Path('/etc/os-release').exists() else 'Other',
                'RUNNER_ARCH': 'X64',
                'MISE_FILES': 'abc',
                'NPM_LOCKS': 'def',
            }
            subprocess.run(['bash', '-c', script], env={**base, **env}, check=True)
            text = out.read_text()
        values = dict(re.findall(r'^([a-z-]+)=(.*)$', text, re.M))
        blocks = dict(re.findall(r'^([a-z-]+)<<EOF\n(.*?)\nEOF$', text, re.M | re.S))
        return values, blocks

    def test_keys_bind_platform_mise_interpreter_week_and_hashes(self):
        values, blocks = self.run_keys()
        self.assertRegex(values['mise-key'], r'^standards-mise-v1-.+-X64-.+-2026\.10\.19-[0-9a-f]{8}-\d{4}-W\d{2}-abc$')
        self.assertRegex(values['npm-key'], r'^standards-npm-v1-.+-X64-.+-\d{4}-W\d{2}-def$')
        week_prefix, any_week = blocks['mise-restore-keys'].splitlines()
        self.assertTrue(values['mise-key'].startswith(week_prefix))
        self.assertEqual(any_week, values['mise-key'].rsplit('-', 3)[0] + '-')
        self.assertTrue(values['npm-key'].startswith(blocks['npm-restore-keys'].splitlines()[0]))
        self.assertEqual(values['npm-path'], '/home/runner/.npm')

    def test_paths_follow_the_runner_and_skip_the_baked_mise_binary(self):
        _, blocks = self.run_keys(MISE_DATA_DIR='/home/runner/.local/share/mise')
        self.assertEqual(
            blocks['mise-paths'].splitlines(),
            ['/home/runner/.local/share/mise/installs', '/home/runner/.local/share/mise/shims'],
        )
        values, _ = self.run_keys(npm_config_cache='/tmp/npm')
        self.assertEqual(values['npm-path'], '/tmp/npm')


class Workflows(unittest.TestCase):
    def test_the_check_restores_and_never_saves(self):
        trivy = extract_step(CHECK, 'Restore the trivy vulnerability database')
        self.assertEqual(field(trivy, 'uses'), f'actions/cache/restore@{CACHE_SHA} # v6')
        mise_action = CHECK.split('- uses: jdx/mise-action@', 1)[1].split('\n      - ', 1)[0]
        self.assertIn('cache: false', mise_action)
        setup = CHECK.split('      - id: setup\n', 1)[1].split('\n      - ', 1)[0]
        self.assertIn('uses: Rubio-Enterprises/.github/actions/standards-setup@main', setup)
        self.assertNotIn('save:', setup)
        self.assertNotIn('actions/cache/save@', CHECK)
        self.assertNotIn(f'actions/cache@{CACHE_SHA}', CHECK)

    def test_the_seed_saves_only_on_the_default_branch_with_the_checks_keys(self):
        self.assertRegex(SEED, r'"on":\n  workflow_call:\n(?!  \w)')
        self.assertIn("github.ref == format('refs/heads/{0}', github.event.repository.default_branch)", SEED)
        self.assertIn("needs.route.outputs.ci-image != 'true'", SEED)
        self.assertIn('runs-on: ${{ fromJSON(needs.route.outputs.runner) }}', SEED)
        self.assertIn('uses: Rubio-Enterprises/.github/.github/workflows/project-route.yml@main', SEED)
        setup = SEED.split('- uses: Rubio-Enterprises/.github/actions/standards-setup@main\n', 1)[1].split('\n      - ', 1)[0]
        self.assertIn('token: ${{ secrets.GITHUB_TOKEN }}', setup)
        self.assertIn('save: "true"', setup)
        check_trivy = extract_step(CHECK, 'Restore the trivy vulnerability database')
        for name in ("Look up this week's trivy vulnerability database", "Save this week's trivy vulnerability database"):
            step = extract_step(SEED, name)
            self.assertEqual(field(step, 'path'), field(check_trivy, 'path'))
            self.assertEqual(field(step, 'key'), field(check_trivy, 'key'))
        self.assertIn('lookup-only: true', extract_step(SEED, "Look up this week's trivy vulnerability database"))
        # The same runner setup as the check, so the computed keys and cache versions agree.
        for pattern in (r'MISE_VERSION: "[^"]+"', r'- uses: astral-sh/setup-uv@\S+', r'- uses: jdx/mise-action@\S+',
                        r'version: "0\.[0-9.]+"'):
            self.assertEqual(re.findall(pattern, SEED), re.findall(pattern, CHECK), pattern)
        self.assertIn('permissions:\n  contents: read\n  packages: read', SEED)


if __name__ == '__main__':
    unittest.main()
