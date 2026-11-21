"""Behavioral contract tests for the organization Renovate preset."""

from __future__ import annotations

import json
import re
import unittest
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

AGENT_SANDBOX_DEPS = [
    "kubernetes-sigs/agent-sandbox",
    "registry.k8s.io/agent-sandbox/agent-sandbox-controller",
]
CLAUDE_CODE_DEP = "@anthropic-ai/claude-code"
# Renovate 44.218.0 resolves schedule:weekends to this cron expression
WEEKEND_SCHEDULE = ["* * * * 0,6"]
HELD_UPDATE_EXPRESSION = (
    "updateType = 'major' or ($not(updateType in ['minor', 'patch']) and "
    "$contains(lockedVersion ? lockedVersion : "
    "(currentVersion ? currentVersion : currentValue), /^v?0/))"
)
UNGROUPED_EXPRESSION = "$not($exists(groupName)) or groupName = null"
HELD_POSTURE = {
    "draftPR": True,
    "automerge": False,
    "platformAutomerge": False,
    "prConcurrentLimit": 1,
    "branchConcurrentLimit": 1,
}
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
    positive = [selector for selector in selectors if not selector.startswith("!")]
    negative = [selector[1:] for selector in selectors if selector.startswith("!")]

    def matches(selector: str) -> bool:
        regex = re.fullmatch(r"/(.*)/(i?)", selector)
        if regex is not None:
            flags = re.IGNORECASE if regex.group(2) else 0
            return re.search(regex.group(1), value, flags) is not None
        return selector == value

    return not any(map(matches, negative)) and (
        not positive or any(map(matches, positive))
    )


def _rule_matches(
    rule: dict[str, Any],
    dependency: dict[str, str],
    resolved: dict[str, Any],
) -> bool:
    selector_fields = {
        "matchManagers": "manager",
        "matchUpdateTypes": "updateType",
        "matchDepTypes": "depType",
        "matchDepNames": "depName",
        "matchPackageNames": "packageName",
        "matchFileNames": "fileName",
        "matchDatasources": "datasource",
        "matchRepositories": "repository",
    }
    for rule_field, dependency_field in selector_fields.items():
        if rule_field not in rule:
            continue
        value = dependency.get(dependency_field)
        if value is None or not _selector_matches(rule[rule_field], value):
            return False

    current_value = rule.get("matchCurrentValue")
    if current_value is not None and not _selector_matches(
        [current_value], dependency.get("currentValue", "")
    ):
        return False

    compare_version = (
        dependency.get("lockedVersion")
        or dependency.get("currentVersion")
        or dependency.get("currentValue", "")
    )
    current_version = rule.get("matchCurrentVersion")
    if current_version is not None:
        if current_version == "<7":
            if int(dependency["currentVersion"].lstrip("v").split(".")[0]) >= 7:
                return False
        else:
            negate = current_version.startswith("!")
            pattern = current_version[2:-1] if negate else current_version[1:-1]
            matched = re.search(pattern, compare_version) is not None
            if matched == negate:
                return False

    expressions = rule.get("matchJsonata", [])
    matches = []
    for expression in expressions:
        # JSONata's $exists is true for a key holding null (checked against Renovate
        # 44.218.0's library), and every ungrouped update carries groupName: null,
        # so `$exists(groupName) = false` never matched and is unsupported here.
        if expression == UNGROUPED_EXPRESSION:
            matches.append(resolved.get("groupName") is None)
        elif expression == HELD_UPDATE_EXPRESSION:
            update_type = dependency.get("updateType")
            matches.append(
                update_type == "major"
                or (
                    update_type not in ("minor", "patch")
                    and re.search(r"^v?0", compare_version) is not None
                )
            )
        else:
            raise AssertionError(f"unsupported test fixture JSONata: {expression}")
    if expressions and not any(matches):
        return False
    return True


