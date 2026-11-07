import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def validate(directory):
    reports = sorted(directory.rglob("*.xml"))
    if not reports:
        raise ValueError(f"{directory}: no JUnit reports")
    executed = skipped = failed = 0
    seen = set()
    for report in reports:
        resolved = report.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        root = ET.parse(report).getroot()
        if root.tag not in ("testsuite", "testsuites"):
            raise ValueError(f"{report}: unexpected JUnit root {root.tag}")
        # Parent suite totals include their descendants and must not be summed
        cases = list(root.iter("testcase"))
        skipped += sum(case.find("skipped") is not None for case in cases)
        executed += sum(case.find("skipped") is None for case in cases)
        failed += sum(element.tag in ("failure", "error") for element in root.iter())
        for suite in root.iter():
            if suite.tag in ("testsuite", "testsuites"):
                for attribute in ("failures", "errors"):
                    count = int(suite.get(attribute, "0"))
                    if count < 0:
                        raise ValueError(f"{report}: negative {attribute} count")
                    if count:
                        raise ValueError(f"{report}: reports {count} {attribute}")
    print(f"{directory}: {executed} executed, {skipped} skipped, {failed} failures/errors")
    if not executed or failed:
        raise ValueError(f"{directory}: require at least one executed testcase and no failures/errors")
    return executed


def main():
    concerns = os.environ["JUNIT_CONCERNS"].split()
    if any(concern not in ("test", "e2e") for concern in concerns):
        raise ValueError("JUnit concerns must be test and/or e2e")
    if os.environ["SUITE_OUTCOME"] != "success":
        raise ValueError(f"Lifecycle step did not succeed: {os.environ['SUITE_OUTCOME']}")
    for concern in dict.fromkeys(concerns):
        validate(Path("reports") / concern)
    if not concerns:
        print("No test/e2e Lifecycle Scripts apply")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, ET.ParseError) as error:
        sys.exit(f"::error::{error}")
