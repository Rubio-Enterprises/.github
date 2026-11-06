# Test Gate

The organization injects `.github/workflows/test-gate.yml` on pull requests and merge
groups. Terraform requires its workflow identity, not an interchangeable Actions
status. Its single required job is named `test-gate`. Dependency review remains a
separate public-PR job, outside that required context.

## Execution contract

`project-route.yml` with `route: test` reads `ci-platform`, `ci-size` and optional
`ci-test-route`. The gate uses the returned labels unchanged, including ARM. Route
failure schedules a small failure-reporting job rather than skipping the required
context. Public tests stay hosted. See
[project routing](reusable-workflows/project-route.md).

Checkout retains full history and does not persist credentials. A CI Image runs
`bin/setup.sh` and the Lifecycle Scripts directly. Other Linux runners execute
`just build`, `just setup` and each existing recipe among `test typecheck e2e package`.
Native macOS runs `just setup-native` and only `test`, preserving the native gate's
coverage; it never logs into Docker or builds a dev image. Missing native mise is
installed from a checksum-pinned release.

Script existence declares applicability. Non-executable scripts fail. No test/e2e
script means no JUnit obligation, not an automatic Opt-out. Tests clear their own
report directory before execution. Linux non-image jobs tear down Compose services
and dependency volumes even on failure; external organization caches survive.

The `actions/junit-gate` composite uses Python's XML parser, independently of any
Base Image helper version. For each required concern it recursively reads XML under
`reports/test/` or `reports/e2e/`, counts actual `testcase` elements without a
`skipped` child, and requires at least one executed case and no failures/errors.
Parent suite totals are not added to child cases; aliases of a report file are
counted once. Malformed XML, missing reports, failed suite summaries and an
unsuccessful Lifecycle step all fail. This reuses the XML approach in `e2e.yml`
rather than a new regex or reporting dependency.

## Exceptions

Missing `.github/standards.toml` means enforcement is enabled. Either independent
exception may contain only a non-empty string reason:

```toml
[test-gate]
off = "Documentation only; no runnable Lifecycle concern"

[app-image]
off = "The base publisher is not an App Image"
```

The gate validates both exception value types, honors only `test-gate.off`, and
reports an Opt-out notice without running setup/tests. Empty/non-string reasons,
non-table sections and malformed TOML fail. The policy reader needs Python 3.11+
when this file exists; CI Images select the Base Image's interpreter independently
of older repository pins. JUnit validation uses only the Python XML standard library.
The package and org audit own the other policy readers. Retired workflow comments
do not opt out.

Only read-only contents/package permissions are requested. Test Gate does not
publish images, mint OIDC, persist reader credentials or forward all secrets.
Fork/Dependabot credentials retain GitHub's restrictions. Actual ruleset secret
availability and event semantics must be proven by rollout canaries.

## Release Check qualification

Standards' Release Check can call the *same* workflow on representative repositories:

```yaml
uses: Rubio-Enterprises/.github/.github/workflows/test-gate.yml@main
with:
  qualification-repository: org-ops
  qualification-runner: '["linux-x64-large"]'
  qualification-central-ref: main
  candidate-base-artifact: candidate-base
secrets: inherit
```

Qualification is accepted only from `Rubio-Enterprises/standards`. A reader App
token scoped to the representative performs checkout, then normal production
policy/setup/Lifecycle steps run. Optional `qualification-ref` selects a ref.
`qualification-ci-image: true` selects direct script execution on an explicitly
selected existing CI Image runner. Qualification runner labels are caller-owned:
real property routing is separately covered by contract tests and injection canaries.

For an unmerged central change, point the workflow ref and
`qualification-central-ref` at its branch. The latter checks out only the candidate
JUnit action for a local composite call; production checks out the same action from
central main. A conditional remote action reference would still load its manifest
before the runner evaluates the condition, breaking pre-merge qualification when
the action does not exist on main yet.
The source repository keeps an independent bootstrap test gate so its consumer
no-op guard cannot qualify a broken central implementation.

The optional same-run `candidate-base` artifact has the Release Check format:
`base-candidate.tar.gz` plus `source.tar.gz`. Docker x86_64 legs load the candidate
and prevent `just build --pull` replacing it; native macOS legs use its config.
Omit this artifact for ARM or existing CI Image runners, whose candidate image is
qualified separately. Do not claim these qualifications prove injection, secret
access, or new image pins: the shadow/canary rollout owns that evidence.