def _resolve_dependency(
    dependency: dict[str, str],
    initial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        # Renovate's option defaults put groupName: null on every upgrade config.
        "groupName": None,
        "automerge": False,
        "draftPR": False,
        "prConcurrentLimit": CONFIG["prConcurrentLimit"],
        "branchConcurrentLimit": CONFIG["branchConcurrentLimit"],
        "platformAutomerge": False,
        "minimumReleaseAge": CONFIG["minimumReleaseAge"],
        "minimumReleaseAgeBehaviour": "timestamp-required",
        "rangeStrategy": CONFIG["rangeStrategy"],
        "schedule": CONFIG.get("schedule", WEEKEND_SCHEDULE),
    }
    if initial is not None:
        resolved.update(initial)
    resolved_fields = {
        "automerge",
        "draftPR",
        "prConcurrentLimit",
        "branchConcurrentLimit",
        "enabled",
        "groupName",
        "groupSlug",
        "minimumReleaseAge",
        "minimumReleaseAgeBehaviour",
        "platformAutomerge",
        "rangeStrategy",
        "allowedVersions",
        "pinDigests",
        "schedule",
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
        self.assertEqual(CONFIG["timezone"], "America/Chicago")
        self.assertIn("schedule:weekends", CONFIG["extends"])
        self.assertNotIn(":disableDependencyDashboard", CONFIG["extends"])
        # Temporary backlog drain from 2026-11-21; revert both to 1 afterwards.
        self.assertEqual(CONFIG["commitHourlyLimit"], 5)
        self.assertEqual(CONFIG["prHourlyLimit"], 5)
        self.assertEqual(CONFIG["prConcurrentLimit"], 5)
        self.assertEqual(CONFIG["branchConcurrentLimit"], 5)
        self.assertEqual(CONFIG["rebaseWhen"], "conflicted")
        self.assertEqual(CONFIG["minimumReleaseAge"], "7 days")

    def test_copier_is_retired(self) -> None:
        self.assertEqual(CONFIG["copier"], {"enabled": False})
        self.assertFalse((ROOT / "copier.json").exists())
        self.assertFalse(any("copier" in preset for preset in CONFIG["extends"]))
        for rule in CONFIG["packageRules"]:
            with self.subTest(rule=rule.get("description", rule.get("groupName"))):
                self.assertNotIn("copier", json.dumps(rule.get("matchManagers", [])))

    def test_no_rule_arms_github_native_automerge(self) -> None:
        side_merge_rules = [
            rule
            for rule in CONFIG["packageRules"]
            if rule.get("automerge") is True
            and rule.get("platformAutomerge") is False
        ]
        self.assertEqual(len(side_merge_rules), 7)
        for rule in CONFIG["packageRules"]:
            with self.subTest(rule=rule.get("description", rule.get("groupName"))):
                self.assertIsNot(rule.get("platformAutomerge"), True)

    def test_other_safe_class_updates_keep_renovate_side_merge(self) -> None:
        resolved = _resolve_dependency({
            "repository": "Rubio-Enterprises/org-ops",
            "manager": "npm",
            "depName": "vite",
            "packageName": "vite",
            "fileName": "package.json",
            "currentVersion": "8.1.0",
            "updateType": "patch",
        })
        self.assertTrue(resolved["automerge"])
        self.assertFalse(resolved["platformAutomerge"])

    def test_python_interpreter_pins_update_and_the_floor_is_held(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/org-ops",
            "depName": "python",
            "packageName": "python",
            "currentVersion": "3.14.6",
            "updateType": "patch",
        }
        for manager, file_name in (
            ("asdf", ".tool-versions"),
            ("pyenv", ".python-version"),
        ):
            with self.subTest(manager=manager):
                # Runtime pins are repository-owned: Renovate proposes them.
                candidate = {**dependency, "manager": manager, "fileName": file_name}
                self.assertNotIn("enabled", _resolve_dependency(candidate))

        consumer_manifest = {
            **dependency,
            "manager": "pep621",
            "fileName": "pyproject.toml",
            "depType": "requires-python",
        }
        self.assertFalse(_resolve_dependency(consumer_manifest)["enabled"])
        self.assertNotIn(
            "enabled",
            _resolve_dependency(
                {**consumer_manifest, "packageName": "mypy", "depName": "mypy"}
            ),
        )
        self.assertNotIn(
            "enabled",
            _resolve_dependency({**consumer_manifest, "depType": "project.dependencies"}),
        )
        self.assertNotIn(
            "enabled",
            _resolve_dependency(
                {**dependency, "manager": "asdf", "fileName": "other/.tool-versions"}
            ),
        )
        self.assertNotIn(
            "enabled",
            _resolve_dependency(
                {**dependency, "manager": "asdf", "fileName": ".tool-versions", "depName": "go"}
            ),
        )

    def test_node_runtime_pins_update_and_the_published_floor_is_held(self) -> None:
        node = {
            "repository": "Rubio-Enterprises/pi-agent-browser-native",
            "depName": "node",
            "packageName": "node",
            "datasource": "node-version",
            "updateType": "patch",
        }
        # Renovate 44's npm manager extracts package.json engines.node as
        # depType "engines", depName "node"; that published floor is held.
        floor = {
            **node,
            "manager": "npm",
            "fileName": "package.json",
            "depType": "engines",
            "currentValue": ">=24.21.0",
        }
        self.assertFalse(_resolve_dependency(floor)["enabled"])

        # The image pin, other engines and other dependency types keep updating.
        for candidate in (
            {**node, "manager": "mise", "fileName": "image/mise.toml", "currentValue": "24.21.0"},
            {**floor, "depName": "npm", "packageName": "npm", "datasource": "npm"},
            {**floor, "depType": "devEngines.runtime"},
            {**floor, "depType": "volta"},
            {
                **floor,
                "depType": "devDependencies",
                "depName": "@types/node",
                "packageName": "@types/node",
                "datasource": "npm",
                "currentValue": "24.19.1",
            },
        ):
            with self.subTest(candidate=candidate):
                self.assertNotIn("enabled", _resolve_dependency(candidate))

    def test_python_minor_bumps_are_manual_and_patch_pins_automerge(self) -> None:
        pins = (
            ("mise", "image/mise.toml"),
            ("asdf", ".tool-versions"),
            ("pyenv", ".python-version"),
        )
        for manager, file_name in pins:
            dependency = {
                "repository": "Rubio-Enterprises/org-ops",
                "manager": manager,
                "depName": "python",
                "packageName": "python",
                "fileName": file_name,
                "currentVersion": "3.15.2",
            }
            with self.subTest(manager=manager, file=file_name):
                minor = _resolve_dependency({**dependency, "updateType": "minor"})
                self.assertNotIn("enabled", minor)
                self.assertFalse(minor["automerge"])
                self.assertFalse(minor["platformAutomerge"])
                patch = _resolve_dependency({**dependency, "updateType": "patch"})
                self.assertTrue(patch["automerge"])
                self.assertFalse(patch["platformAutomerge"])

        # Other runtimes, and other Python-named dependencies, keep the standing lane.
        for manager, name in (("mise", "node"), ("npm", "python"), ("pep621", "pytest")):
            with self.subTest(manager=manager, dep=name):
                resolved = _resolve_dependency({
                    "repository": "Rubio-Enterprises/org-ops",
                    "manager": manager,
                    "depName": name,
                    "packageName": name,
                    "fileName": "mise.toml",
                    "currentVersion": "24.1.0",
                    "updateType": "minor",
                })
                self.assertTrue(resolved["automerge"])

    def test_coupled_earendil_updates_keep_the_standing_policy(self) -> None:
        for update, version, held in (
            ("minor", "1.3.0", False), ("major", "1.3.0", True),
            ("patch", "0.9.0", False), ("major", "0.9.0", True),
        ):
            dependency = {
                "manager": "npm", "datasource": "npm", "depName": "@earendil-works/pi-ai",
                "packageName": "@earendil-works/pi-ai", "currentVersion": version,
                "updateType": update,
            }
            with self.subTest(update=update, version=version):
                result = _resolve_dependency(dependency)
                self.assertEqual(result["groupSlug"], "earendil-works-packages")
                self.assertEqual(result["draftPR"], held)
                self.assertEqual(result["automerge"], not held)
        group = _rule_with_group("earendil-works packages")
        self.assertFalse(group["separateMajorMinor"])
        self.assertFalse(_selector_matches(group["matchPackageNames"], "@earendil-works-other/pi-ai"))

    def test_dockerfile_python_manager_and_minor_hold(self) -> None:
        manager = next(item for item in CONFIG["customManagers"] if item.get("packageNameTemplate") == "python/cpython")
        self.assertEqual(manager["datasourceTemplate"], "github-tags")
        self.assertEqual(manager["extractVersionTemplate"], r"^v(?<version>\S+)")
        matcher = _python_regex(manager["matchStrings"][0])
        self.assertEqual(matcher.search("ENV UV_PYTHON=3.14.6\n").group("currentValue"), "3.14.6")
        for invalid in ("ENV UV_PYTHON_DOWNLOADS=never", "ENV UV_PYTHON=3.14", "# ENV UV_PYTHON=3.14.6"):
            self.assertIsNone(matcher.search(invalid))
        dependency = {"depName": "python", "packageName": "python/cpython", "datasource": "github-tags", "currentVersion": "3.14.6"}
        for manager_name in ("mise", "custom.regex"):
            for update, automerge in (("minor", False), ("patch", True), ("major", False)):
                with self.subTest(manager=manager_name, update=update):
                    resolved = _resolve_dependency({**dependency, "manager": manager_name, "updateType": update})
                    self.assertEqual(resolved["groupSlug"], "python-runtime")
                    self.assertEqual(resolved["automerge"], automerge)
                    self.assertEqual(resolved["draftPR"], update == "major")

    def test_docker_hub_python_image_joins_the_python_runtime_group(self) -> None:
        for name in ("python", "docker.io/python", "docker.io/library/python"):
            dependency = {"manager": "dockerfile", "datasource": "docker", "depName": name, "packageName": name, "currentVersion": "3.14.6"}
            for update, automerge in (("minor", False), ("patch", True), ("major", False)):
                with self.subTest(image=name, update=update):
                    resolved = _resolve_dependency({**dependency, "updateType": update})
                    self.assertEqual(resolved["groupSlug"], "python-runtime")
                    self.assertEqual(resolved["automerge"], automerge)
                    self.assertEqual(resolved["draftPR"], update == "major")
        # Other images, including other images named python, keep their own branches.
        # Docker Hub images keep the standing automerge; images on other registries are
        # merged by a person (UndatedRegistryImageTests).
        for name, automerge in (("node", True), ("ghcr.io/astral-sh/uv", False), ("ghcr.io/example/python", False), ("pypy", True)):
            with self.subTest(image=name):
                resolved = _resolve_dependency({"manager": "dockerfile", "datasource": "docker", "depName": name, "packageName": name, "currentVersion": "3.14.6", "updateType": "minor"})
                self.assertNotIn("groupSlug", resolved)
                self.assertEqual(resolved["automerge"], automerge)

    def test_workflow_python_minor_does_not_hold_other_actions(self) -> None:
        dependency = {"manager": "github-actions", "depType": "uses-with", "datasource": "github-releases", "depName": "python", "packageName": "actions/python-versions", "currentVersion": "3.13.0"}
        minor = _resolve_dependency({**dependency, "updateType": "minor"})
        self.assertEqual(minor["groupSlug"], "workflow-python")
        self.assertFalse(minor["automerge"])
        patch = _resolve_dependency({**dependency, "updateType": "patch"})
        self.assertTrue(patch["automerge"])
        action = _resolve_dependency({**dependency, "depType": "action", "datasource": "github-tags", "depName": "actions/checkout", "packageName": "actions/checkout", "updateType": "minor"})
        self.assertEqual(action["groupSlug"], "github-actions")
        self.assertTrue(action["automerge"])

    def test_generated_sidecars_are_not_independent_manager_inputs(self) -> None:
        # ignorePaths replaces the inherited list from :ignoreModulesAndTests.
        self.assertEqual(CONFIG.get("ignorePaths"), [
            "**/node_modules/**", "**/bower_components/**", "**/vendor/**",
            "**/examples/**", "**/__tests__/**", "**/tests/**",
            "**/__fixtures__/**", "**/.mise/locks/**",
        ])
        for path in (
            "image/.mise/locks/pipx-pytest/9.2.0/pyproject.toml",
            "image/.mise/locks/pipx-pytest/9.2.0/uv.lock",
            "image/.mise/locks/npm-likec4/1.60.0/package.json",
            "mise/.mise/locks/npm-commitlint-cli/21.2.2/package.json",
            ".mise/locks/npm-playwright/1.65.0/aube-lock.yaml",
        ):
            with self.subTest(path=path):
                self.assertTrue(any(PurePosixPath(path).full_match(pattern)
                                    for pattern in CONFIG["ignorePaths"]))
        # test/ is scanned: a repository's custom manager may keep a fixture there in
        # step with the pin it mirrors (dotfiles scripts/test/fixtures/).
        for path in ("pyproject.toml", "uv.lock", "package.json", "image/mise.toml",
                     "image/mise.lock", "src/package.json",
                     "scripts/test/fixtures/pi-kept-package-records.toml"):
            with self.subTest(path=path):
                self.assertFalse(any(PurePosixPath(path).full_match(pattern)
                                     for pattern in CONFIG["ignorePaths"]))

    def test_sidecar_pins_and_mise_maintenance_keep_their_update_path(self) -> None:
        self.assertTrue(CONFIG.get("mise", {}).get("enabled", True))
        self.assertTrue(CONFIG.get("lockFileMaintenance", {}).get("enabled", True))
        self.assertTrue(CONFIG["mise"].get("lockFileMaintenance", {}).get("enabled", True))
        for name, version in (("pipx:pytest", "9.1.1"), ("npm:likec4", "1.59.4")):
            for update in ("minor", "patch", "lockFileMaintenance"):
                with self.subTest(name=name, update=update):
                    resolved = _resolve_dependency({
                        "manager": "mise", "depName": name, "packageName": name,
                        "fileName": "image/mise.toml", "currentVersion": version,
                        "updateType": update,
                    })
                    self.assertTrue(resolved.get("enabled", True))
                    self.assertTrue(resolved["automerge"])
                    self.assertFalse(resolved["platformAutomerge"])

    def test_image_mise_updates_follow_the_standing_runtime_policy(self) -> None:
        for name, version in (
            ("rust", "1.95.0"),
            ("node", "24.1.0"),
            ("python", "3.14.6"),
            ("go", "1.25.0"),
            ("pnpm", "10.0.0"),
            ("node", "0.9.0"),
            ("node", "v0.9.0"),
        ):
            for update in ("major", "minor", "patch", "pin", "digest", "pinDigest"):
                with self.subTest(name=name, version=version, update=update):
                    resolved = _resolve_dependency({
                        "manager": "mise",
                        "depName": name,
                        "packageName": name,
                        "fileName": "image/mise.toml",
                        "currentVersion": version,
                        "updateType": update,
                    })
                    pre_one = version.lstrip("v").startswith("0.")
                    held = update == "major" or (
                        pre_one and update not in ("minor", "patch")
                    )
                    python_minor = name == "python" and update == "minor"
                    self.assertNotIn("enabled", resolved)
                    self.assertEqual(resolved["automerge"], not (held or python_minor))
                    self.assertFalse(resolved["platformAutomerge"])
                    self.assertEqual(resolved["draftPR"], held)
                    self.assertEqual(resolved["prConcurrentLimit"], 1 if held else 5)
                    self.assertEqual(resolved["branchConcurrentLimit"], 1 if held else 5)
                    self.assertEqual(resolved["minimumReleaseAge"], "7 days")

    def test_image_pin_policy_keeps_other_locations_and_managers_unchanged(self) -> None:
        for manager, file_name in (
            ("mise", "image/mise.toml"),
            ("mise", "mise.toml"),
            ("mise", "other/image/mise.toml"),
            ("npm", "image/mise.toml"),
        ):
            with self.subTest(manager=manager, file=file_name):
                resolved = _resolve_dependency({
                    "manager": manager,
                    "depName": "node",
                    "packageName": "node",
                    "fileName": file_name,
                    "currentVersion": "24.1.0",
                    "updateType": "patch",
                })
                self.assertNotIn("enabled", resolved)
                self.assertTrue(resolved["automerge"])
                self.assertFalse(resolved["platformAutomerge"])
                self.assertFalse(resolved["draftPR"])
                self.assertEqual(resolved["minimumReleaseAge"], "7 days")

    def test_image_lock_maintenance_keeps_the_standing_automerge_policy(self) -> None:
        for file_name in ("image/mise.toml", "image/mise.lock"):
            with self.subTest(file=file_name):
                resolved = _resolve_dependency({
                    "manager": "mise",
                    "fileName": file_name,
                    "updateType": "lockFileMaintenance",
                })
                self.assertNotIn("enabled", resolved)
                self.assertTrue(resolved["automerge"])
                self.assertFalse(resolved["platformAutomerge"])
                self.assertFalse(resolved["draftPR"])
                self.assertEqual(resolved["minimumReleaseAge"], "0 days")

    def test_host_first_main_images_pin_and_automerge_in_any_dockerfile(self) -> None:
        group = "standards base and runner images"
        rule = _rule_with_group(group)
        self.assertNotIn("schedule", rule)
        self.assertNotIn("automergeSchedule", rule)

        for repository in (
            "Rubio-Enterprises/org-ops",
            "Rubio-Enterprises/daily-routine",
            "Rubio-Enterprises/opentabs",
            "another-owner/consumer",
        ):
            for name in ("base", "runner"):
                package = f"ghcr.io/rubio-enterprises/{name}"
                dependency = {
                    "repository": repository,
                    "manager": "dockerfile",
                    "datasource": "docker",
                    "depName": package,
                    "packageName": package,
                    "currentValue": "main",
                }
                for update in ({}, {"updateType": "pinDigest"}, {"updateType": "digest"}):
                    with self.subTest(repository=repository, name=name, update=update):
                        resolved = _resolve_dependency({**dependency, **update})
                        self.assertTrue(resolved["enabled"])
                        self.assertTrue(resolved["pinDigests"])
                        self.assertEqual(resolved["groupName"], group)
                        self.assertEqual(resolved["groupSlug"], "standards-base-and-runner")
                        self.assertEqual(resolved["schedule"], WEEKEND_SCHEDULE)
                        self.assertIsNone(resolved["minimumReleaseAge"])
                        self.assertTrue(resolved["automerge"])
                        self.assertFalse(resolved["platformAutomerge"])
                        self.assertFalse(resolved["draftPR"])
        self.assertNotIn("matchRepositories", rule)

    def test_host_first_main_images_float_outside_dockerfiles(self) -> None:
        for manager in ("docker-compose", "devcontainer", "custom.regex", "github-actions"):
            for name in ("base", "runner"):
                for update in (None, "major", "minor", "patch", "digest", "pinDigest"):
                    with self.subTest(manager=manager, name=name, update=update):
                        package = f"ghcr.io/rubio-enterprises/{name}"
                        dependency = {
                            "repository": "Rubio-Enterprises/org-ops",
                            "manager": manager,
                            "datasource": "docker",
                            "depName": package,
                            "packageName": package,
                            "currentValue": "main",
                        }
                        if update is not None:
                            dependency["updateType"] = update
                        resolved = _resolve_dependency(dependency, {"pinDigests": True})
                        self.assertFalse(resolved.get("enabled", True))
                        self.assertFalse(resolved["pinDigests"])
                        self.assertNotEqual(resolved.get("groupSlug"), "standards-base-and-runner")
                        self.assertEqual(resolved["schedule"], WEEKEND_SCHEDULE)

    def test_host_first_float_rule_preserves_release_pins_and_other_images(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/org-ops",
            "manager": "dockerfile",
            "datasource": "docker",
            "depName": "ghcr.io/rubio-enterprises/base",
            "packageName": "ghcr.io/rubio-enterprises/base",
            "currentValue": "main",
            "currentVersion": "main",
            "updateType": "pinDigest",
        }
        for changes in (
            {"currentValue": "v5.0.5", "currentVersion": "5.0.5"},
            {"currentValue": "main-backup"},
            {"packageName": "ghcr.io/rubio-enterprises/base-extra"},
            {"packageName": "ghcr.io/rubio-enterprises/runner-extra"},
            {"packageName": "ghcr.io/rubio-enterprises/app"},
            {"packageName": "ghcr.io/other/base"},
            {"packageName": "ghcr.io/rubio-enterprises-x/base"},
            {"packageName": "rubio-enterprises/base"},
            {"packageName": "docker.io/library/ubuntu"},
            {"datasource": "github-tags"},
        ):
            with self.subTest(changes=changes):
                resolved = _resolve_dependency(
                    {**dependency, **changes}, {"pinDigests": True}
                )
                self.assertTrue(resolved.get("enabled", True))
                self.assertTrue(resolved["pinDigests"])
                self.assertIsNone(resolved["groupName"])
                self.assertEqual(resolved["schedule"], WEEKEND_SCHEDULE)

    def test_python_dev_tools_are_exact_pins_only_in_dev_groups(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/avr",
            "manager": "pep621",
            "depName": "mypy",
            "packageName": "mypy",
            "fileName": "pyproject.toml",
            "depType": "dependency-groups",
            "currentVersion": "0.16.7",
            "updateType": "patch",
        }
        for dep_type in ("dependency-groups", "tool.uv.dev-dependencies"):
            for name in ("pytest", "pytest-cov", "mypy", "pyright", "coverage", "hypothesis"):
                with self.subTest(dep_type=dep_type, name=name):
                    resolved = _resolve_dependency(
                        {**dependency, "depType": dep_type, "depName": name, "packageName": name}
                    )
                    self.assertEqual(resolved["rangeStrategy"], "pin")
                    self.assertEqual(resolved["minimumReleaseAge"], "7 days")

        for changes in (
            {"depType": "project.dependencies"},
            {"depType": "project.optional-dependencies"},
            {"packageName": "typer", "depName": "typer"},
            {"manager": "pip_requirements", "fileName": "requirements.txt"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(_resolve_dependency({**dependency, **changes})["rangeStrategy"], "bump")

    def test_python_dev_tool_pin_alone_skips_the_soak(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/avr",
            "manager": "pep621",
            "depName": "mypy",
            "packageName": "mypy",
            "fileName": "pyproject.toml",
            "depType": "dependency-groups",
            "currentVersion": "0.16.4",
            "updateType": "pin",
        }
        for dep_type in ("dependency-groups", "tool.uv.dev-dependencies"):
            with self.subTest(dep_type=dep_type):
                self.assertIsNone(_resolve_dependency({**dependency, "depType": dep_type})["minimumReleaseAge"])

        for changes in (
            {"updateType": "patch"},
            {"updateType": "major"},
            {"depType": "project.dependencies"},
            {"packageName": "black", "depName": "black"},
            {"manager": "npm", "fileName": "package.json"},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(
                    _resolve_dependency({**dependency, **changes})["minimumReleaseAge"], "7 days"
                )

    def test_starter_workflow_pins_advance_only_through_the_package(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/org-ops",
            "manager": "github-actions",
            "depName": "jdx/mise-action",
            "packageName": "jdx/mise-action",
            "currentVersion": "4.0.0",
            "updateType": "digest",
        }
        first_party = {"depName": "Rubio-Enterprises/.github", "packageName": "Rubio-Enterprises/.github"}
        file_name = ".github/workflows/standards.yml"
        self.assertFalse(_resolve_dependency({**dependency, "fileName": file_name})["enabled"])
        self.assertFalse(
            _resolve_dependency({**dependency, **first_party, "fileName": file_name})["enabled"]
        )
        # The injected Required Workflow in .github is not a starter copy.
        injected = _resolve_dependency(
            {**dependency, "repository": "Rubio-Enterprises/.github", "fileName": file_name}
        )
        self.assertTrue(injected.get("enabled", True))

        hand_authored = _resolve_dependency({**dependency, "fileName": ".github/workflows/deploy.yml"})
        self.assertTrue(hand_authored.get("enabled", True))

    def test_first_party_actions_are_disabled_in_every_caller(self) -> None:
        dependency = {
            "manager": "github-actions",
            "depName": "Rubio-Enterprises/.github",
            "packageName": "Rubio-Enterprises/.github",
            "currentVersion": "main",
        }
        for filename in (".github/workflows/testflight-release.yml",
                         ".github/workflows/test-gate.yml",
                         ".github/workflows/custom.yml", "actions/example/action.yml"):
            for update in ("major", "minor", "digest", "pin", "pinDigest"):
                with self.subTest(filename=filename, update=update):
                    resolved = _resolve_dependency({
                        **dependency, "fileName": filename, "updateType": update,
                    }, {"pinDigests": True})
                    self.assertFalse(resolved["enabled"])
                    self.assertFalse(resolved["pinDigests"])

    def test_first_party_exception_does_not_match_other_dependencies(self) -> None:
        self.assertIn("helpers:pinGitHubActionDigests", CONFIG["extends"])
        for dependency in ("actions/checkout", "jdx/mise-action",
                           "Rubio-Enterprises/.github-private",
                           "Rubio-Enterprises/other", "other/.github"):
            with self.subTest(dependency=dependency):
                resolved = _resolve_dependency({
                    "manager": "github-actions",
                    "depName": dependency, "packageName": dependency,
                    "fileName": ".github/workflows/testflight-release.yml",
                    "currentVersion": "4.0.0", "updateType": "digest",
                }, {"pinDigests": True})
                self.assertTrue(resolved.get("enabled", True))
                self.assertTrue(resolved["pinDigests"])
        resolved = _resolve_dependency({
            "manager": "custom.regex", "depName": "Rubio-Enterprises/.github",
            "fileName": "manifest.toml", "currentVersion": "4.0.0",
            "updateType": "digest",
        }, {"pinDigests": True})
        self.assertTrue(resolved.get("enabled", True))
        self.assertTrue(resolved["pinDigests"])

    def test_dev_tool_majors_are_held_and_zero_x_follows_the_pre_one_policy(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/avr",
            "manager": "pep621",
            "depName": "mypy",
            "packageName": "mypy",
            "fileName": "pyproject.toml",
            "depType": "dependency-groups",
            "currentVersion": "0.16.7",
            "updateType": "major",
        }
        for repository in (
            "Rubio-Enterprises/avr",
            "Rubio-Enterprises/standards",
            "Rubio-Enterprises/.github",
            "Rubio-Enterprises/.github-private",
        ):
            for current, update in (
                ("0.16.7", "patch"), ("v0.16.7", "minor"),
                ("0.16.7", "pin"), ("0.16.7", "major"), ("9.1.1", "major"),
            ):
                with self.subTest(repository=repository, current=current, update=update):
                    resolved = _resolve_dependency({
                        **dependency, "repository": repository,
                        "currentVersion": current, "updateType": update,
                    })
                    if update in ("minor", "patch"):
                        self.assertTrue(resolved["automerge"])
                        self.assertFalse(resolved["platformAutomerge"])
                        self.assertFalse(resolved["draftPR"])
                    else:
                        self.assertEqual(
                            {key: resolved[key] for key in HELD_POSTURE}, HELD_POSTURE
                        )
                    self.assertEqual(resolved["rangeStrategy"], "pin")
                    self.assertEqual(
                        resolved["minimumReleaseAge"], None if update == "pin" else "7 days"
                    )

    def test_typescript_seven_hold_only_affects_older_repos(self) -> None:
        dependency = {
            "repository": "Rubio-Enterprises/static-webpage-template",
            "manager": "npm",
            "depName": "typescript",
            "packageName": "typescript",
            "fileName": "package.json",
            "currentVersion": "6.0.0",
            "updateType": "major",
        }
        self.assertEqual(_resolve_dependency(dependency)["allowedVersions"], "<7")
        self.assertEqual(
            _resolve_dependency({**dependency, "currentVersion": "5.9.3"})["allowedVersions"],
            "<7",
        )
        self.assertNotIn(
            "allowedVersions",
            _resolve_dependency({**dependency, "currentVersion": "7.0.2", "updateType": "patch"}),
        )
        self.assertNotIn(
            "allowedVersions",
            _resolve_dependency({**dependency, "packageName": "typescript-eslint", "depName": "typescript-eslint"}),
        )

    def test_one_held_rule_is_last_and_cannot_be_rearmed(self) -> None:
        rules = CONFIG["packageRules"]
        held_rules = [rule for rule in rules if rule.get("draftPR") is True]
        self.assertEqual(len(held_rules), 1)
        self.assertIs(held_rules[0], rules[-1])
        self.assertEqual(rules[-1]["matchJsonata"], [HELD_UPDATE_EXPRESSION])
        self.assertEqual(
            {key: rules[-1][key] for key in HELD_POSTURE}, HELD_POSTURE
        )
        self.assertEqual(
            {key for key in rules[-1] if key.startswith("match")}, {"matchJsonata"}
        )

    def test_dashboard_approval_is_absent_from_all_local_presets(self) -> None:
        def check(value: Any) -> None:
            if isinstance(value, dict):
                self.assertNotIn("dependencyDashboardApproval", value)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
            elif isinstance(value, str):
                self.assertNotIn("approveMajorUpdates", value)
                self.assertNotIn("dependencyDashboardApproval", value)

        for path in ("default.json", "renovate.json"):
            with self.subTest(path=path):
                check(json.loads((ROOT / path).read_text(encoding="utf-8")))

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
                True,
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
                True,
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
                True,
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
                True,
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
                self.assertFalse(resolved["platformAutomerge"])
                self.assertEqual(resolved["minimumReleaseAge"], "7 days")
                self.assertEqual(
                    resolved["draftPR"], not should_merge
                )
                slugs_by_group[expected_group] = expected_slug

        self.assertEqual(len(slugs_by_group), len(set(slugs_by_group.values())))

    def test_majors_and_pre_one_pins_are_held_drafts(self) -> None:
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
                "stable major is held",
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
                "pre-one patch is automatic",
                {
                    "manager": "cargo",
                    "depName": "unstable-crate",
                    "packageName": "unstable-crate",
                    "fileName": "Cargo.toml",
                    "currentVersion": "0.9.0",
                    "updateType": "patch",
                },
                False,
            ),
            (
                "v-prefixed pre-one minor is automatic",
                {
                    "manager": "dockerfile",
                    "depName": "unstable-image",
                    "packageName": "unstable-image",
                    "fileName": "Dockerfile",
                    "currentVersion": "v0.9.0",
                    "updateType": "minor",
                },
                False,
            ),
            (
                "pre-one major is held",
                {
                    "manager": "cargo",
                    "depName": "unstable-crate",
                    "packageName": "unstable-crate",
                    "fileName": "Cargo.toml",
                    "currentVersion": "0.9.0",
                    "updateType": "major",
                },
                True,
            ),
            (
                "v-prefixed pre-one pinDigest is held",
                {
                    "manager": "dockerfile",
                    "depName": "unstable-image",
                    "packageName": "unstable-image",
                    "fileName": "Dockerfile",
                    "currentVersion": "v0.9.0",
                    "updateType": "pinDigest",
                },
                True,
            ),
        ]

        for fixture_name, dependency, held in fixtures:
            with self.subTest(fixture=fixture_name):
                resolved = _resolve_dependency(dependency)
                self.assertEqual(resolved["draftPR"], held)
                self.assertEqual(resolved["automerge"], not held)
                self.assertEqual(resolved["prConcurrentLimit"], 1 if held else 5)
                self.assertEqual(resolved["branchConcurrentLimit"], 1 if held else 5)

    def test_held_posture_survives_all_group_and_fast_lane_overrides(self) -> None:
        fixtures = (
            ("npm", "zero-npm", "package.json", "npm"),
            ("cargo", "zero-crate", "Cargo.toml", "crate"),
            ("custom.regex", "jdx/mise", ".github/workflows/e2e.yml", "github-releases"),
            ("custom.regex", "astral-sh/uv", ".github/workflows/e2e.yml", "github-releases"),
            ("npm", "typescript", "package.json", "npm"),
            ("pep621", "typer", "pyproject.toml", "pypi"),
            ("pep621", "rubio-cli-kit", "pyproject.toml", "pypi"),
            ("custom.regex", "Rubio-Enterprises/org-ops", "home/.chezmoidata/uv-tools.toml", "github-tags"),
            ("gomod", "go", "go.mod", "golang-version"),
            ("gomod", "github.com/Rubio-Enterprises/example", "go.mod", "go"),
            ("devcontainer", "ghcr.io/rubio-enterprises/features/example", ".devcontainer/devcontainer.json", "docker"),
            ("github-actions", "actions/checkout", ".github/workflows/test-gate.yml", "github-tags"),
        )
        for manager, name, filename, datasource in fixtures:
            for current, update in (
                ("1.2.3", "major"), ("v1.2.3", "major"),
                *((current, update)
                  for current in ("0.9.0", "v0.9.0")
                  for update in ("major", "pin", "digest", "pinDigest")),
            ):
                with self.subTest(name=name, current=current, update=update):
                    resolved = _resolve_dependency({
                        "manager": manager, "depName": name, "packageName": name,
                        "fileName": filename, "datasource": datasource,
                        "currentVersion": current, "updateType": update,
                    }, {"automerge": True, "platformAutomerge": True})
                    self.assertEqual(
                        {key: resolved[key] for key in HELD_POSTURE}, HELD_POSTURE
                    )

    def test_pre_one_minor_and_patch_automerge_except_under_named_holds(self) -> None:
        named_holds = {"jdx/mise", "astral-sh/uv", "go"}
        first_party = {
            "Rubio-Enterprises/org-ops",
            "github.com/Rubio-Enterprises/example",
            "ghcr.io/rubio-enterprises/features/example",
        }
        fixtures = (
            ("npm", "zero-npm", "package.json", "npm"),
            ("cargo", "zero-crate", "Cargo.toml", "crate"),
            ("custom.regex", "jdx/mise", ".github/workflows/e2e.yml", "github-releases"),
            ("custom.regex", "astral-sh/uv", ".github/workflows/e2e.yml", "github-releases"),
            ("npm", "typescript", "package.json", "npm"),
            ("pep621", "typer", "pyproject.toml", "pypi"),
            ("pep621", "rubio-cli-kit", "pyproject.toml", "pypi"),
            ("custom.regex", "Rubio-Enterprises/org-ops", "home/.chezmoidata/uv-tools.toml", "github-tags"),
            ("gomod", "go", "go.mod", "golang-version"),
            ("gomod", "github.com/Rubio-Enterprises/example", "go.mod", "go"),
            ("devcontainer", "ghcr.io/rubio-enterprises/features/example", ".devcontainer/devcontainer.json", "docker"),
            ("github-actions", "actions/checkout", ".github/workflows/test-gate.yml", "github-tags"),
            ("custom.regex", "aqua:vercel-labs/agent-browser", "home/.chezmoidata/mise_tools.toml", "github-releases"),
        )
        for manager, name, filename, datasource in fixtures:
            for current in ("0.9.0", "v0.9.0"):
                for update in ("minor", "patch"):
                    with self.subTest(name=name, current=current, update=update):
                        resolved = _resolve_dependency({
                            "manager": manager, "depName": name, "packageName": name,
                            "fileName": filename, "datasource": datasource,
                            "currentVersion": current, "updateType": update,
                        }, {"platformAutomerge": True})
                        self.assertEqual(resolved["automerge"], name not in named_holds)
                        self.assertFalse(resolved["platformAutomerge"])
                        self.assertFalse(resolved["draftPR"])
                        self.assertEqual(resolved["prConcurrentLimit"], 5)
                        self.assertEqual(resolved["branchConcurrentLimit"], 5)
                        self.assertEqual(
                            resolved["minimumReleaseAge"],
                            None if name in first_party else "7 days",
                        )

    def test_python_minor_hold_outranks_the_pre_one_rule(self) -> None:
        dependency = {
            "manager": "mise", "depName": "python", "packageName": "python",
            "fileName": "image/mise.toml", "currentVersion": "0.9.0",
        }
        self.assertFalse(_resolve_dependency({**dependency, "updateType": "minor"})["automerge"])
        self.assertTrue(_resolve_dependency({**dependency, "updateType": "patch"})["automerge"])

    def test_pre_one_rule_sits_between_the_standing_rule_and_the_named_holds(self) -> None:
        rules = CONFIG["packageRules"]
        standing = next(
            index for index, rule in enumerate(rules)
            if rule.get("description", "").startswith("Standing automerge")
        )
        pre_one = rules[standing + 1]
        self.assertEqual(
            {key: value for key, value in pre_one.items() if key != "description"},
            {
                "matchUpdateTypes": ["minor", "patch"],
                "matchCurrentVersion": "/^v?0/",
                "automerge": True,
                "platformAutomerge": False,
            },
        )
        for index, rule in enumerate(rules):
            if rule.get("automerge") is False and index != len(rules) - 1:
                with self.subTest(rule=rule.get("groupName", rule.get("matchDepNames"))):
                    self.assertGreater(index, standing + 1)

    def test_held_version_matching_uses_renovates_current_version_fallback(self) -> None:
        dependency = {
            "manager": "pep621", "depName": "example", "packageName": "example",
            "fileName": "pyproject.toml", "updateType": "pin",
        }
        for versions, held in (
            ({"currentValue": "v0.9.0"}, True),
            ({"currentValue": "^0.9", "currentVersion": "0.9.1"}, True),
            ({"currentValue": "^1", "currentVersion": "1.0.0", "lockedVersion": "0.9.1"}, True),
            ({"currentValue": "^0", "currentVersion": "0.9.1", "lockedVersion": "1.0.0"}, False),
            ({"currentValue": "1.2.3"}, False),
            ({}, False),
        ):
            with self.subTest(versions=versions):
                self.assertEqual(_resolve_dependency({**dependency, **versions})["draftPR"], held)
                patch = _resolve_dependency({**dependency, **versions, "updateType": "patch"})
                self.assertFalse(patch["draftPR"])
                self.assertTrue(patch["automerge"])
        untyped = {key: value for key, value in dependency.items() if key != "updateType"}
        self.assertTrue(_resolve_dependency({**untyped, "currentVersion": "0.9.1"})["draftPR"])

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
        self.assertFalse(resolved["platformAutomerge"])
        self.assertEqual(resolved["minimumReleaseAge"], "7 days")

    def test_go_module_directive_requires_review(self) -> None:
        directive = _resolve_dependency(
            {
                "manager": "gomod",
                "depName": "go",
                "packageName": "go",
                "fileName": "go.mod",
                "currentVersion": "1.26.6",
                "updateType": "minor",
            }
        )
        self.assertFalse(directive["automerge"])
        self.assertFalse(directive["draftPR"])

        module_dependency = _resolve_dependency(
            {
                "manager": "gomod",
                "depName": "github.com/example/dependency",
                "packageName": "github.com/example/dependency",
                "fileName": "go.mod",
                "currentVersion": "1.26.6",
                "updateType": "minor",
            }
        )
        self.assertTrue(module_dependency["automerge"])

    def test_mise_manager_tracks_workflow_fallbacks_exactly_once(self) -> None:
        manager = next(
            item for item in CONFIG["customManagers"]
            if item.get("depNameTemplate") == "jdx/mise"
        )
        matcher = _python_regex(manager["matchStrings"][0])
        workflows = ROOT / ".github" / "workflows"
        for workflow in workflows.glob("*.yml"):
            self.assertEqual(matcher.findall(workflow.read_text()), [], workflow.name)
        sample = '# renovate: datasource=github-releases depName=jdx/mise\nMISE_VERSION: "2099.1.2"'
        self.assertEqual(matcher.findall(sample), ["2099.1.2"])

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
        self.assertFalse(unrelated["platformAutomerge"])
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
                "mise CLI",
                {
                    "manager": "custom.regex",
                    "depName": "jdx/mise",
                    "packageName": "jdx/mise",
                    "fileName": ".github/workflows/e2e.yml",
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
                    "fileName": ".github/workflows/e2e.yml",
                    "currentVersion": "0.12.1",
                    "updateType": "patch",
                },
            ),
            (
                "v-prefixed pre-1.0 major",
                {
                    "manager": "github-tags",
                    "depName": "kubernetes-sigs/agent-sandbox",
                    "packageName": "kubernetes-sigs/agent-sandbox",
                    "fileName": "infrastructure/agent-sandbox.yaml",
                    "currentVersion": "v0.5.5",
                    "updateType": "major",
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

    def test_ungrouped_guards_treat_a_null_group_as_ungrouped(self) -> None:
        guarded = [
            rule for rule in CONFIG["packageRules"]
            if any("groupName" in expression for expression in rule.get("matchJsonata", []))
        ]
        self.assertEqual(len(guarded), 6)
        for rule in guarded:
            with self.subTest(group=rule["groupName"]):
                self.assertEqual(rule["matchJsonata"], [UNGROUPED_EXPRESSION])

    def test_ungrouped_npm_and_cargo_updates_join_their_rolling_groups(self) -> None:
        cases = [
            ("npm", "package.json", "6.0.0", "stable non-major npm dependencies"),
            ("npm", "package.json", "0.9.0", "pre-1.0 npm dependencies"),
            ("cargo", "Cargo.toml", "2.0.0", "stable non-major cargo dependencies"),
            ("cargo", "Cargo.toml", "0.9.0", "pre-1.0 cargo dependencies"),
        ]
        for manager, file_name, version, group in cases:
            for update_type in ("minor", "patch"):
                with self.subTest(manager=manager, version=version, update_type=update_type):
                    resolved = _resolve_dependency(
                        {
                            "manager": manager,
                            "depName": "example",
                            "packageName": "example",
                            "fileName": file_name,
                            "currentVersion": version,
                            "updateType": update_type,
                        }
                    )
                    self.assertEqual(resolved["groupName"], group)

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
        self.assertTrue(resolved_pre_one["automerge"])
        self.assertFalse(resolved_pre_one["draftPR"])

    def test_mixed_runtime_group_merges_unless_a_member_is_held(self) -> None:
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
        self.assertTrue(zero_member["automerge"])
        self.assertEqual(zero_member["minimumReleaseAge"], "7 days")

        held_member = _resolve_dependency(
            {
                "manager": "pep621",
                "depName": "typer",
                "packageName": "typer",
                "fileName": "pyproject.toml",
                "currentVersion": "0.16.0",
                "updateType": "pin",
            }
        )
        self.assertEqual(stable_member["groupSlug"], held_member["groupSlug"])
        self.assertFalse(
            all(member["automerge"] for member in (stable_member, held_member))
        )

    def test_manual_overrides_follow_the_standing_safe_rule(self) -> None:
        rules = CONFIG["packageRules"]
        standing = next(
            index
            for index, rule in enumerate(rules)
            if rule.get("description", "").startswith("Standing automerge")
        )
        manual_groups = {
            "pre-1.0 dependency pins",
            "mise-cli",
            "uv-cli",
        }
        for group_name in ("pre-1.0 npm dependencies", "pre-1.0 cargo dependencies"):
            with self.subTest(group=group_name):
                grouping = _rule_with_group(group_name)
                self.assertNotIn("automerge", grouping)
                self.assertNotIn("platformAutomerge", grouping)
        python_minor = next(
            index
            for index, rule in enumerate(rules)
            if rule.get("matchDepNames") == ["python"]
            and rule.get("matchUpdateTypes") == ["minor"]
        )
        self.assertGreater(python_minor, standing)
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
        self.assertNotIn("minimumReleaseAge", group_rule)

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
        self.assertFalse(fast_lane["platformAutomerge"])
        self.assertNotIn("minimumReleaseAge", fast_lane)

    def test_tool_runtime_pypi_updates_wait_the_uv_release_age(self) -> None:
        # uv projects set exclude-newer to the global age, so a younger PyPI release cannot lock.
        group_rule = _rule_with_group("tool runtime dependencies")
        for name in group_rule["matchDepNames"]:
            for update in group_rule["matchUpdateTypes"]:
                for current in ("1.2.3", "0.27.1"):
                    with self.subTest(name=name, update=update, current=current):
                        resolved = _resolve_dependency({
                            "manager": "pep621", "datasource": "pypi",
                            "depName": name, "packageName": name,
                            "fileName": "pyproject.toml", "depType": "project.dependencies",
                            "currentVersion": current, "updateType": update,
                        })
                        self.assertEqual(resolved["minimumReleaseAge"], CONFIG["minimumReleaseAge"])

    def test_git_sourced_kit_keeps_the_first_party_fast_lane(self) -> None:
        # Renovate maps a uv git source on github.com to github-tags with the owner path.
        kit = {
            "manager": "pep621", "datasource": "github-tags",
            "depName": "rubio-cli-kit", "packageName": "Rubio-Enterprises/rubio-cli-kit",
            "fileName": "pyproject.toml", "depType": "project.dependencies",
            "currentValue": "v1.2.4", "updateType": "patch",
        }
        typer = {
            "manager": "pep621", "datasource": "pypi",
            "depName": "typer", "packageName": "typer",
            "fileName": "pyproject.toml", "depType": "project.dependencies",
            "currentVersion": "0.27.1", "updateType": "minor",
        }
        resolved_kit = _resolve_dependency(kit)
        resolved_typer = _resolve_dependency(typer)
        self.assertIsNone(resolved_kit["minimumReleaseAge"])
        self.assertTrue(resolved_kit["automerge"])
        self.assertEqual(resolved_typer["minimumReleaseAge"], "7 days")
        self.assertEqual(resolved_kit["groupSlug"], resolved_typer["groupSlug"])

    def test_no_rule_shortens_the_soak_for_resolved_pypi_versions(self) -> None:
        # pin writes the locked version and lock maintenance lets uv choose; both stay in uv's cutoff.
        python_managers = {"pep621", "pip_requirements", "pip_setup", "poetry", "pipenv"}
        non_resolving = {"pin", "lockFileMaintenance"}
        offenders = []
        for rule in CONFIG["packageRules"]:
            if "minimumReleaseAge" not in rule:
                continue
            age = rule["minimumReleaseAge"]
            if age is not None and int(age.split()[0]) >= int(CONFIG["minimumReleaseAge"].split()[0]):
                continue
            if "pypi" not in rule.get("matchDatasources", ["pypi"]):
                continue
            if not python_managers & set(rule.get("matchManagers", python_managers)):
                continue
            if not set(rule.get("matchUpdateTypes", ["minor"])) - non_resolving:
                continue
            offenders.append(rule.get("description", "")[:80])
        self.assertEqual(offenders, [])

    def test_first_party_tool_pin_fast_lane_excludes_zero_x_updates(self) -> None:
        pin_rule = _rule_with_group("first-party tool pins")
        self.assertEqual(
            set(pin_rule["matchUpdateTypes"]),
            {"minor", "patch", "pin", "digest", "pinDigest"},
        )
        self.assertEqual(pin_rule["matchCurrentVersion"], "!/^v?0/")
        self.assertTrue(pin_rule["automerge"])
        self.assertFalse(pin_rule["platformAutomerge"])
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

    def test_flux_uses_upstream_file_patterns(self) -> None:
        self.assertNotIn("flux", CONFIG)

    def test_retired_image_has_no_custom_claude_manager_or_rule(self) -> None:
        self.assertFalse(any(
            manager.get("depNameTemplate") == CLAUDE_CODE_DEP
            for manager in CONFIG["customManagers"]
        ))
        self.assertFalse(any(
            CLAUDE_CODE_DEP in rule.get("matchPackageNames", [])
            for rule in CONFIG["packageRules"]
        ))

    def test_agent_sandbox_has_no_package_specific_group(self) -> None:
        self.assertFalse(any(
            rule.get("groupName") == "agent-sandbox"
            or set(rule.get("matchPackageNames", [])) & set(AGENT_SANDBOX_DEPS)
            for rule in CONFIG["packageRules"]
        ))


class FirstPartyReleaseFastLaneTests(unittest.TestCase):
    """Org-published releases skip the third-party soak; look-alikes do not."""

    BASELINE = {
        "manager": "dockerfile",
        "datasource": "docker",
        "depName": "ghcr.io/rubio-enterprises/base",
        "packageName": "ghcr.io/rubio-enterprises/base",
        "fileName": "Dockerfile",
        "repository": "Rubio-Enterprises/todoist-classify",
    }

    def test_own_base_image_major_skips_soak_but_stays_held(self) -> None:
        resolved = _resolve_dependency(
            {**self.BASELINE, "updateType": "major", "currentVersion": "2.0.0"}
        )
        self.assertIsNone(resolved["minimumReleaseAge"])
        self.assertEqual({key: resolved[key] for key in HELD_POSTURE}, HELD_POSTURE)

    def test_own_base_image_patch_skips_soak(self) -> None:
        resolved = _resolve_dependency(
            {**self.BASELINE, "updateType": "patch", "currentVersion": "3.0.0"}
        )
        self.assertIsNone(resolved["minimumReleaseAge"])

    def test_owned_github_sources_skip_soak(self) -> None:
        for datasource, package in (
            ("github-tags", "Rubio-Enterprises/some-action"),
            ("github-releases", "Rubio-Enterprises/rubio-cli-kit"),
            ("git-tags", "https://github.com/Rubio-Enterprises/rubio-cli-kit"),
            ("go", "github.com/Rubio-Enterprises/scaleset"),
        ):
            with self.subTest(datasource=datasource, package=package):
                resolved = _resolve_dependency(
                    {
                        "manager": "custom.regex",
                        "datasource": datasource,
                        "depName": package,
                        "packageName": package,
                        "updateType": "minor",
                        "currentVersion": "1.2.3",
                    }
                )
                self.assertIsNone(resolved["minimumReleaseAge"])

    def test_look_alikes_and_unowned_namespaces_keep_the_soak(self) -> None:
        for datasource, package in (
            # Docker Hub namespace: first-come, not provably the org's.
            ("docker", "rubio-enterprises/base"),
            ("docker", "docker.io/rubio-enterprises/image"),
            # Another registry, and a look-alike GHCR owner.
            ("docker", "ghcr.io/rubio-enterprises-x/image"),
            ("docker", "quay.io/rubio-enterprises/image"),
            # Look-alike GitHub owner, and an owned path on the wrong datasource.
            ("github-tags", "Rubio-Enterprises-x/action"),
            ("npm", "Rubio-Enterprises/thing"),
            ("pypi", "rubio-enterprises"),
        ):
            with self.subTest(datasource=datasource, package=package):
                resolved = _resolve_dependency(
                    {
                        "manager": "custom.regex",
                        "datasource": datasource,
                        "depName": package,
                        "packageName": package,
                        "updateType": "minor",
                        "currentVersion": "1.2.3",
                    }
                )
                self.assertEqual(resolved["minimumReleaseAge"], "7 days")


class UndatedRegistryImageTests(unittest.TestCase):
    """Renovate dates docker tags only on Docker Hub (.github-private#722).

    Images on any other registry pass without the age wait and a person merges
    them; Docker Hub and first-party images keep their behaviour.
    """

    NON_HUB = (
        "ghcr.io/astral-sh/uv",
        "ghcr.io/immich-app/immich-server",
        "lscr.io/linuxserver/grocy",
        "quay.io/keycloak/keycloak",
        "mcr.microsoft.com/playwright/python",
        "cr.agentgateway.dev/agentgateway",
        "registry.example.com:5000/team/image",
        "ghcr.io/rubio-enterprises-x/image",
    )

    @staticmethod
    def _image(name: str, update: str, version: str) -> dict[str, Any]:
        return _resolve_dependency({
            "manager": "docker-compose", "datasource": "docker",
            "depName": name, "packageName": name, "fileName": "compose.yaml",
            "currentVersion": version, "updateType": update,
        })

    def test_other_registries_skip_the_wait_and_need_a_person(self) -> None:
        for name in self.NON_HUB:
            for version in ("2.7.5", "0.8.15"):
                for update in ("minor", "patch", "digest", "pinDigest"):
                    with self.subTest(image=name, version=version, update=update):
                        resolved = self._image(name, update, version)
                        self.assertEqual(resolved["minimumReleaseAgeBehaviour"], "timestamp-optional")
                        # A release that does carry a date still waits its seven days.
                        self.assertEqual(resolved["minimumReleaseAge"], "7 days")
                        self.assertFalse(resolved["automerge"])
                        self.assertFalse(resolved["platformAutomerge"])
                        self.assertNotIn("groupSlug", resolved)
                        # Only the held rule makes drafts: pre-1.0 digests, never a stable patch.
                        held = version.startswith("0") and update in ("digest", "pinDigest")
                        self.assertEqual(resolved["draftPR"], held)
            with self.subTest(image=name, update="major"):
                resolved = self._image(name, "major", "2.7.5")
                self.assertEqual(resolved["minimumReleaseAgeBehaviour"], "timestamp-optional")
                self.assertEqual({key: resolved[key] for key in HELD_POSTURE}, HELD_POSTURE)

    OFFICIAL = ("node", "library/node", "docker.io/library/node", "index.docker.io/library/postgres",
                "registry-1.docker.io/library/redis", "python", "Postgres")
    THIRD_PARTY_HUB = ("index.docker.io/grafana/grafana", "registry-1.docker.io/grafana/loki",
                       "grafana/grafana", "dgtlmoon/sockpuppetbrowser", "docker.io/ollama/ollama",
                       "library-fork/postgres")

    def test_docker_hub_tag_updates_stay_dated_and_soaked(self) -> None:
        for name in self.OFFICIAL + self.THIRD_PARTY_HUB:
            for update in ("minor", "patch"):
                if name == "python" and update == "minor":
                    continue
                with self.subTest(image=name, update=update):
                    resolved = self._image(name, update, "24.22.2")
                    self.assertEqual(resolved["minimumReleaseAgeBehaviour"], "timestamp-required")
                    self.assertEqual(resolved["minimumReleaseAge"], "7 days")
                    self.assertTrue(resolved["automerge"])

    def test_official_image_digests_skip_the_missing_date_and_automerge(self) -> None:
        for name in self.OFFICIAL:
            for update in ("digest", "pinDigest"):
                with self.subTest(image=name, update=update):
                    resolved = self._image(name, update, "16.15-alpine")
                    self.assertEqual(resolved["minimumReleaseAgeBehaviour"], "timestamp-optional")
                    self.assertEqual(resolved["minimumReleaseAge"], "7 days")
                    self.assertTrue(resolved["automerge"])
                    self.assertFalse(resolved["platformAutomerge"])
                    self.assertFalse(resolved["draftPR"])

    def test_third_party_hub_digests_skip_the_missing_date_and_need_a_person(self) -> None:
        for name in self.THIRD_PARTY_HUB:
            for update in ("digest", "pinDigest"):
                with self.subTest(image=name, update=update):
                    resolved = self._image(name, update, "latest")
                    self.assertEqual(resolved["minimumReleaseAgeBehaviour"], "timestamp-optional")
                    self.assertFalse(resolved["automerge"])
                    self.assertFalse(resolved["platformAutomerge"])

    def test_hub_digest_rules_leave_other_registries_and_first_party_alone(self) -> None:
        for name in self.NON_HUB:
            with self.subTest(image=name):
                self.assertFalse(self._image(name, "digest", "2.7.5")["automerge"])
        first_party = self._image("ghcr.io/rubio-enterprises/base", "digest", "3.0.0")
        self.assertEqual(first_party["minimumReleaseAgeBehaviour"], "timestamp-required")
        self.assertTrue(first_party["automerge"])

    def test_docker_hub_majors_and_pre_one_digests_stay_held(self) -> None:
        for name in ("postgres", "grafana/grafana"):
            with self.subTest(image=name):
                major = self._image(name, "major", "16.15.0")
                self.assertEqual({key: major[key] for key in HELD_POSTURE}, HELD_POSTURE)
                pre_one = self._image(name, "digest", "0.9.1")
                self.assertEqual({key: pre_one[key] for key in HELD_POSTURE}, HELD_POSTURE)

    def test_docker_hub_python_minor_keeps_its_hold(self) -> None:
        python_minor = self._image("python", "minor", "3.14.6")
        self.assertEqual(python_minor["minimumReleaseAgeBehaviour"], "timestamp-required")
        self.assertEqual(python_minor["groupSlug"], "python-runtime")
        self.assertFalse(python_minor["automerge"])
        self.assertTrue(self._image("node", "minor", "24.18.0")["automerge"])

    def test_first_party_images_keep_their_fast_lane(self) -> None:
        for name in ("ghcr.io/rubio-enterprises/base", "ghcr.io/Rubio-Enterprises/runner",
                     "ghcr.io/rubio-enterprises/standards"):
            for update in ("minor", "patch", "digest", "pinDigest"):
                with self.subTest(image=name, update=update):
                    resolved = self._image(name, update, "3.0.0")
                    self.assertEqual(resolved["minimumReleaseAgeBehaviour"], "timestamp-required")
                    self.assertIsNone(resolved["minimumReleaseAge"])
                    self.assertTrue(resolved["automerge"])
            with self.subTest(image=name, update="major"):
                resolved = self._image(name, "major", "3.0.0")
                self.assertEqual({key: resolved[key] for key in HELD_POSTURE}, HELD_POSTURE)

    def test_rule_disarms_both_automerge_rules_and_precedes_the_held_rule(self) -> None:
        rules = CONFIG["packageRules"]
        index = next(i for i, rule in enumerate(rules) if "minimumReleaseAgeBehaviour" in rule)
        automerge_rules = [
            i for i, rule in enumerate(rules)
            if rule.get("automerge") is True and "matchCurrentVersion" in rule and "matchManagers" not in rule
            and "matchDepNames" not in rule
        ]
        self.assertEqual(len(automerge_rules), 2)
        self.assertGreater(index, max(automerge_rules))
        # Other registries, then the Docker Hub digest pair, then the held rule.
        self.assertEqual(index, len(rules) - 4)
        hub, official = rules[index + 1], rules[index + 2]
        self.assertFalse(hub["automerge"])
        self.assertTrue(official["automerge"])
        self.assertEqual(hub["matchUpdateTypes"], ["digest", "pinDigest"])
        self.assertEqual(official["matchUpdateTypes"], ["digest", "pinDigest"])
        self.assertNotIn("groupName", rules[index])
        self.assertNotIn("minimumReleaseAge", rules[index])


class MiseOwnedToolTests(unittest.TestCase):
    def test_manifest_copies_are_frozen_but_regex_floor_and_schema_are_live(self) -> None:
        cases = [
            ("npm", "npm", "@biomejs/biome", "package.json", False),
            ("npm", "npm", "@biomejs/biome", "app/package.json", False),
            ("pep621", "pypi", "ruff", "pyproject.toml", False),
            ("pip_requirements", "pypi", "ruff", "requirements-dev.txt", False),
            ("poetry", "pypi", "ruff", "pyproject.toml", False),
            ("custom.regex", "npm", "@biomejs/biome", "template/.mise.toml.jinja", True),
            ("custom.regex", "npm", "@biomejs/biome", "template/biome.json.fragment.jinja", True),
            ("custom.regex", "pypi", "ruff", "template/.mise.toml.jinja", True),
            ("pep621", "pypi", "pytest", "pyproject.toml", True),
        ]
        for manager, datasource, package, filename, enabled in cases:
            for repo in ("native-example", "extended-fork-example", "standards"):
                with self.subTest(manager=manager, package=package, repo=repo):
                    result = _resolve_dependency({
                        "manager": manager, "datasource": datasource,
                        "packageName": package, "depName": package,
                        "fileName": filename, "repository": f"Rubio-Enterprises/{repo}",
                        "currentVersion": "2.5.14", "updateType": "patch",
                        "depType": "dependency-groups",
                    })
                    self.assertEqual(result.get("enabled", True), enabled)


if __name__ == "__main__":
    unittest.main()


class DevcontainerPrebuildTests(unittest.TestCase):
    def test_floating_images_are_disabled_for_all_docker_managers(self) -> None:
        for manager in ("devcontainer", "dockerfile", "docker-compose"):
            for update in ("major", "minor", "patch", "digest", "pinDigest"):
                dependency = {
                    "manager": manager, "datasource": "docker",
                    "depName": "ghcr.io/rubio-enterprises/bookmark-scraper/devcontainer",
                    "packageName": "ghcr.io/rubio-enterprises/bookmark-scraper/devcontainer",
                    "currentVersion": "main", "updateType": update,
                }
                with self.subTest(manager=manager, update=update):
                    self.assertFalse(_resolve_dependency(dependency)["enabled"])
                    matching = [
                        rule for rule in CONFIG["packageRules"]
                        if _rule_matches(rule, dependency, {}) and "pinDigests" in rule
                    ]
                    self.assertEqual([rule["pinDigests"] for rule in matching], [False])

    def test_features_and_other_images_remain_managed(self) -> None:
        for package in (
            "ghcr.io/devcontainers/features/node",
            "ghcr.io/rubio-enterprises/agent-workspace",
            "ghcr.io/rubio-enterprises/bookmark-scraper/devcontainer-extra",
            "ghcr.io/rubio-enterprises-other/repo/devcontainer",
            "ghcr.io/another-owner/repo/devcontainer",
            "mcr.microsoft.com/devcontainers/base",
        ):
            dependency = {
                "manager": "devcontainer", "datasource": "docker",
                "depName": package, "packageName": package,
                "fileName": ".github/.devcontainer/devcontainer.json",
                "currentVersion": "1.2.3", "updateType": "patch",
            }
            with self.subTest(package=package):
                self.assertTrue(_resolve_dependency(dependency).get("enabled", True))

    def test_build_definition_does_not_run_runtime_lockfile_refresh(self) -> None:
        for rule in CONFIG["packageRules"]:
            if "postUpgradeTasks" in rule:
                self.assertFalse(_rule_matches(rule, {
                    "manager": "devcontainer", "datasource": "docker", "depName": "x",
                    "fileName": ".github/.devcontainer/devcontainer.json",
                }, {}))


class HostFirstRenovateTests(unittest.TestCase):
    def test_no_devcontainer_upgrade_task_or_feature_rule(self) -> None:
        self.assertNotIn("devcontainer upgrade", json.dumps(CONFIG))
        self.assertFalse(any(
            "devcontainer" in rule.get("matchManagers", [])
            for rule in CONFIG["packageRules"]
        ))

    def test_mise_manager_matches_image_mise_toml(self) -> None:
        self.assertEqual(CONFIG.get("mise", {}).get("managerFilePatterns"), ["/^image/mise\\.toml$/"])
