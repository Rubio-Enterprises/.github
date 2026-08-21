"""Behavioral contract tests for the organization Renovate preset."""

from __future__ import annotations

import json
import re
import unittest
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "default.json").read_text(encoding="utf-8"))

REGISTRY_FIXTURE = """\
[tools.pypi-only]
description = "A PyPI dependency that the custom manager must ignore"
version = "9.9.9"

[tools.sha-tool]
description = "A SHA-pinned first-party tool"
version = "0123456789abcdef0123456789abcdef01234567"  # v1.2.3
source = "git+ssh://git@github.com/Rubio-Enterprises/sha-tool"
personal_only = true

[tools.tag-tool]
description = "A tag-only pin that pinDigests must bootstrap"
version = "v2.3.4"
source = "git+ssh://git@github.com/Rubio-Enterprises/tag-tool"

[tools.after]
description = "A following table that no match may consume"
version = "1.0.0"
"""

DOCKERFILE_FIXTURE = """\
FROM node:22-trixie-slim@sha256:db8a96a
# renovate: datasource=deb depName=tailscale packageName=tailscale versioning=deb
ARG TAILSCALE_VERSION=1.102.2
USER dev
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
# renovate: datasource=npm depName=@anthropic-ai/claude-code
RUN npm install -g @anthropic-ai/claude-code@2.1.226 \\
    && npm cache clean --force
"""

# An unannotated global install, and a ${VAR} indirection, must both stay unmatched.
DOCKERFILE_DECOYS = """\
RUN npm install -g @anthropic-ai/claude-code@9.9.9
# renovate: datasource=npm depName=@anthropic-ai/claude-code
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"
"""

AGENT_SANDBOX_DEPS = [
    "kubernetes-sigs/agent-sandbox",
    "registry.k8s.io/agent-sandbox/agent-sandbox-controller",
]
CLAUDE_CODE_DEP = "@anthropic-ai/claude-code"
# Renovate ships flux scoped to the Flux distribution's own manifest, and
# managerFilePatterns REPLACES that default rather than extending it.
FLUX_DEFAULT_PATTERN = "/(?:^|/)gotk-components\\.ya?ml$/"


def _selects_file(rule: dict[str, Any], path: str) -> bool:
    """Mirror matchFileNames for the `**/<basename>` globs this preset uses.

    Every glob here is anchored as `**/<basename-pattern>`, and minimatch's `**/`
    matches zero or more leading segments — so the decision reduces exactly to a
    basename match at any depth, root included. Renovate's own engine is the
    authority; this keeps the contract honest without reimplementing minimatch.
    """
    basenames = []
    for glob in rule["matchFileNames"]:
        assert glob.startswith("**/"), f"unexpected glob shape: {glob}"
        basenames.append(glob.removeprefix("**/"))
    return any(fnmatch(PurePosixPath(path).name, pattern) for pattern in basenames)


def _python_regex(pattern: str) -> re.Pattern[str]:
    """Compile a Renovate RE2 pattern with Python's named-group spelling."""
    return re.compile(pattern.replace("(?<", "(?P<"))


