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

    def test_ci_image_and_hosted_legs_run_standards_checks(self):
        self.assertIn("run: standards check", WORKFLOW)
        self.assertIn("run: standards check-pr", WORKFLOW)
        self.assertIn("run: just build", WORKFLOW)
        self.assertIn("run: just check", WORKFLOW)
        self.assertIn("just check-pr || status=$?", WORKFLOW)
        self.assertNotIn("jdx/mise-action@", WORKFLOW)
        self.assertNotIn("astral-sh/setup-uv@", WORKFLOW)

    def test_repository_hygiene_and_privileges_remain(self):
        self.assertIn('fetch-depth: 0', WORKFLOW)
        self.assertIn('persist-credentials: false', WORKFLOW)
        self.assertIn('packages: read', WORKFLOW)
        self.assertNotIn('STANDARDS_PIN', WORKFLOW)
        self.assertNotIn('devcontainer exec', WORKFLOW)


if __name__ == '__main__':
    unittest.main()
