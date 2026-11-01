"""The injected standards job restores exactly what standards-cache.yml saves on the default branch."""

from pathlib import Path
import re
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
        self.assertEqual(steps(INJECTED, 'jdx/mise-action')[0]['cache'], 'false')

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
        self.assertRegex(WARMER, r'"on":\n  workflow_call:\n\n')
        self.assertIn('permissions:\n  contents: read\n  packages: read', WARMER)

    def test_the_trivy_database_is_used_as_restored_only_when_fresh(self):
        step = extract_step(INJECTED, 'Use the restored trivy database without updating it')
        self.assertIn("if: steps.trivy-db.outputs.cache-matched-key != ''", step)
        self.assertIn('TRIVY_SKIP_DB_UPDATE=true', step)
        self.assertIn('"$days" -lt 8', step)
        self.assertLess(
            INJECTED.index('Use the restored trivy database without updating it'),
            INJECTED.index('name: Every Check on every file'),
        )

    def test_dangers_npm_ci_reads_the_restored_npm_cache(self):
        self.assertIn('run: npm_config_cache="$HOME/.cache/standards-npm" mise run check:pr', INJECTED)


if __name__ == '__main__':
    unittest.main()
