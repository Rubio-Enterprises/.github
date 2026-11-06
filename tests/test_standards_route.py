from itertools import product
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_project_route import ID, run_route

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/standards.yml').read_text()


def workflow_jobs():
    return json.loads(subprocess.check_output(
        ['yq', '-o=json', str(ROOT / '.github/workflows/standards.yml')], text=True))['jobs']


class StandardsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = workflow_jobs()

    def test_injected_workflow_consumes_the_central_linux_router(self):
        route = self.jobs['route']
        self.assertEqual(route['uses'],
                         'Rubio-Enterprises/.github/.github/workflows/project-route.yml@main')
        self.assertEqual(route['with'], {'route': 'linux'})
        self.assertEqual(route['if'], "github.repository != 'Rubio-Enterprises/.github'")
        linux = self.jobs['standards-linux']
        self.assertEqual(linux['needs'], 'route')
        self.assertEqual(linux['if'], route['if'])
        self.assertEqual(linux['runs-on'], '${{ fromJSON(needs.route.outputs.runner) }}')
        self.assertNotIn('secrets: inherit', WORKFLOW)

    def test_linux_and_macos_select_the_same_linux_leg_with_declared_size(self):
        for platform, size, private in product(('linux', 'macos'), ('small', 'medium'), (True, False)):
            with self.subTest(platform=platform, size=size, private=private):
                code, result = run_route(route='linux', repository={
                    'private': private,
                    'custom_properties': {'ci-platform': platform, 'ci-size': size},
                })
                self.assertEqual(code, 0, result)
                self.assertEqual(result['output']['platform'], platform)
                self.assertEqual(result['output']['ci-image'], str(private).lower())
                expected = [f'ci-{ID}-org-ops-{size}'] if private else ['ubuntu-latest']
                self.assertEqual(json.loads(result['output']['runner']), expected)

    def test_macos_linux_fallbacks_keep_existing_admission_and_hosted_modes(self):
        for overrides in ({'admitted': []}, {'runners': {}},
                          {'runners': {'medium': ['ubuntu-latest']}}):
            with self.subTest(overrides=overrides):
                code, result = run_route(route='linux', repository={
                    'custom_properties': {'ci-platform': 'macos', 'ci-size': 'medium'},
                }, **overrides)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['output']['platform'], 'macos')
                self.assertEqual(result['output']['ci-image'], 'false')
                expected = ['linux-x64-large'] if 'admitted' in overrides else ['ubuntu-latest']
                self.assertEqual(json.loads(result['output']['runner']), expected)

    def test_only_macos_runs_native_checks_without_docker_or_pr_checks(self):
        native = self.jobs['standards-native']
        self.assertEqual(native['needs'], 'route')
        self.assertEqual(native['if'],
                         "github.repository != 'Rubio-Enterprises/.github' && needs.route.outputs.platform == 'macos'")
        self.assertEqual(native['runs-on'],
                         '${{ fromJSON(vars.RUNNERS || \'{}\').macos || fromJSON(\'["macos-26"]\') }}')
        self.assertEqual(native['permissions'], {'contents': 'read', 'packages': 'read'})
        self.assertEqual([step['run'] for step in native['steps'] if 'run' in step],
                         ['just setup-native', 'just check-native'])
        self.assertEqual(native['steps'][1]['uses'],
                         'Rubio-Enterprises/.github/actions/setup-just@main')
        self.assertFalse(native['steps'][0]['with']['persist-credentials'])
        setup = native['steps'][2]
        self.assertEqual(setup['env']['NODE_AUTH_TOKEN'], '${{ secrets.GITHUB_TOKEN }}')
        self.assertNotIn('docker', json.dumps(native).lower())
        for name, job in self.jobs.items():
            if name != 'standards-linux':
                self.assertNotIn('check-pr', json.dumps(job))

    def test_one_required_context_aggregates_all_applicable_legs_fail_closed(self):
        aggregate = self.jobs['standards']
        self.assertEqual([name for name, job in self.jobs.items()
                          if job.get('name', name) == 'standards'], ['standards'])
        self.assertEqual(aggregate['needs'], ['route', 'standards-linux', 'standards-native'])
        self.assertEqual(aggregate['if'],
                         "${{ always() && github.repository != 'Rubio-Enterprises/.github' }}")
        self.assertEqual(aggregate['permissions'], {})
        step = aggregate['steps'][0]
        self.assertEqual(step['env'], {
            'ROUTE_RESULT': '${{ needs.route.result }}',
            'PLATFORM': '${{ needs.route.outputs.platform }}',
            'LINUX_RESULT': '${{ needs.standards-linux.result }}',
            'NATIVE_RESULT': '${{ needs.standards-native.result }}',
        })
        for platform, route, linux, native in product(
                ('linux', 'macos', ''), *([('success', 'failure', 'cancelled', 'skipped')] * 3)):
            with self.subTest(platform=platform, route=route, linux=linux, native=native):
                result = subprocess.run(['bash', '-euc', step['run']], capture_output=True,
                                        env={**os.environ, 'PLATFORM': platform, 'ROUTE_RESULT': route,
                                             'LINUX_RESULT': linux, 'NATIVE_RESULT': native})
                expected = (route == linux == 'success'
                            and ((platform == 'macos' and native == 'success')
                                 or (platform == 'linux' and native == 'skipped')))
                self.assertEqual(result.returncode == 0, expected)

    def test_hosted_macos_runs_only_image_checks_and_linux_keeps_just_check(self):
        step = next(step for step in self.jobs['standards-linux']['steps']
                    if step.get('id') == 'check-hosted')
        self.assertEqual(step['env']['CI_PLATFORM'], '${{ needs.route.outputs.platform }}')
        for platform, status in product(('linux', 'macos'), (0, 7)):
            with self.subTest(platform=platform, status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                just = root / 'just'
                just.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >"$CALLS"\nexit ' + str(status) + '\n')
                just.chmod(0o755)
                result = subprocess.run(['bash', '-euc', step['run']], capture_output=True,
                                        env={**os.environ, 'CI_PLATFORM': platform, 'CALLS': str(root / 'calls'),
                                             'PATH': str(root) + ':' + os.environ['PATH']})
                self.assertEqual(result.returncode, status)
                expected = 'exec standards check' if platform == 'macos' else 'check'
                self.assertEqual((root / 'calls').read_text().strip(), expected)

    def test_ci_image_and_hosted_legs_run_standards_checks(self):
        linux = json.dumps(self.jobs['standards-linux'])
        for command in ('standards check', 'standards check-pr', 'just build',
                        'just check', 'just check-pr || status=$?'):
            self.assertIn(command, linux)
        self.assertIn("steps['check-ci'].outcome != 'skipped'", linux)
        self.assertIn("steps['check-hosted'].outcome != 'skipped'", linux)
        self.assertIn('!cancelled()', linux)
        self.assertNotIn('jdx/mise-action@', WORKFLOW)
        self.assertNotIn('astral-sh/setup-uv@', WORKFLOW)

    def test_repository_hygiene_and_privileges_remain(self):
        self.assertIn('fetch-depth: 0', WORKFLOW)
        self.assertIn('persist-credentials: false', WORKFLOW)
        self.assertIn('packages: read', WORKFLOW)
        self.assertNotIn('STANDARDS_PIN', WORKFLOW)
        self.assertNotIn('devcontainer exec', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
