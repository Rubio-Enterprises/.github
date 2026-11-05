from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / ".github/workflows/app-image-pipeline.yml").read_text()
APP_IMAGE = (ROOT / ".github/workflows/app-image.yml").read_text()
JOBS = ["changes", "build", "build-arm64", "structure-arm64", "smoke", "test", "promote"]


def job(name: str, text: str = PIPELINE) -> str:
    """The text of one top-level job, from its key to the next job (or the end)."""
    body = text.split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [a-z0-9-]+:\n", body, maxsplit=1)[0]


def declared_inputs(text: str) -> list[str]:
    section = text.split("    inputs:\n", 1)[1].split("    outputs:\n", 1)[0].split("\npermissions:", 1)[0]
    return re.findall(r"^      ([a-z-]+):\n", section, flags=re.M)


class Shape(unittest.TestCase):
    def test_the_workflow_has_exactly_the_jobs_the_pipeline_chains(self):
        self.assertEqual(re.findall(r"^  ([a-z0-9-]+):\n", PIPELINE.split("\njobs:\n", 1)[1], flags=re.M), JOBS)

    def test_no_job_runs_with_more_than_it_needs_and_only_two_hold_the_oidc_token(self):
        self.assertIn("permissions: {}", PIPELINE)
        self.assertEqual(PIPELINE.count("id-token: write"), 2)
        self.assertIn("id-token: write", job("build"))
        self.assertIn("id-token: write", job("promote"))
        # Repository code runs only in the test job, and there it holds read access only
        self.assertEqual(re.search(r"permissions:\n((?:      .*\n)+)", job("test") + "\n").group(1), "      contents: read\n      packages: read\n")
        self.assertEqual(re.search(r"permissions:\n((?:      .*\n)+)", job("smoke") + "\n").group(1), "      contents: read\n      packages: read\n")
        self.assertNotIn("packages: write", job("test") + job("smoke") + job("changes") + job("structure-arm64"))
        # The arm64 build has no Build Service, so no certificate
        self.assertNotIn("id-token", job("build-arm64"))
        self.assertEqual(re.search(r"permissions:\n((?:      .*\n)+)", job("build-arm64") + "\n").group(1), "      contents: read\n      packages: write\n")

    def test_repository_tasks_run_only_in_the_read_only_test_job(self):
        for name in JOBS:
            if name != "test":
                self.assertNotIn("mise ", job(name), name)
                self.assertNotIn("mise-action", job(name), name)
        test = job("test")
        self.assertNotIn("mise-action", test)
        self.assertIn("run: just build && just setup", test)
        self.assertIn('IMAGE_NAME="${IMAGE%@*}" just test-image', test)
        self.assertLess(test.index("operation: pull"), test.index("just setup"))

    def test_the_smoke_job_checks_out_only_to_mount_listed_files_and_runs_nothing_from_it(self):
        smoke = job("smoke")
        checkout = smoke.split("- name: Start the candidate", 1)[0]
        self.assertIn("if: ${{ inputs.smoke-files != '' }}", checkout)
        self.assertIn("persist-credentials: false", checkout)
        self.assertNotIn("submodules", checkout)
        self.assertIn("SMOKE_FILES: ${{ inputs.smoke-files }}", smoke)
        # still no repository code: no mise, no run steps, only the central action
        self.assertNotIn("mise", smoke)
        self.assertNotIn("run:", smoke)
        self.assertEqual(smoke.count("uses: Rubio-Enterprises/.github/actions/app-image@main"), 1)

    def test_promotion_waits_for_every_tier_and_only_a_publishing_run_promotes(self):
        promote = job("promote")
        self.assertIn("needs: [changes, build, build-arm64, structure-arm64, smoke, test]", promote)
        self.assertIn("inputs.publish && !cancelled() && !failure() && needs.changes.outputs.run == 'true'", promote)
        self.assertIn("uses: Rubio-Enterprises/.github/.github/workflows/app-image.yml@main", promote)
        self.assertIn("promote: true", promote)
        self.assertIn("candidate-image: ${{ needs.build.outputs.image || needs.build-arm64.outputs.image }}", promote)
        self.assertNotIn("candidate: true", promote)
        for tier in ("smoke", "test"):
            self.assertIn("needs: [changes, build, build-arm64, structure-arm64]", job(tier))

    def test_the_candidate_is_built_once_and_attested_only_when_it_will_be_published(self):
        build = job("build")
        self.assertIn("uses: Rubio-Enterprises/.github/.github/workflows/app-image.yml@main", build)
        self.assertIn("candidate: true", build)
        self.assertIn("attestations: ${{ inputs.publish }}", build)
        self.assertNotIn("publish:", build.replace("inputs.publish", ""))  # the build never publishes
        self.assertIn("server: ${{ inputs.smoke-command == '' }}", build)
        self.assertIn("provenance: ${{ inputs.publish && 'mode=max' || 'false' }}", job("build-arm64"))
        self.assertIn("sbom: ${{ inputs.publish }}", job("build-arm64"))

    def test_the_image_jobs_wait_for_the_change_check(self):
        for name in ("build", "build-arm64"):
            self.assertIn("needs: changes", job(name))
            self.assertIn("needs.changes.outputs.run == 'true'", job(name))
        self.assertIn("inputs.arch == 'amd64'", job("build"))
        self.assertIn("inputs.arch == 'arm64'", job("build-arm64"))
        for name in ("smoke", "test", "promote"):
            self.assertIn("needs.changes.outputs.run == 'true'", job(name))

    def test_run_jobs_use_the_docker_route_or_the_arm_route_by_architecture(self):
        route = ("runs-on: ${{ inputs.arch == 'arm64' && (fromJSON(vars.RUNNERS || '{}').arm || fromJSON('[\"ubuntu-24.04-arm\"]')) "
                 "|| (fromJSON(vars.RUNNERS || '{}').docker || fromJSON('[\"ubuntu-latest\"]')) }}")
        for name in ("smoke", "test"):
            self.assertIn(route, job(name))
        self.assertIn("runs-on: ${{ fromJSON(vars.RUNNERS || '{}').arm || fromJSON('[\"ubuntu-24.04-arm\"]') }}", job("build-arm64"))
        for name in ("changes", "structure-arm64"):
            self.assertIn("fromJSON(vars.RUNNERS || '{}').small || fromJSON('[\"ubuntu-slim\"]')", job(name))
        self.assertNotIn("QEMU", PIPELINE.upper().replace("NO EMULATION", ""))
        self.assertNotIn("setup-qemu", PIPELINE)

    def test_the_arm64_structure_checks_read_the_candidate_for_its_platform(self):
        structure = job("structure-arm64")
        self.assertIn("PLATFORM: linux/arm64", structure)
        self.assertIn("REF: ${{ needs.build-arm64.outputs.image }}", structure)
        self.assertIn("operation: structure", structure)
        self.assertNotIn("mise", structure)

    def test_third_party_actions_are_pinned_and_first_party_ones_follow_main(self):
        for ref in re.findall(r"uses: (\S+)", PIPELINE):
            if ref.startswith("Rubio-Enterprises/.github/"):
                self.assertRegex(ref, r"^Rubio-Enterprises/\.github/(actions/(app-image|build-service|setup-just)|\.github/workflows/app-image\.yml)@main$")
            else:
                self.assertRegex(ref, r"@[a-f0-9]{40}$")

    def test_caller_text_reaches_scripts_only_through_the_environment(self):
        for run in re.findall(r"\n        run: \|\n((?:          .*\n|\n)+)", PIPELINE) + re.findall(r"\n        run: (.*)\n", PIPELINE):
            self.assertNotIn("${{", run)
        for forbidden in ("pull_request_target", "workflow_run", "secrets:", "continue-on-error", "docker/setup-qemu-action", "type=gha"):
            self.assertNotIn(forbidden, PIPELINE)

    def test_every_input_is_used_and_every_forwarded_input_exists_in_the_builder(self):
        for name in declared_inputs(PIPELINE):
            self.assertIn(f"inputs.{name}", PIPELINE.split("\njobs:\n", 1)[1], name)
        builder = declared_inputs(APP_IMAGE)
        for call in ("build", "promote"):
            section = job(call).split("    with:\n", 1)[1]
            for name in re.findall(r"^      ([a-z-]+): ", section, flags=re.M):
                self.assertIn(name, builder, f"{call} passes {name}, which app-image.yml does not declare")

    def test_the_candidate_tag_matches_the_shape_prune_deletes(self):
        # prune.sh deletes only candidate-<run>-<attempt>-<12 hex digits>; the arm64 build makes its tag in that shape
        self.assertIn("candidate-%s-%s-%s", job("build-arm64"))
        self.assertIn("od -An -N6 -tx1 /dev/urandom", job("build-arm64"))

    def test_the_arm64_image_name_is_held_to_the_repository_prefix(self):
        self.assertIn('[[ "$package" != "$repo" && "$package" != "$repo/"* ]]', job("build-arm64"))


