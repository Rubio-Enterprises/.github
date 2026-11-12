# Test Gate

The organization injects `.github/workflows/test-gate.yml` on pull requests and merge
groups. Terraform requires its workflow identity, not an interchangeable Actions
status. Its required result is the aggregate job `test-gate`. Execution happens in
`test-gate-linux` and `test-gate-macos`. Dependency review remains a
separate public-PR job, outside that required context.

## Execution contract

`project-route.yml` with `route: test` reads `ci-platform`, `ci-size`, optional
`ci-test-route` and `ci-test-macos`. The Linux job uses the returned labels unchanged,
including ARM, with a 120-minute limit. The macOS job has a 60-minute limit and uses
`macos-runner`. Public tests stay hosted. Route failures fail the aggregate rather
than silently skipping the required result. See
[project routing](reusable-workflows/project-route.md).

Checkout retains full history and does not persist credentials. A CI Image runs
`bin/setup.sh` and the Lifecycle Scripts directly. Other Linux runners execute
`just build`, `just setup` and each existing recipe among `test typecheck e2e package`.
The macOS job has two modes, neither using Docker:
- **Shape mode:** when the whole Test Gate runs on macOS, install just and bootstrap
  mise through `actions/macos-tools` with `provision: false`. Run `setup-macos`
  when listed by `just --summary`, otherwise `setup-native`, then only `test`.
  This preserves today's coverage. The small shared `run-recipe.sh` compatibility
  script is removed after the recipe rename sweep.
- **Leg mode:** a Linux test route can declare additional concerns in the organization
  multi-select property `ci-test-macos`, through reviewed OpenTofu configuration.
  Provision locked repository tools and the base's four runners through
  `actions/macos-tools` with `provision: true`, run `bin/setup.sh`, then each declared
  executable `bin/<concern>.sh` directly, in `test typecheck e2e package` order.
  A declared missing or non-executable script fails. Locked installation fails if
  macOS lock entries are missing. The Linux job remains unchanged.

Both central macOS jobs restore and save mise's content-addressed download store at
`${{ runner.temp }}/macos-tools/cache/downloads-cas`, keyed by runner OS, architecture
and `image/mise.lock`, without a restore prefix. Setup and Lifecycle steps receive
`GITHUB_TOKEN` from `github.token`. The shared `bin/macos-env.sh` must set
`MISE_CACHE_DIR=$RUNNER_TEMP/macos-tools/cache` in CI. Existing repository helpers
that still use a checkout-local cache simply do not consume this restored store.
Installed tools are not cached.

The aggregate requires every expected leg to succeed and every unexpected leg to be
skipped. Linux alone, Linux with a declared macOS leg, and macOS shape are distinct
cases. Failed, cancelled or unexpectedly skipped legs fail the aggregate. An existing
reasoned Opt-out still succeeds through the applicable leg's policy step.

Script existence declares applicability. Non-executable scripts fail. No test/e2e
script means no JUnit obligation, not an automatic Opt-out. Tests clear their own
report directory before execution. Linux non-image jobs tear down Compose services
and dependency volumes even on failure; external organization caches survive.

Before any candidate download or Docker build, GitHub-hosted Linux non-image jobs
call `actions/prepare-hosted-docker`. It removes only the host Android and .NET SDKs.
All Linux Lifecycle work runs inside the dev image, so it uses neither host SDK.
Self-hosted, CI Image and macOS runners are untouched. The action logs free bytes
before and after cleanup; the gate also logs disk use after dependency setup.
The preparation action's temporary central checkout is removed before repository
setup. Setup asserts its absence on every runner kind. The JUnit action is checked
out only after Lifecycle Scripts finish, so tree-walking tests never see central
repository files.
The other central hosted Docker jobs use the same temporary sparse checkout,
local action call and immediate removal before Docker work, including ARM jobs.

The November 2026 Ubuntu 26.04 hosted measurement had 15.0 GiB free on `/` and
no separate `/mnt` disk. Docker used `/var/lib/docker` with the containerd
`overlayfs` snapshotter. The candidate base occupied 4.41 GB including unpacked
layers; karakeep's dev image occupied 6.99 GB including shared layers. Building
left only 5.62 GiB free and `pnpm install --frozen-lockfile` exhausted it.
The two unused SDK trees occupied 16.8 GiB together. Reclaiming them targets at
least 10 GiB of headroom after karakeep setup, without relocating Docker, pruning
job data, changing routes or depending on a third-party cleanup action.

The `actions/junit-gate` composite uses Python's XML parser, independently of any
Base Image helper version. For each required concern it recursively reads XML under
`reports/test/` or `reports/e2e/`, counts actual `testcase` elements without a
`skipped` child, and requires at least one executed case and no failures/errors.
Parent suite totals are not added to child cases; aliases of a report file are
counted once. Malformed XML, missing reports, failed suite summaries and an
unsuccessful Lifecycle step all fail. This reuses the XML approach in `e2e.yml`
rather than a new regex or reporting dependency.

## Default-branch execution

`default-branch-check.yml` calls this workflow as `test-gate-main` without any
qualification inputs or inherited secrets. The image and cache publishers select
default-branch pushes and dispatches; see
[default-branch verification](standards-check.md#default-branch-verification).
The production route, policy reader, applicable Lifecycle concerns and executed
JUnit validation are unchanged, including native shapes and declared macOS legs.
The reader-App token steps are qualification-only. Normal checkout/setup use the
automatic read-only job token, so the publisher callers need no additional secrets.
Dependency review remains public-PR-only, not a whole-tree push check.

The aggregate remains named `test-gate`, under the `test-gate-main` caller prefix.
These extra default-branch results are not required by a ruleset. Existing
reasoned policy exceptions still apply. Every selected run executes the checks;
no evidence shortcut, automatic retry or red-run issue/label/notification exists.
The caller's concurrency group remains occupied through completion.

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
selected existing CI Image runner. Optional `qualification-platform` selects
`linux` or `macos`. Its empty default infers macOS when `qualification-runner`
contains `macos`, otherwise Linux, preserving existing callers. A small qualification
job computes this once for the execution jobs and aggregate. Optional
`qualification-macos-concerns` defaults to empty (shape mode); a non-empty list on
a macOS qualification selects leg mode. Only the selected qualification leg runs.
Qualification runner labels are caller-owned:
real property routing is separately covered by contract tests and injection canaries.

For an unmerged central change, point the workflow ref and
`qualification-central-ref` at its branch. The latter checks out the candidate
JUnit, macOS tools and hosted Docker preparation actions for local composite calls;
production checks out the same actions from central main. Each checkout exists only while
its action is needed. `actions/checkout` requires a path inside `GITHUB_WORKSPACE`,
so the early checkout must be removed rather than placed under runner temp.
A conditional remote action reference would still load its manifest before the
runner evaluates the condition, breaking pre-merge qualification when the action
does not exist on main yet.
The source repository keeps an independent bootstrap test gate so its consumer
no-op guard cannot qualify a broken central implementation.

The optional same-run `candidate-base` artifact has the Release Check format:
`base-candidate.tar.gz` plus `source.tar.gz`. Docker x86_64 legs load the candidate
and prevent `just build --pull` replacing it; macOS shape uses its config and
macOS leg provisioning uses its mise pins and lock.
Omit this artifact for ARM or existing CI Image runners, whose candidate image is
qualified separately. Do not claim these qualifications prove injection, secret
access, or new image pins: the shadow/canary rollout owns that evidence.
