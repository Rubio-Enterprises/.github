"""Behavioral contract tests for the organization Renovate preset."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
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


class RenovateConfigContractTests(unittest.TestCase):
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
        self.assertTrue(manager["pinDigests"])
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


if __name__ == "__main__":
    unittest.main()
