"""The injected standards job restores exactly what standards-cache.yml saves on the default branch."""

from pathlib import Path
import os
import re
import subprocess
import tempfile
import textwrap
import unittest

from test_ruby_workflows import extract_step

ROOT = Path(__file__).resolve().parents[1] / '.github/workflows'
INJECTED = (ROOT / 'standards.yml').read_text()
WARMER = (ROOT / 'standards-cache.yml').read_text()


def steps(workflow, action):
    """The `with:` fields of every step that uses `action`, as dicts."""
    found = []
    for block in workflow.split('\n      - ')[1:]:
        if not re.search(rf'uses: {re.escape(action)}@[0-9a-f]{{40}}', block):
            continue
        fields = dict(re.findall(r'^ {10}([\w-]+): (.+)$', block, re.M))
        found.append(fields)
    return found


def week_step(workflow):
    return re.search(r"name: Name this week's cache keys\n\s+run: (.+)\n", workflow).group(1)


class StandardsCacheTests(unittest.TestCase):
    def test_pull_requests_restore_and_never_save(self):
        self.assertEqual(steps(INJECTED, 'actions/cache'), [])
        self.assertEqual(steps(INJECTED, 'actions/cache/save'), [])
        restores = steps(INJECTED, 'actions/cache/restore')
        self.assertEqual(len(restores), 2)
        for fields in restores:
            self.assertEqual(fields['restore-keys'], fields['key'].rsplit('${{ steps.week', 1)[0])
        self.assertNotIn("mise-action", INJECTED)

    def test_the_warmer_saves_the_keys_and_paths_the_injected_job_restores(self):
        def pairs(found):
            return {(fields['path'], fields['key']) for fields in found}

        restored = pairs(steps(INJECTED, 'actions/cache/restore'))
        self.assertEqual(restored, pairs(steps(WARMER, 'actions/cache/save')))
        self.assertEqual(restored, pairs(steps(WARMER, 'actions/cache/restore')))
        for fields in steps(WARMER, 'actions/cache/restore'):
            self.assertEqual(fields['lookup-only'], 'true')

    def test_both_name_the_week_the_same_way(self):
        self.assertEqual(week_step(INJECTED), week_step(WARMER))

    def test_the_warmer_runs_on_the_injected_jobs_route_and_only_when_called(self):
        route = 'uses: Rubio-Enterprises/.github/.github/workflows/project-route.yml@main'
        self.assertIn(route, INJECTED)
        self.assertIn(route, WARMER)
        self.assertIn('runs-on: ${{ fromJSON(needs.route.outputs.runner) }}', WARMER)
        self.assertRegex(WARMER, r'"on":\n  workflow_call:\n')
        self.assertIn('permissions:\n  contents: read\n  packages: read', WARMER)

    def test_the_trivy_database_is_used_as_restored_only_when_fresh(self):
        step = extract_step(INJECTED, 'Use the restored trivy database without updating it')
        self.assertIn("if: steps.trivy-db.outputs.cache-matched-key != ''", step)
        self.assertIn('TRIVY_SKIP_DB_UPDATE=true', step)
        self.assertIn('"$days" -lt 8', step)
        self.assertLess(
            INJECTED.index('Use the restored trivy database without updating it'),
            INJECTED.index('name: Every Check on every file (CI Image)'),
        )

    def test_warming_never_installs_project_toolchains_on_the_host(self):
        self.assertNotIn("mise exec", WARMER)
        self.assertIn("just exec trivy", WARMER)

    def test_warming_uses_the_linux_check_route_even_for_native_macos_repositories(self):
        self.assertRegex(WARMER, r'project-route.yml@main\n    with:\n      route: linux\n')

    def test_hosted_checkout_is_writable_before_the_container_downloads(self):
        step = extract_step(WARMER, 'Prepare container-writable hosted checkout')
        self.assertIn("needs.route.outputs.ci-image != 'true'", step)
        self.assertIn("runner.environment == 'github-hosted'", step)
        self.assertIn('chmod -R a+rwX "$GITHUB_WORKSPACE"', step)
        self.assertLess(WARMER.index(step), WARMER.index('name: Download the trivy'))

    def test_only_successfully_filled_caches_are_saved_independently(self):
        for name, cache in [('trivy vulnerability database', 'trivy'),
                            ("PR conventions' npm cache", 'npm')]:
            step = extract_step(WARMER, 'Save the ' + name)
            self.assertIn(f"if: steps.{cache}-warm.outputs.ready == 'true'", step)
        self.assertNotIn('continue-on-error', WARMER)


class WarmerExecutionTests(unittest.TestCase):
    def run_warmer(self, cache, *, ci_image=True, tool=True, lock=True, failure=False):
        names = {'trivy': 'Download the trivy vulnerability database',
                 'npm': "Fill the PR conventions' npm cache"}
        step = extract_step(WARMER, names[cache])
        self.assertIn(f"if: steps.{cache}.outputs.cache-hit != 'true'", step)
        script = textwrap.dedent(step.split('run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / 'bin'
            tools.mkdir()
            conventions = root / 'pr-conventions'
            conventions.mkdir()
            if lock:
                (conventions / 'package-lock.json').write_text('{}')
            script = script.replace('/opt/standards/pr-conventions', str(conventions))
            executable = tools / (cache if ci_image else 'just')
            if tool:
                executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"$CALLS"\n'
                                      + ('exit 7\n' if failure else ''))
                executable.chmod(0o755)
            output = root / 'output'
            output.touch()
            calls = root / 'calls'
            env = {**os.environ, 'PATH': str(tools), 'CI_IMAGE': str(ci_image).lower(),
                   'XDG_CACHE_HOME': str(root / '.cache'), 'TRIVY_CACHE_DIR': str(root / '.cache/trivy'),
                   'GITHUB_OUTPUT': str(output), 'CALLS': str(calls)}
            result = subprocess.run(['/bin/bash', '-e', '-c', script], env=env,
                                    capture_output=True, text=True)
            return result, output.read_text(), calls.read_text() if calls.exists() else ''

    def test_stale_images_skip_only_the_missing_tool_without_saving_an_empty_cache(self):
        for cache, tool, lock in [('trivy', False, True), ('npm', False, True), ('npm', True, False)]:
            with self.subTest(cache=cache, tool=tool, lock=lock):
                result, output, calls = self.run_warmer(cache, tool=tool, lock=lock)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('::notice::CI image has no', result.stdout)
                self.assertEqual(output, '')
                self.assertEqual(calls, '')

    def test_capable_images_warm_even_when_the_repository_revision_changed(self):
        for cache in ['trivy', 'npm']:
            with self.subTest(cache=cache):
                result, output, calls = self.run_warmer(cache)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, 'ready=true\n')
                self.assertNotIn('::notice::', result.stdout)
                self.assertIn('--cache', calls)
                self.assertNotIn('build', calls)

    def test_real_download_and_install_failures_are_not_hidden(self):
        for cache in ['trivy', 'npm']:
            with self.subTest(cache=cache):
                result, output, calls = self.run_warmer(cache, failure=True)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(output, '')
                self.assertNotEqual(calls, '')

    def test_hosted_jobs_build_and_execute_inside_the_dev_image(self):
        for cache in ['trivy', 'npm']:
            with self.subTest(cache=cache):
                result, output, calls = self.run_warmer(cache, ci_image=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, 'ready=true\n')
                self.assertEqual(calls.splitlines()[0], 'build')
                self.assertTrue(calls.splitlines()[1].startswith('exec ' + cache + ' '))


if __name__ == '__main__':
    unittest.main()
