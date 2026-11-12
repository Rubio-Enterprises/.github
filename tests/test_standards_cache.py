"""The injected standards job restores exactly what standards-cache.yml saves on the default branch."""

from pathlib import Path
import os
import json
import re
import subprocess
import tempfile
import textwrap
import unittest

from test_ruby_workflows import extract_step
from test_standards_route import workflow_jobs

ROOT = Path(__file__).resolve().parents[1] / '.github/workflows'
INJECTED = (ROOT / 'standards.yml').read_text().split('\n  standards-macos:', 1)[0]
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
        self.assertEqual(len(restores), 1)
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
        self.assertIn('    permissions:\n      contents: read\n      packages: read', WARMER)

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

    def test_only_a_successfully_filled_cache_is_saved(self):
        step = extract_step(WARMER, 'Save the trivy vulnerability database')
        self.assertIn("if: steps.trivy-warm.outputs.ready == 'true'", step)
        self.assertNotIn('continue-on-error', WARMER)

    def test_nothing_warms_the_tools_baked_into_the_image(self):
        # The image bakes the PR conventions' node_modules at build time and the Checks never
        # reinstall them. The entrypoint runs as the workspace owner (uid 1001 on hosted
        # runners), which cannot write the image's /opt/standards tree.
        for workflow in (INJECTED, WARMER):
            self.assertNotIn('standards-npm', workflow)
            self.assertNotIn('npm ci', workflow)
        self.assertNotIn('/opt/standards', WARMER)


class HostedContextTests(unittest.TestCase):
    def setUp(self):
        self.steps = workflow_jobs()['standards-linux']['steps']
        self.writer = next(step for step in self.steps if step.get('id') == 'context-hosted')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.event = self.root / 'runner-event.json'
        self.event.write_text('{"pull_request":{"number":992}}')
        self.env = {**os.environ, 'GITHUB_WORKSPACE': str(self.root),
                    'GITHUB_WORKFLOW': 'standards', 'GITHUB_EVENT_NAME': 'pull_request',
                    'GITHUB_EVENT_PATH': str(self.event), 'GITHUB_REPOSITORY': 'Rubio-Enterprises/example',
                    'GITHUB_RUN_ID': '123', 'GITHUB_SERVER_URL': 'https://github.com',
                    'BASE_SHA': 'a' * 40, 'HEAD_SHA': 'b' * 40, 'PR_TITLE': 'fix: example',
                    'CI': 'true', 'XDG_CACHE_HOME': str(self.root / '.cache'),
                    'TRIVY_CACHE_DIR': str(self.root / '.cache/trivy')}
        self.env.pop('TRIVY_SKIP_DB_UPDATE', None)

    def run_writer(self):
        return subprocess.run(['bash', '-euc', self.writer['run']], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def test_one_hosted_writer_runs_after_freshness_and_before_chmod_and_checks(self):
        self.assertEqual(self.writer['if'], "needs.route.outputs.ci-image != 'true'")
        names = [step.get('name') for step in self.steps]
        freshness = names.index('Use the restored trivy database without updating it')
        restored = names.index('Restore the trivy vulnerability database')
        writable = names.index('Prepare container-writable hosted checkout')
        check = next(i for i, step in enumerate(self.steps) if step.get('id') == 'check-hosted')
        writer = self.steps.index(self.writer)
        self.assertLess(restored, freshness)
        self.assertLess(freshness, writer)
        self.assertLess(writer, writable)
        self.assertLess(writable, check)
        writers = [step for step in self.steps if '$ARGS.named' in step.get('run', '')]
        self.assertEqual(writers, [self.writer])
        self.assertEqual(self.writer['env']['PR_TITLE'], '${{ github.event.pull_request.title }}')

    def test_optional_update_setting_preserves_unset_empty_and_final_values(self):
        for setting in (None, '', 'true', 'false'):
            with self.subTest(setting=setting):
                if setting is not None:
                    self.env['TRIVY_SKIP_DB_UPDATE'] = setting
                result = self.run_writer()
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads((self.root / '.cache/ci/context.json').read_text())
                self.assertEqual(context['CI'], 'true')
                self.assertEqual(context['XDG_CACHE_HOME'], str(self.root / '.cache'))
                self.assertEqual(context['TRIVY_CACHE_DIR'], str(self.root / '.cache/trivy'))
                if setting is None:
                    self.assertNotIn('TRIVY_SKIP_DB_UPDATE', context)
                else:
                    self.assertEqual(context['TRIVY_SKIP_DB_UPDATE'], setting)

    def test_directory_and_context_symlinks_are_refused_without_touching_the_target(self):
        for relative in ('.cache', '.cache/ci', '.cache/ci/context.json',
                         '.cache/ci/event.json', '.cache/ci/summary.md'):
            for dangling in (False, True):
                with self.subTest(path=relative, dangling=dangling), tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp) / 'target'
                    if not dangling:
                        if relative.endswith(('.json', '.md')):
                            target.write_text('untouched')
                        else:
                            target.mkdir()
                            (target / 'context.json').write_text('untouched')
                    link = self.root / relative
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(target)
                    result = self.run_writer()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('::error::', result.stdout + result.stderr)
                    self.assertIn(relative, result.stdout + result.stderr)
                    if not dangling:
                        sentinel = target if target.is_file() else target / 'context.json'
                        self.assertEqual(sentinel.read_text(), 'untouched')
                    self.assertEqual(target.exists(), not dangling)
                    link.unlink()

    def test_committed_directory_symlink_cannot_disguise_tracked_context(self):
        for relative in ('.cache', '.cache/ci'):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                subprocess.run(['git', 'init', '-q', str(root)], check=True)
                data = root / 'data'
                data.mkdir()
                context = data / 'context.json'
                context.write_text('untouched')
                link = root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(os.path.relpath(data, link.parent))
                subprocess.run(['git', 'add', '.'], cwd=root, check=True)
                subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                                'commit', '-qm', 'test fixture'], cwd=root, check=True)
                result = subprocess.run(['bash', '-euc', self.writer['run']], cwd=root,
                                        env={**self.env, 'GITHUB_WORKSPACE': str(root)},
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('symlink', result.stdout + result.stderr)
                self.assertEqual(context.read_text(), 'untouched')

    def test_staged_and_committed_context_are_refused_even_when_deleted(self):
        context = self.root / '.cache/ci/context.json'
        context.parent.mkdir(parents=True)
        context.write_text('untouched')
        subprocess.run(['git', 'add', '-f', str(context)], cwd=self.root, check=True)
        for state in ('staged', 'committed', 'deleted'):
            with self.subTest(state=state):
                if state == 'committed':
                    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                                    'commit', '-qm', 'test fixture'], cwd=self.root, check=True)
                if state == 'deleted':
                    context.unlink()
                result = self.run_writer()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('::error::', result.stdout + result.stderr)
                self.assertIn('tracked', result.stdout + result.stderr)
                if state != 'deleted':
                    self.assertEqual(context.read_text(), 'untouched')
                else:
                    self.assertFalse(context.exists())

    def test_ignored_untracked_context_can_be_replaced(self):
        (self.root / '.gitignore').write_text('.cache/\n')
        context = self.root / '.cache/ci/context.json'
        context.parent.mkdir(parents=True)
        context.write_text('{"CI":"false"}')
        result = self.run_writer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(context.read_text())['CI'], 'true')


