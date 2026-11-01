from pathlib import Path
import unittest

from test_ruby_workflows import extract_step

WORKFLOW = (Path(__file__).resolve().parents[1] / '.github/workflows/standards.yml').read_text()


class StandardsRouteTests(unittest.TestCase):
    def test_injected_workflow_consumes_the_central_router(self):
        self.assertIn('uses: Rubio-Enterprises/.github/.github/workflows/project-route.yml@main', WORKFLOW)
        self.assertIn('    name: standards\n', WORKFLOW)
        self.assertIn('    needs: route\n', WORKFLOW)
        self.assertIn("needs.route.result == 'success' && fromJSON(needs.route.outputs.runner)", WORKFLOW)
        self.assertEqual(WORKFLOW.count("github.repository != 'Rubio-Enterprises/.github'"), 2)
        self.assertIn("always() && github.repository != 'Rubio-Enterprises/.github'", WORKFLOW)
        self.assertNotIn('secrets: inherit', WORKFLOW)

    def test_route_failure_cannot_skip_a_green_required_context(self):
        guard = extract_step(WORKFLOW, 'Require successful project routing')
        self.assertIn("if: needs.route.result != 'success'", guard)
        self.assertIn('exit 1', guard)
        self.assertLess(WORKFLOW.index('Require successful project routing'), WORKFLOW.index('actions/checkout@'))

    def test_image_mode_does_not_restore_plain_mise_data_or_bootstrap_tools(self):
        for action in ('astral-sh/setup-uv@', 'jdx/mise-action@'):
            step = WORKFLOW.split('- uses: ' + action, 1)[1].split('\n      - ', 1)[0]
            self.assertIn("if: needs.route.outputs.ci-image != 'true'", step)
        self.assertIn('run: mise trust "$GITHUB_WORKSPACE"', WORKFLOW)
        self.assertLess(WORKFLOW.index('mise trust'), WORKFLOW.index('run: mise run setup'))
        setup = WORKFLOW.split('      - id: setup\n', 1)[1].split('\n      - ', 1)[0]
        self.assertIn('run: mise run setup', setup)
        self.assertIn('NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}', setup)
        self.assertIn('MISE_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}', setup)

    def test_all_project_checks_and_the_package_task_origin_guard_remain(self):
        for command in ('run: mise run setup', 'run: mise run check\n', 'run: mise run check:pr'):
            self.assertIn(command, WORKFLOW)
        self.assertIn('for task in check check:pr; do', WORKFLOW)
        self.assertIn('fetch-depth: 0', WORKFLOW)
        self.assertIn('persist-credentials: false', WORKFLOW)
        self.assertIn('packages: read', WORKFLOW)
        self.assertNotIn('STANDARDS_PIN', WORKFLOW)
        self.assertNotIn('devcontainer exec', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
