from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/project-route.yml').read_text()
SCRIPT = textwrap.dedent(WORKFLOW.split('          script: |\n', 1)[1])
ID = 1277378935
RUNNERS = {
    'small': ['linux-x64-small'],
    'medium': ['linux-x64-medium'],
    'docker': ['linux-x64-large'],
    'kvm': ['linux-x64-large'],
    'arm': ['linux-arm64'],
    'macos': ['macos-arm64'],
    'vrt': ['linux-x64-medium'],
}
HARNESS = '''
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = {};
const calls = [];
const core = { setOutput: (key, value) => { output[key] = value; }, info: () => {} };
const github = { request: async (path, args) => {
  calls.push({path, args});
  if (input.apiError) throw new Error('metadata API unavailable');
  return {data: input.properties};
} };
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
(async () => {
  try {
    await new AsyncFunction('context', 'github', 'core', input.script)(input.context, github, core);
    process.stdout.write(JSON.stringify({output, calls}));
  } catch (error) {
    process.stdout.write(JSON.stringify({output, calls, error: error.message}));
    process.exitCode = 1;
  }
})();
'''


def run_route(*, route='auto', runners=None, admitted=None, repository=None,
              name='org-ops', owner='Rubio-Enterprises', properties=None, api_error=False):
    repo = {'id': ID, 'private': True, 'custom_properties': {
        'ci-platform': 'linux', 'ci-size': 'small',
    }}
    if repository is not None:
        repo.update(repository)
    payload = {
        'script': SCRIPT,
        'context': {'repo': {'owner': owner, 'repo': name}, 'payload': {'repository': repo}},
        'properties': properties if properties is not None else [
            {'property_name': 'ci-platform', 'value': 'linux'},
            {'property_name': 'ci-size', 'value': 'small'},
        ],
        'apiError': api_error,
    }
    result = subprocess.run(
        ['node', '-e', HARNESS], input=json.dumps(payload), text=True, capture_output=True,
        check=False, env={**os.environ, 'ROUTE': route,
                          'RUNNERS': json.dumps(RUNNERS if runners is None else runners),
                          'CI_IMAGE_REPOS': json.dumps([ID] if admitted is None else admitted)},
    )
    return result.returncode, json.loads(result.stdout)