class WarmerExecutionTests(unittest.TestCase):
    def run_warmer(self, *, ci_image=True, tool=True, failure=False):
        step = extract_step(WARMER, 'Download the trivy vulnerability database')
        self.assertIn("if: steps.trivy.outputs.cache-hit != 'true'", step)
        script = textwrap.dedent(step.split('run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / 'bin'
            tools.mkdir()
            executable = tools / ('trivy' if ci_image else 'just')
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

    def test_stale_images_skip_the_missing_tool_without_saving_an_empty_cache(self):
        result, output, calls = self.run_warmer(tool=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('::notice::CI image has no', result.stdout)
        self.assertEqual(output, '')
        self.assertEqual(calls, '')

    def test_capable_images_warm_even_when_the_repository_revision_changed(self):
        result, output, calls = self.run_warmer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, 'ready=true\n')
        self.assertNotIn('::notice::', result.stdout)
        self.assertIn('--cache-dir', calls)
        self.assertNotIn('build', calls)

    def test_real_download_failures_are_not_hidden(self):
        result, output, calls = self.run_warmer(failure=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(output, '')
        self.assertNotEqual(calls, '')

    def test_hosted_jobs_build_and_execute_inside_the_dev_image(self):
        result, output, calls = self.run_warmer(ci_image=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, 'ready=true\n')
        self.assertEqual(calls.splitlines()[0], 'build')
        self.assertTrue(calls.splitlines()[1].startswith('exec trivy '))


if __name__ == '__main__':
    unittest.main()