def _rule_with_group(group_name: str) -> dict[str, Any]:
    matches = [
        rule
        for rule in CONFIG["packageRules"]
        if rule.get("groupName") == group_name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {group_name!r} rule, found {len(matches)}")
    return matches[0]


def _selector_matches(selectors: list[str], value: str) -> bool:
    for selector in selectors:
        if selector.startswith("/") and selector.endswith("/"):
            if re.search(selector[1:-1], value):
                return True
        elif selector == value:
            return True
    return False


def _rule_matches(
    rule: dict[str, Any],
    dependency: dict[str, str],
    resolved: dict[str, Any],
) -> bool:
    selector_fields = {
        "matchManagers": "manager",
        "matchUpdateTypes": "updateType",
        "matchDepNames": "depName",
        "matchPackageNames": "packageName",
        "matchFileNames": "fileName",
        "matchDatasources": "datasource",
    }
    for rule_field, dependency_field in selector_fields.items():
        if rule_field not in rule:
            continue
        value = dependency.get(dependency_field)
        if value is None or not _selector_matches(rule[rule_field], value):
            return False

    current_version = rule.get("matchCurrentVersion")
    if current_version is not None:
        negate = current_version.startswith("!")
        pattern = current_version[2:-1] if negate else current_version[1:-1]
        matched = re.search(pattern, dependency["currentVersion"]) is not None
        if matched == negate:
            return False

    for expression in rule.get("matchJsonata", []):
        if expression != "$exists(groupName) = false":
            raise AssertionError(f"unsupported test fixture JSONata: {expression}")
        if "groupName" in resolved:
            return False
    return True


def _resolve_dependency(
    dependency: dict[str, str],
    initial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "automerge": False,
        "dependencyDashboardApproval": False,
        "platformAutomerge": False,
        "minimumReleaseAge": CONFIG["minimumReleaseAge"],
    }
    if initial is not None:
        resolved.update(initial)
    resolved_fields = {
        "automerge",
        "dependencyDashboardApproval",
        "enabled",
        "groupName",
        "groupSlug",
        "minimumReleaseAge",
        "platformAutomerge",
    }
    for rule in CONFIG["packageRules"]:
        if _rule_matches(rule, dependency, resolved):
            resolved.update(
                {key: value for key, value in rule.items() if key in resolved_fields}
            )
    return resolved


class RenovateConfigContractTests(unittest.TestCase):
    def test_global_backlog_posture_is_exact(self) -> None:
        self.assertTrue(CONFIG["dependencyDashboard"])
        self.assertNotIn(":disableDependencyDashboard", CONFIG["extends"])
        self.assertEqual(CONFIG["commitHourlyLimit"], 1)
        self.assertEqual(CONFIG["prHourlyLimit"], 1)
        self.assertEqual(CONFIG["prConcurrentLimit"], 5)
        self.assertEqual(CONFIG["branchConcurrentLimit"], 5)
        self.assertEqual(CONFIG["rebaseWhen"], "automerging")
        self.assertEqual(CONFIG["minimumReleaseAge"], "7 days")

    def test_stable_and_pre_one_groups_resolve_to_distinct_branches(self) -> None:
        fixtures = [
            (
                "stable npm",
                {
                    "manager": "npm",
                    "depName": "stable-npm",
                    "packageName": "stable-npm",
                    "fileName": "package.json",
                    "currentVersion": "1.2.3",
                    "updateType": "patch",
                },
                "stable non-major npm dependencies",
                "npm-stable-non-major",
                True,
            ),
            (
                "plain pre-1.0 npm",
                {
                    "manager": "npm",
                    "depName": "zero-npm",
                    "packageName": "zero-npm",
                    "fileName": "package.json",
                    "currentVersion": "0.8.0",
                    "updateType": "minor",
                },
                "pre-1.0 npm dependencies",
                "npm-pre-1-0",
                False,
            ),
            (
                "v-prefixed pre-1.0 npm",
                {
                    "manager": "npm",
                    "depName": "v-zero-npm",
                    "packageName": "v-zero-npm",
                    "fileName": "package.json",
                    "currentVersion": "v0.8.0",
                    "updateType": "patch",
                },
                "pre-1.0 npm dependencies",
                "npm-pre-1-0",
                False,
            ),
            (
                "stable Cargo",
                {
                    "manager": "cargo",
                    "depName": "stable-cargo",
                    "packageName": "stable-cargo",
                    "fileName": "Cargo.toml",
                    "currentVersion": "2.1.0",
                    "updateType": "minor",
                },
                "stable non-major cargo dependencies",
                "cargo-stable-non-major",
                True,
            ),
            (
                "plain pre-1.0 Cargo",
                {
                    "manager": "cargo",
                    "depName": "zero-cargo",
                    "packageName": "zero-cargo",
                    "fileName": "Cargo.toml",
                    "currentVersion": "0.4.0",
                    "updateType": "patch",
                },
                "pre-1.0 cargo dependencies",
                "cargo-pre-1-0",
                False,
            ),
            (
                "v-prefixed pre-1.0 Cargo",
                {
                    "manager": "cargo",
                    "depName": "v-zero-cargo",
                    "packageName": "v-zero-cargo",
                    "fileName": "Cargo.toml",
                    "currentVersion": "v0.4.0",
                    "updateType": "minor",
                },
                "pre-1.0 cargo dependencies",
                "cargo-pre-1-0",
                False,
            ),
            (
                "stable pin",
                {
                    "manager": "pep621",
                    "depName": "stable-pin",
                    "packageName": "stable-pin",
                    "fileName": "pyproject.toml",
                    "currentVersion": "3.0.0",
                    "updateType": "pin",
                },
                "stable dependency pins",
                "stable-dependency-pins",
                True,
            ),
            (
                "plain pre-1.0 pin",
                {
                    "manager": "pep621",
                    "depName": "zero-pin",
                    "packageName": "zero-pin",
                    "fileName": "pyproject.toml",
                    "currentVersion": "0.12.0",
                    "updateType": "pin",
                },
                "pre-1.0 dependency pins",
                "pre-1-0-dependency-pins",
                False,
            ),
            (
                "v-prefixed pre-1.0 pin",
                {
                    "manager": "pep621",
                    "depName": "v-zero-pin",
                    "packageName": "v-zero-pin",
                    "fileName": "pyproject.toml",
                    "currentVersion": "v0.12.0",
                    "updateType": "pin",
                },
                "pre-1.0 dependency pins",
                "pre-1-0-dependency-pins",
                False,
            ),
        ]

        slugs_by_group: dict[str, str] = {}
        for fixture_name, dependency, expected_group, expected_slug, should_merge in fixtures:
            with self.subTest(fixture=fixture_name):
                resolved = _resolve_dependency(dependency)
                self.assertEqual(resolved["groupName"], expected_group)
                self.assertEqual(resolved["groupSlug"], expected_slug)
                self.assertEqual(resolved["automerge"], should_merge)
                self.assertEqual(resolved["platformAutomerge"], should_merge)
                self.assertEqual(resolved["minimumReleaseAge"], "7 days")
                self.assertEqual(
                    resolved["dependencyDashboardApproval"], not should_merge
                )
                slugs_by_group[expected_group] = expected_slug

        self.assertEqual(len(slugs_by_group), len(set(slugs_by_group.values())))

    def test_major_and_pre_one_updates_require_dashboard_approval(self) -> None:
        fixtures = [
            (
                "stable patch remains automatic",
                {
                    "manager": "npm",
                    "depName": "stable-patch",
                    "packageName": "stable-patch",
                    "fileName": "package.json",
                    "currentVersion": "1.2.3",
                    "updateType": "patch",
                },
                False,
            ),
            (
                "stable major requires approval",
                {
                    "manager": "npm",
                    "depName": "stable-major",
                    "packageName": "stable-major",
                    "fileName": "package.json",
                    "currentVersion": "1.2.3",
                    "updateType": "major",
                },
                True,
            ),
            (
                "pre-one patch requires approval",
                {
                    "manager": "cargo",
                    "depName": "unstable-crate",
                    "packageName": "unstable-crate",
                    "fileName": "Cargo.toml",
                    "currentVersion": "0.9.0",
                    "updateType": "patch",
                },
                True,
            ),
            (
                "v-prefixed pre-one minor requires approval",
                {
                    "manager": "dockerfile",
                    "depName": "unstable-image",
                    "packageName": "unstable-image",
                    "fileName": "Dockerfile",
                    "currentVersion": "v0.9.0",
                    "updateType": "minor",
                },
                True,
            ),
        ]

        for fixture_name, dependency, should_require_approval in fixtures:
            with self.subTest(fixture=fixture_name):
                resolved = _resolve_dependency(dependency)
                self.assertEqual(
                    resolved["dependencyDashboardApproval"],
                    should_require_approval,
                )

    def test_stable_pin_digest_inherits_the_soaked_safe_lane(self) -> None:
        standing = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("description", "").startswith("Standing automerge")
        )
        self.assertEqual(
            set(standing["matchUpdateTypes"]),
            {"minor", "patch", "pin", "digest", "pinDigest"},
        )
        self.assertEqual(standing["matchCurrentVersion"], "!/^v?0/")

        resolved = _resolve_dependency(
            {
                "manager": "dockerfile",
                "depName": "stable-image",
                "packageName": "stable-image",
                "fileName": "Dockerfile",
                "currentVersion": "4.5.6",
                "updateType": "pinDigest",
            }
        )
        self.assertTrue(resolved["automerge"])
        self.assertTrue(resolved["platformAutomerge"])
        self.assertEqual(resolved["minimumReleaseAge"], "7 days")

    def test_mise_cli_override_is_manager_independent(self) -> None:
        mise_update = {
            "depName": "jdx/mise",
            "packageName": "jdx/mise",
            "fileName": ".github/workflows/test-gate.yml",
            "currentVersion": "v2026.8.3",
            "updateType": "patch",
        }
        for manager in ("custom.regex", "github-actions"):
            with self.subTest(manager=manager):
                resolved = _resolve_dependency({**mise_update, "manager": manager})
                self.assertEqual(resolved["groupSlug"], "mise-cli")
                self.assertFalse(resolved["automerge"])
                self.assertFalse(resolved["platformAutomerge"])
                self.assertEqual(resolved["minimumReleaseAge"], "7 days")

        unrelated = _resolve_dependency(
            {
                "manager": "github-actions",
                "depName": "actions/cache",
                "packageName": "actions/cache",
                "fileName": ".github/workflows/test-gate.yml",
                "currentVersion": "v4.2.3",
                "updateType": "patch",
            }
        )
        self.assertTrue(unrelated["automerge"])
        self.assertTrue(unrelated["platformAutomerge"])
        self.assertEqual(unrelated["minimumReleaseAge"], "7 days")

    def test_later_manual_exceptions_override_the_safe_lane(self) -> None:
        fixtures = [
            (
                "stable major",
                {
                    "manager": "npm",
                    "depName": "major-dependency",
                    "packageName": "major-dependency",
                    "fileName": "package.json",
                    "currentVersion": "3.0.0",
                    "updateType": "major",
                },
            ),
            (
                "plain pre-1.0 pinDigest",
                {
                    "manager": "dockerfile",
                    "depName": "zero-image",
                    "packageName": "zero-image",
                    "fileName": "Dockerfile",
                    "currentVersion": "0.9.0",
                    "updateType": "pinDigest",
                },
            ),
            (
                "v-prefixed pre-1.0 pinDigest",
                {
                    "manager": "dockerfile",
                    "depName": "v-zero-image",
                    "packageName": "v-zero-image",
                    "fileName": "Dockerfile",
                    "currentVersion": "v0.9.0",
                    "updateType": "pinDigest",
                },
            ),
            (
                "TestFlight",
                {
                    "manager": "github-actions",
                    "depName": "Rubio-Enterprises/.github",
                    "packageName": "Rubio-Enterprises/.github",
                    "fileName": ".github/workflows/testflight-release.yml",
                    "currentVersion": "2.0.0",
                    "updateType": "digest",
                },
            ),
            (
                "mise CLI",
                {
                    "manager": "custom.regex",
                    "depName": "jdx/mise",
                    "packageName": "jdx/mise",
                    "fileName": ".github/workflows/lint-hooks.yml",
                    "currentVersion": "v2026.8.1",
                    "updateType": "patch",
                },
            ),
            (
                "uv CLI",
                {
                    "manager": "custom.regex",
                    "depName": "astral-sh/uv",
                    "packageName": "astral-sh/uv",
                    "fileName": ".github/workflows/lint-hooks.yml",
                    "currentVersion": "0.12.1",
                    "updateType": "patch",
                },
            ),
            (
                "agent-sandbox",
                {
                    "manager": "github-tags",
                    "depName": "kubernetes-sigs/agent-sandbox",
                    "packageName": "kubernetes-sigs/agent-sandbox",
                    "fileName": "infrastructure/agent-sandbox.yaml",
                    "currentVersion": "v0.5.5",
                    "updateType": "minor",
                },
            ),
        ]

        for fixture_name, dependency in fixtures:
            with self.subTest(fixture=fixture_name):
                resolved = _resolve_dependency(dependency)
                self.assertFalse(resolved["automerge"])
                self.assertFalse(resolved["platformAutomerge"])

    def test_narrower_groups_override_the_general_pin_group(self) -> None:
        fixtures = [
            (
                {
                    "manager": "pep621",
                    "depName": "rubio-cli-kit",
                    "packageName": "rubio-cli-kit",
                    "fileName": "pyproject.toml",
                    "currentVersion": "1.4.0",
                    "updateType": "pin",
                },
                "tool runtime dependencies",
                "tool-runtime-dependencies",
            ),
            (
                {
                    "manager": "github-actions",
                    "depName": "actions/checkout",
                    "packageName": "actions/checkout",
                    "fileName": ".github/workflows/release.yml",
                    "currentVersion": "4.0.0",
                    "updateType": "pin",
                },
                "github-actions",
                "github-actions",
            ),
        ]
        for dependency, expected_group, expected_slug in fixtures:
            with self.subTest(group=expected_group):
                resolved = _resolve_dependency(dependency)
                self.assertEqual(resolved["groupName"], expected_group)
                self.assertEqual(resolved["groupSlug"], expected_slug)

    def test_existing_narrow_monorepo_groups_are_not_flattened(self) -> None:
        fixtures = [
            {
                "manager": "npm",
                "depName": "@example/core",
                "packageName": "@example/core",
                "fileName": "package.json",
                "currentVersion": "2.0.0",
                "updateType": "patch",
            },
            {
                "manager": "cargo",
                "depName": "example-core",
                "packageName": "example-core",
                "fileName": "Cargo.toml",
                "currentVersion": "2.0.0",
                "updateType": "patch",
            },
            {
                "manager": "pep621",
                "depName": "example-core",
                "packageName": "example-core",
                "fileName": "pyproject.toml",
                "currentVersion": "2.0.0",
                "updateType": "pin",
            },
        ]
        existing_group = {
            "groupName": "example monorepo",
            "groupSlug": "example-monorepo",
        }
        for dependency in fixtures:
            with self.subTest(manager=dependency["manager"]):
                resolved = _resolve_dependency(dependency, existing_group)
                self.assertEqual(resolved["groupName"], "example monorepo")
                self.assertEqual(resolved["groupSlug"], "example-monorepo")
                self.assertTrue(resolved["automerge"])

        pre_one = dict(fixtures[0], currentVersion="v0.9.0")
        resolved_pre_one = _resolve_dependency(pre_one, existing_group)
        self.assertEqual(resolved_pre_one["groupName"], "example monorepo")
        self.assertFalse(resolved_pre_one["automerge"])

    def test_mixed_runtime_group_stays_manual(self) -> None:
        stable_member = _resolve_dependency(
            {
                "manager": "pep621",
                "depName": "rubio-cli-kit",
                "packageName": "rubio-cli-kit",
                "fileName": "pyproject.toml",
                "currentVersion": "1.4.0",
                "updateType": "patch",
            }
        )
        zero_member = _resolve_dependency(
            {
                "manager": "pep621",
                "depName": "typer",
                "packageName": "typer",
                "fileName": "pyproject.toml",
                "currentVersion": "0.16.0",
                "updateType": "minor",
            }
        )
        self.assertEqual(stable_member["groupSlug"], zero_member["groupSlug"])
        self.assertTrue(stable_member["automerge"])
        self.assertFalse(zero_member["automerge"])
        self.assertFalse(
            all(member["automerge"] for member in (stable_member, zero_member))
        )

    def test_manual_overrides_follow_the_standing_safe_rule(self) -> None:
        rules = CONFIG["packageRules"]
        standing = next(
            index
            for index, rule in enumerate(rules)
            if rule.get("description", "").startswith("Standing automerge")
        )
        manual_groups = {
            "pre-1.0 npm dependencies",
            "pre-1.0 cargo dependencies",
            "pre-1.0 dependency pins",
            "testflight release workflow",
            "agent-sandbox",
            "mise-cli",
            "uv-cli",
        }
        for group_name in manual_groups:
            with self.subTest(group=group_name):
                index = next(
                    index
                    for index, rule in enumerate(rules)
                    if rule.get("groupName") == group_name
                )
                self.assertGreater(index, standing)
                self.assertFalse(rules[index]["automerge"])
                self.assertFalse(rules[index]["platformAutomerge"])

    def test_runtime_dependencies_cut_releases_without_widening_automerge(self) -> None:
        matches = [
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("semanticCommitType") == "fix"
            and set(rule.get("matchDepNames", [])) == {"rubio-cli-kit", "typer"}
        ]
        self.assertEqual(len(matches), 1)
        release_rule = matches[0]
        self.assertNotIn("matchManagers", release_rule)
        self.assertNotIn("matchPackageNames", release_rule)
        self.assertNotIn("automerge", release_rule)

    def test_runtime_dependency_group_keeps_coupled_updates_solvable(self) -> None:
        group_rule = _rule_with_group("tool runtime dependencies")
        self.assertEqual(
            set(group_rule["matchDepNames"]), {"rubio-cli-kit", "typer"}
        )
        self.assertEqual(
            set(group_rule["matchUpdateTypes"]),
            {"minor", "patch", "pin", "digest"},
        )
        self.assertNotIn("matchCurrentVersion", group_rule)
        self.assertNotIn("automerge", group_rule)
        self.assertNotIn("platformAutomerge", group_rule)
        self.assertEqual(group_rule["minimumReleaseAge"], "0 days")

    def test_stable_runtime_fast_lane_excludes_zero_x_updates(self) -> None:
        fast_lane = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("description", "").startswith(
                "Stable non-major tool runtime dependencies"
            )
        )
        self.assertEqual(
            set(fast_lane["matchDepNames"]), {"rubio-cli-kit", "typer"}
        )
        self.assertEqual(
            set(fast_lane["matchUpdateTypes"]),
            {"minor", "patch", "pin", "digest"},
        )
        self.assertEqual(fast_lane["matchCurrentVersion"], "!/^v?0/")
        self.assertNotIn("groupName", fast_lane)
        self.assertTrue(fast_lane["automerge"])
        self.assertTrue(fast_lane["platformAutomerge"])
        self.assertEqual(fast_lane["minimumReleaseAge"], "0 days")

    def test_first_party_tool_pin_fast_lane_excludes_zero_x_updates(self) -> None:
        pin_rule = _rule_with_group("first-party tool pins")
        self.assertEqual(
            set(pin_rule["matchUpdateTypes"]),
            {"minor", "patch", "pin", "digest", "pinDigest"},
        )
        self.assertEqual(pin_rule["matchCurrentVersion"], "!/^v?0/")
        self.assertTrue(pin_rule["automerge"])
        self.assertTrue(pin_rule["platformAutomerge"])
        self.assertEqual(pin_rule["minimumReleaseAge"], "0 days")
        self.assertEqual(pin_rule["matchManagers"], ["custom.regex"])
        self.assertEqual(
            pin_rule["matchFileNames"], ["home/.chezmoidata/uv-tools.toml"]
        )
        self.assertEqual(pin_rule["matchPackageNames"], ["/^Rubio-Enterprises\\//"])

        pin_digest_rule = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("pinDigests") is True
        )
        self.assertEqual(pin_digest_rule["matchManagers"], ["custom.regex"])
        self.assertEqual(
            pin_digest_rule["matchFileNames"], ["home/.chezmoidata/uv-tools.toml"]
        )
        self.assertEqual(
            pin_digest_rule["matchPackageNames"], ["/^Rubio-Enterprises\\//"]
        )
        self.assertNotIn("matchUpdateTypes", pin_digest_rule)
        self.assertNotIn("matchCurrentVersion", pin_digest_rule)
        self.assertNotIn("automerge", pin_digest_rule)

    def test_standing_automerge_description_records_closed_gate_gap(self) -> None:
        rule = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("description", "").startswith("Standing automerge")
        )
        description = rule["description"]
        self.assertIn(".github-private`#179", description)
        self.assertIn("lint-hooks / lint-hooks", description)
        self.assertNotIn("required NOWHERE", description)
        self.assertNotIn("Closing it properly", description)

    def test_uv_tools_manager_binds_one_table_and_replaces_only_version(self) -> None:
        managers = [
            manager
            for manager in CONFIG["customManagers"]
            if manager.get("fileMatch") == ["^home/\\.chezmoidata/uv-tools\\.toml$"]
        ]
        self.assertEqual(len(managers), 1)
        manager = managers[0]
        self.assertEqual(manager["matchStringsStrategy"], "recursive")
        self.assertNotIn("pinDigests", manager)
        self.assertEqual(
            manager["autoReplaceStringTemplate"],
            '{{{indentation}}}version = "{{{newDigest}}}"  # {{{newValue}}}',
        )

        table_regex, version_regex = map(_python_regex, manager["matchStrings"])
        table_matches = list(table_regex.finditer(REGISTRY_FIXTURE))
        self.assertEqual(
            [match.group("depName") for match in table_matches],
            ["sha-tool", "tag-tool"],
        )
        self.assertEqual(
            [match.group("packageName") for match in table_matches],
            ["Rubio-Enterprises/sha-tool", "Rubio-Enterprises/tag-tool"],
        )

        expected_values = {"sha-tool": "v1.2.3", "tag-tool": "v2.3.4"}
        for table_match in table_matches:
            dep_name = table_match.group("depName")
            table = table_match.group(0)
            self.assertNotIn("[tools.after]", table)
            version_match = version_regex.search(table)
            self.assertIsNotNone(version_match)
            assert version_match is not None

            current_value = version_match.group("taggedValue") or version_match.group(
                "bareValue"
            )
            self.assertEqual(current_value, expected_values[dep_name])
            if dep_name == "sha-tool":
                self.assertEqual(
                    version_match.group("currentDigest"),
                    "0123456789abcdef0123456789abcdef01234567",
                )
            else:
                self.assertIsNone(version_match.group("currentDigest"))

            replacement = (
                f'{version_match.group("indentation")}version = '
                '"fedcba9876543210fedcba9876543210fedcba98"  # v9.9.9'
            )
            updated = table[: version_match.start()] + replacement + table[version_match.end() :]
            self.assertIn(f"[tools.{dep_name}]", updated)
            self.assertIn(
                f'source = "git+ssh://git@github.com/Rubio-Enterprises/{dep_name}"',
                updated,
            )
            self.assertEqual(
                [line for line in updated.splitlines() if not line.startswith("version")],
                [line for line in table.splitlines() if not line.startswith("version")],
            )

    def test_dockerfile_arg_versions_use_the_shipped_preset(self) -> None:
        # The `# renovate: datasource=... ARG X_VERSION=...` idiom is invisible to
        # the built-in dockerfile manager, which only reads FROM lines. Renovate
        # ships a manager for exactly this shape; prefer it over a hand-rolled
        # regex so the pattern tracks upstream.
        self.assertIn("customManagers:dockerfileVersions", CONFIG["extends"])

    def test_deb_datasource_carries_a_registry_or_it_resolves_nothing(self) -> None:
        # Renovate's deb datasource has no default registry: a `datasource=deb`
        # marker with no registryUrl looks up null and the pin silently never
        # advances. The suite must track the consumer's Debian base image.
        rule = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("matchDatasources") == ["deb"]
        )
        self.assertEqual(rule["matchPackageNames"], ["tailscale"])
        self.assertEqual(
            rule["registryUrls"],
            [
                "https://pkgs.tailscale.com/stable/debian"
                "?suite=trixie&components=main&binaryArch=amd64"
            ],
        )
        self.assertNotIn("automerge", rule)
        # A trixie-specific registry must never be inherited on package identity
        # alone — a `deb`/`tailscale` dep reached by any other manager keeps
        # Renovate's default of no registry rather than the wrong suite.
        self.assertEqual(rule["matchManagers"], ["custom.regex"])
        self.assertTrue(_selects_file(rule, "image/Dockerfile"))
        self.assertFalse(_selects_file(rule, "infrastructure/cluster/apps.yaml"))

    def test_scoped_rules_cannot_match_on_package_identity_alone(self) -> None:
        # Both rules configure image-specific behavior, so both must be pinned to
        # the custom.regex manager AND to Dockerfile-shaped files.
        deb_rule = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("matchDatasources") == ["deb"]
        )
        claude_rule = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("matchPackageNames") == [CLAUDE_CODE_DEP]
        )
        for rule in (deb_rule, claude_rule):
            self.assertEqual(rule["matchManagers"], ["custom.regex"])
            # Positive: Dockerfile-shaped files at any depth, root included.
            for path in (
                "image/Dockerfile",
                "Dockerfile",
                "a/b/c/Dockerfile",
                "Dockerfile.ci",
                "build.Dockerfile",
            ):
                self.assertTrue(_selects_file(rule, path), path)
            # Negative: an ordinary npm dependency of the same name, or any
            # non-Dockerfile file, must fall outside the rule.
            for path in ("package.json", "app/package.json", "pyproject.toml"):
                self.assertFalse(_selects_file(rule, path), path)
            # The manager selector is the second, independent guard: even a
            # Dockerfile reached by a built-in manager must not inherit these.
            self.assertNotIn("npm", rule["matchManagers"])
            self.assertNotIn("dockerfile", rule["matchManagers"])

    def test_claude_code_manager_reads_the_run_line_not_an_assignment(self) -> None:
        managers = [
            manager
            for manager in CONFIG["customManagers"]
            if manager.get("depNameTemplate") == CLAUDE_CODE_DEP
        ]
        self.assertEqual(len(managers), 1)
        manager = managers[0]
        self.assertEqual(manager["datasourceTemplate"], "npm")

        regex = _python_regex(manager["matchStrings"][0])
        self.assertEqual(
            [m.group("currentValue") for m in regex.finditer(DOCKERFILE_FIXTURE)],
            ["2.1.226"],
        )
        # An unannotated global install must not be claimed, and a ${VAR} form
        # belongs to the dockerfileVersions preset's ARG match, not to this one.
        self.assertEqual(list(regex.finditer(DOCKERFILE_DECOYS)), [])

    def test_claude_code_follows_the_stable_dist_tag(self) -> None:
        # followTag skips Renovate's normal major/minor/patch logic and the
        # stability-days check, so matchUpdateTypes cannot gate this dep and
        # automerge is all-or-nothing. It is ON: `stable` is the vendor's soak.
        rule = next(
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("matchPackageNames") == [CLAUDE_CODE_DEP]
        )
        self.assertEqual(rule["followTag"], "stable")
        self.assertTrue(rule["automerge"])
        self.assertEqual(rule["schedule"], ["before 6am on monday"])
        self.assertEqual(rule["commitMessageTopic"], "Claude Code")
        # These are image decisions, not opinions about the npm package, so a
        # plain package.json dependency of the same name must not inherit them.
        self.assertEqual(rule["matchManagers"], ["custom.regex"])
        self.assertFalse(_selects_file(rule, "package.json"))

    def test_flux_widening_keeps_the_upstream_gotk_pattern(self) -> None:
        # managerFilePatterns REPLACES the manager default rather than extending
        # it, so dropping the gotk pattern would silently stop tracking the Flux
        # distribution across every repo that has one.
        patterns = CONFIG["flux"]["managerFilePatterns"]
        self.assertIn(FLUX_DEFAULT_PATTERN, patterns)
        self.assertIn("/(^|/)infrastructure/.+\\.ya?ml$/", patterns)

    def test_agent_sandbox_git_ref_and_image_move_in_one_pr(self) -> None:
        # A split bump briefly runs new CRDs against an old controller
        # (kubernetes-sigs/agent-sandbox#844), so both deps share a branch.
        rule = _rule_with_group("agent-sandbox")
        self.assertEqual(rule["matchPackageNames"], AGENT_SANDBOX_DEPS)
        # groupName alone still lets Renovate open a branch carrying just one of
        # the two. minimumGroupSize holds branch creation until both have an
        # update, so the CRDs can never land without the controller.
        self.assertEqual(rule["minimumGroupSize"], len(AGENT_SANDBOX_DEPS))

    def test_agent_sandbox_stays_explicitly_manual(self) -> None:
        # The stable-version matcher now excludes plain and v-prefixed 0.x
        # versions. Keep the later explicit override as defense in depth for
        # this coupled, migration-sensitive dependency.
        rules = CONFIG["packageRules"]
        standing = next(
            index
            for index, rule in enumerate(rules)
            if rule.get("description", "").startswith("Standing automerge")
        )
        sandbox = next(
            index
            for index, rule in enumerate(rules)
            if rule.get("groupName") == "agent-sandbox"
        )
        self.assertGreater(sandbox, standing)
        self.assertFalse(rules[sandbox]["automerge"])
        self.assertFalse(rules[sandbox]["platformAutomerge"])


if __name__ == "__main__":
    unittest.main()