class ProjectRouteTests(unittest.TestCase):
    def assert_route(self, expected, *, image=False, **kwargs):
        code, result = run_route(**kwargs)
        self.assertEqual(code, 0, result)
        self.assertEqual(json.loads(result['output']['runner']), expected)
        self.assertEqual(result['output']['ci-image'], str(image).lower())
        return result

    def test_both_image_classes_match_the_inventory_labels(self):
        for size in ('small', 'medium'):
            with self.subTest(size=size):
                result = self.assert_route([f'ci-{ID}-org-ops-{size}'], image=True, route=size)
                self.assertEqual(result['calls'], [])

    def test_event_properties_select_medium_without_a_metadata_call(self):
        result = self.assert_route([f'ci-{ID}-org-ops-medium'], image=True, repository={
            'custom_properties': {'ci-platform': 'linux', 'ci-size': 'medium'},
        })
        self.assertEqual(result['calls'], [])

    def test_unadmitted_repository_retains_the_plain_route(self):
        self.assert_route(['linux-x64-small'], admitted=[])
        self.assert_route(['linux-x64-small'], admitted=[ID + 1])

    def test_hidden_runners_keeps_each_hosted_fallback_even_if_admitted(self):
        for route, expected in {
            'small': 'ubuntu-slim', 'medium': 'ubuntu-latest', 'docker': 'ubuntu-latest',
            'kvm': 'ubuntu-latest', 'arm': 'ubuntu-24.04-arm', 'macos': 'macos-26',
            'vrt': 'linux-x64-medium',
        }.items():
            with self.subTest(route=route):
                self.assert_route([expected], route=route, runners={}, repository={'private': False})

    def test_owner_hosted_modes_and_custom_constraints_are_not_overridden(self):
        for labels in (['ubuntu-latest'], ['self-hosted', 'linux-x64-small', 'custom']):
            with self.subTest(labels=labels):
                self.assert_route(labels, runners={'small': labels})

    def test_capability_routes_never_select_a_ci_image(self):
        for route in ('docker', 'kvm', 'arm', 'macos', 'vrt'):
            with self.subTest(route=route):
                self.assert_route(RUNNERS[route], route=route)

    def test_auto_macos_wins_over_the_size(self):
        self.assert_route(['macos-arm64'], repository={
            'custom_properties': {'ci-platform': 'macos', 'ci-size': 'medium'},
        })

    def test_missing_or_partial_event_properties_use_the_base_repository_api(self):
        for properties in (None, {}, {'ci-platform': 'linux'}):
            with self.subTest(properties=properties):
                result = self.assert_route([f'ci-{ID}-org-ops-medium'], image=True,
                                          repository={'custom_properties': properties}, properties=[
                                              {'property_name': 'ci-platform', 'value': 'linux'},
                                              {'property_name': 'ci-size', 'value': 'medium'},
                                          ])
                self.assertEqual(result['calls'], [{
                    'path': 'GET /repos/{owner}/{repo}/properties/values',
                    'args': {'owner': 'Rubio-Enterprises', 'repo': 'org-ops'},
                }])

    def test_schema_defaults_apply_when_no_values_are_returned(self):
        self.assert_route([f'ci-{ID}-org-ops-small'], image=True,
                          repository={'custom_properties': None}, properties=[])

    def test_metadata_failure_does_not_fall_back_to_small(self):
        code, result = run_route(repository={'custom_properties': None}, api_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['output'], {})
        self.assertIn('metadata API unavailable', result['error'])

    def test_inventory_slug_sanitization_and_truncation_order(self):
        for name, slug in {
            'Grocy-SwiftUI': 'grocy-swiftui', '.github': 'github',
            '__A..B__': 'a--b', 'a' * 39 + '.tail': 'a' * 39 + '-',
        }.items():
            with self.subTest(name=name):
                self.assert_route([f'ci-{ID}-{slug}-small'], image=True, name=name)

    def test_public_repositories_and_other_organizations_never_enter_image_pools(self):
        self.assert_route(['linux-x64-small'], repository={'private': False})
        self.assert_route(['linux-x64-small'], owner='other-org')

    def test_invalid_declarations_fail_before_emitting_a_route(self):
        for kwargs in (
            {'runners': []}, {'runners': {'small': []}}, {'runners': {'small': 'ubuntu-latest'}},
            {'runners': {'small': [42]}}, {'runners': {'small': ['  ']}},
            {'admitted': {}}, {'admitted': [str(ID)]}, {'admitted': [True]},
            {'admitted': [0]}, {'admitted': [-1]}, {'admitted': [1.5]},
            {'repository': {'id': None}}, {'repository': {'id': '1277378935'}},
            {'route': 'large'}, {'route': '__proto__'}, {'name': '___'},
            {'repository': {'custom_properties': {'ci-platform': 'arm', 'ci-size': 'small'}}},
            {'repository': {'custom_properties': {'ci-platform': 'linux', 'ci-size': 'large'}}},
        ):
            with self.subTest(kwargs=kwargs):
                code, result = run_route(**kwargs)
                self.assertEqual(code, 1, result)
                self.assertEqual(result['output'], {})

    def test_workflow_is_route_only_and_uses_no_inherited_secret_or_project_code(self):
        self.assertIn('workflow_call:', WORKFLOW)
        self.assertIn('contents: read', WORKFLOW)
        self.assertNotIn('secrets:', WORKFLOW)
        self.assertNotIn('checkout@', WORKFLOW)
        self.assertNotIn('run: mise', WORKFLOW)
        self.assertNotIn('${{', SCRIPT)
        self.assertIn("value: ${{ jobs.route.outputs.runner }}", WORKFLOW)
        self.assertIn("value: ${{ jobs.route.outputs.ci-image }}", WORKFLOW)
        self.assertIn("fromJSON(vars.RUNNERS || '{}').small", WORKFLOW)


if __name__ == '__main__':
    unittest.main()