class Tags(unittest.TestCase):
    def test_every_metadata_step_turns_the_automatic_latest_tag_off(self):
        # docker/metadata-action adds latest to every semver release by default; App Images publish none
        self.assertEqual(PIPELINE.count("docker/metadata-action@"), 1)
        self.assertEqual(APP_IMAGE.count("docker/metadata-action@"), 2)
        self.assertEqual(PIPELINE.count("flavor: |\n            latest=false\n            ${{ inputs.flavor }}"), 1)
        self.assertEqual(APP_IMAGE.count("flavor: |\n            latest=false\n            ${{ inputs.flavor }}"), 2)


class Builder(unittest.TestCase):
    def test_the_promote_job_has_no_builder_certificate_or_repository_code(self):
        promote = APP_IMAGE.split("\n  promote:\n", 1)[1]
        self.assertNotIn("id-token", promote)
        self.assertEqual(re.search(r"permissions:\n((?:      .*\n)+)", promote).group(1), "      contents: read\n      packages: write\n")
        for forbidden in ("operation: connect", "operation: build", "mise", "npm ", "pnpm", "./", "BUILD_SERVICE", "docker/build-push-action"):
            self.assertNotIn(forbidden, promote)
        self.assertIn("fromJSON(vars.RUNNERS || '{}').small", promote)
        self.assertIn("operation: promote", promote)
        # the checkout, when there is one, is data for docker/metadata-action only
        self.assertIn("if: inputs.ref != '' && inputs.ref != github.ref && inputs.ref != github.sha", promote)
        self.assertEqual(promote.count("github.token"), 1)

    def test_the_builder_does_not_build_when_promoting_and_the_other_way_round(self):
        self.assertIn("if: ${{ !cancelled() && !failure() && !inputs.promote }}", APP_IMAGE)
        self.assertIn("    if: inputs.promote\n", APP_IMAGE)
        self.assertNotIn("standards-install", APP_IMAGE)

    def test_the_builders_outputs_come_from_whichever_job_ran(self):
        for output in ("digest", "image", "version"):
            self.assertIn(f"value: ${{{{ jobs.build.outputs.{output} || jobs.promote.outputs.{output} }}}}", APP_IMAGE)
        self.assertIn("value: ${{ jobs.promote.outputs.tags }}", APP_IMAGE)

    def test_promotion_requires_a_resolved_candidate_and_excludes_the_other_modes(self):
        promote = APP_IMAGE.split("\n  promote:\n", 1)[1]
        self.assertIn("promote is exclusive with candidate", promote)
        self.assertIn("promote needs the tags input", promote)
        self.assertIn("the output it reads did not resolve", promote)


if __name__ == "__main__":
    unittest.main()
