# Test Gate

The organization injects `.github/workflows/test-gate.yml` on pull requests and merge
groups. Terraform requires its workflow identity, not an interchangeable Actions
status. Its required result is the aggregate job `test-gate`. Execution happens in
`test-gate-linux` and `test-gate-macos`. Dependency review remains a
separate public-PR job, outside that required context.

## Execution contract

This workflow calls `project-route.yml` with `route: test` and
`inspect-image-inputs: true`. Other router callers default to no image-input
inspection. The router reads `ci-platform`, `ci-size`, optional
`ci-test-route` and `ci-test-macos`. The Linux job uses the returned labels unchanged,
including ARM, with a 120-minute limit. The macOS job has a 60-minute limit and uses
`macos-runner`. Public tests stay hosted. Route failures fail the aggregate rather
than silently skipping the required result. See
[project routing](reusable-workflows/project-route.md).

For admitted Linux small/medium tests, the shared router sends image-changing pull
requests and merge groups to `RUNNERS.docker` with `ci-image=false`. Unknown or
capped changed-file lists take the same safe route. `test-gate-linux` builds the
checked-out Dev Image and runs every existing concern inside it. It retains hosted
disk preparation, setup and JUnit validation. Unrelated changes keep the CI Image;
public repositories already use Compose. ARM and Docker test overrides already
build the changed image and retain their runners; macOS shapes and declared macOS
legs are unchanged. No file-detection logic is duplicated in this workflow.

Checkout retains full history and does not persist credentials. A CI Image runs
`bin/setup.sh` and the Lifecycle Scripts directly. Other Linux runners execute
`just build`, `just setup` and each existing recipe among `test typecheck e2e package`.
Both jobs use the [Lifecycle dispatcher](../actions/lifecycle/README.md) for setup
and single-recipe execution. The workflow retains concern selection, script checks,
report cleanup, outputs and log groups. The dispatcher is staged alongside the
policy reader in runner temp before the central checkout is removed.

The macOS job has two modes, neither using Docker:
- **Shape mode:** when the whole Test Gate runs on macOS, install just and bootstrap
  mise through `actions/macos-tools` with `provision: false`. Run `setup-macos`
  when listed by `just --summary`, otherwise `setup-native`, then each existing
  concern through `just test`, `just typecheck`, `just e2e` and `just package`, in
  that order. The shared policy reader selects the same applicable concerns as
  Linux, including per-concern Opt-outs. Both `test` and `e2e` require executed
  JUnit cases. No concern is skipped because the runner is macOS. The small shared
  `run-recipe.sh` compatibility script is removed after the recipe rename sweep.
- **Leg mode:** a Linux test route can declare additional concerns in the organization
  multi-select property `ci-test-macos`, through reviewed OpenTofu configuration.
  Provision locked repository tools and the base's four runners through
  `actions/macos-tools` with `provision: true`, run `bin/setup.sh`, then each declared
  executable `bin/<concern>.sh` directly, in `test typecheck e2e package` order.
  A declared missing or non-executable script fails. Locked installation fails if
  macOS lock entries are missing. The Linux job remains unchanged.

Host-native lanes on Linux or macOS can use `actions/macos-tools` with
`provision: true` and `runners: ''` to install only repository tools from
`image/mise.toml` and `image/mise.lock`, without downloading the base package.
The lock must contain entries for the runner's OS and architecture.

Both central macOS jobs restore and save mise's content-addressed download store at
`${{ runner.temp }}/macos-tools/cache/downloads-cas`, keyed by runner OS, architecture
and `image/mise.lock`, without a restore prefix. Setup and Lifecycle steps receive
`GITHUB_TOKEN` from `github.token`. The shared `bin/macos-env.sh` must set
`MISE_CACHE_DIR=$RUNNER_TEMP/macos-tools/cache` in CI. Existing repository helpers
that still use a checkout-local cache simply do not consume this restored store.
Installed tools are not cached.

Leg-mode provisioning sets runtime homes before the package download or either
mise install and exports them to setup and Lifecycle steps. All paths are below
`$RUNNER_TEMP/macos-tools`, not the persistent runner user's home:

| Runtime | Data under `data/` | Regenerable files under `cache/` |
| --- | --- | --- |
| Rust | `RUSTUP_HOME=rustup`, `CARGO_HOME=cargo` | Cargo uses its isolated home for registry/build downloads |
| uv | `UV_PYTHON_INSTALL_DIR=uv-python` | `UV_CACHE_DIR=uv` |
| Go | `GOPATH=go` | `GOMODCACHE=go-mod`, `GOCACHE=go-build` |
| npm | Global tools stay in mise's installed Node prefix | `npm_config_cache=npm`, including the initial standards package download |
| pnpm | `PNPM_HOME=pnpm` | `pnpm_config_store_dir=pnpm/store`, `pnpm_config_cache_dir=pnpm` |

pnpm's update-check state uses `pnpm_config_state_dir` below `state/pnpm`.
Setting `PNPM_HOME` alone does not relocate its metadata cache or update-check state.
These override inherited runner-level values. The exporter still accepts PATH
entries only below `MISE_DATA_DIR`; Cargo's `data/cargo/bin` satisfies that boundary.
No external PATH directories are added. Only mise's download store is restored
across jobs; the other stores above remain job-local. `HOME`, XDG locations,
repository dependencies and project build directories are not relocated. Shape
mode (`provision: false`) does not run provisioning or set these variables;
repository macOS helpers continue to own its runtime homes.

The aggregate requires every expected leg to succeed and every unexpected leg to be
skipped. Linux alone, Linux with a declared macOS leg, and macOS shape are distinct
cases. Failed, cancelled or unexpectedly skipped legs fail the aggregate. An existing
reasoned Opt-out still succeeds through the applicable leg's policy step.

Script existence declares applicability, except for an explicit per-concern Opt-out
below. Non-executable scripts fail. No test/e2e script means no JUnit obligation,
not an automatic Opt-out. Tests clear their own
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

## Artifacts

Every execution leg keeps Lifecycle evidence on passing and failing runs. This
includes pull requests, merge groups, default-branch calls and Release Check
qualifications. An Opt-out or a run cancelled before upload has no new evidence.
The aggregate does not upload. Each leg offers up to two artifacts, retained for
**7 days**:

- `reports`: contents of `reports/`, with `test/`, `e2e/`, `coverage/` and other
  diagnostic directories at the artifact root. Only JUnit XML belongs under
  `reports/test/` and `reports/e2e/`: the validator parses every XML file there.
- `package`: contents of `dist/`, only after the `package` Lifecycle Script was
  attempted, including a failed package's partial output. `dist/` is the single
  packaged-output directory. Scripts writing elsewhere must copy their output
  there; the gate does not search for builds.

Missing or empty directories produce no artifact and no warning. Preparation or
upload errors warn and never change the gate result. No files are truncated or
silently capped by size. GitHub's artifact/storage limits still apply; each leg
uses at most two of its 500 artifact slots. Upload happens before JUnit validation
and Compose teardown, so a failed script still leaves its diagnostic evidence.

The workflow's `Upload Lifecycle artifacts` calling step has a **10-minute**
`timeout-minutes` and `continue-on-error: true`. This bounds staging, compression
and both uploads together, not the Lifecycle Scripts or JUnit gate. The November
inventory measured packages up to 94 MB and reports up to 64 MB compressed; an
[80 MB macOS archive took 11 seconds](https://github.com/Rubio-Enterprises/t3code-overlay/actions/runs/42538344990).
Ten minutes allows roughly 27 times that transfer time for the combined measured
package/report sizes, plus local enumeration, copying and compression. It is a
side-step budget, not a larger job timeout or an unconditional artifact-size cap.
A timeout or upload failure emits a warning naming the source directories and
their eligible, uncompressed byte sizes. If enumeration itself fails or times out,
the warning names the directories and explicitly reports size unavailable.

The [workflow step timeout](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idstepstimeout-minutes)
belongs on the caller, not inside composite metadata. Runner 2.337.0's
[composite schema](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Worker/action_yaml.json)
has no inner timeout field. Its
[embedded contexts inherit the caller's remaining time](https://github.com/actions/runner/blob/v2.337.0/src/Runner.Worker/ExecutionContext.cs),
so an inner `always()` cleanup can itself be interrupted. The composite cleans up
with `if: always()`, and a separate workflow `always()` step removes staging again
before JUnit validation, including after the caller times out.

Before copying, staging sizes the same non-hidden, no-follow regular files it
will upload. It checks free space on the staging filesystem and reserves **1 GiB**
for JUnit, teardown and runner logs. If the eligible tree cannot fit above that
reserve, staging warns with each directory's size and the free/reserved bytes,
removes staging and skips the upload. It checks again before each file and removes
all partial staging on any copy failure or interrupt. This is a conservative
logical-byte check, including sparse files, not a fixed maximum artifact size.
The original `reports/` and `dist/` remain untouched. Portable hard-link staging
would reopen source paths rather than copy from the already verified no-follow
descriptors; keeping the existing isolated copies plus a space preflight avoids
that race and filesystem-specific fallbacks.

### Naming and discovery

The name contract is:

```text
test-gate-<leg>-<concerns>-<kind>-<attempt>[-<representative>]
```

- `leg` is `linux` or `macos`, taken from the execution job id.
- `concerns` is the applicable concern set, excluding per-concern Opt-outs, in
  `test-typecheck-e2e-package` order, joined with hyphens, or `none`. It is announced before scripts run, not reduced
  when one fails. macOS shape mode uses the same existing-concern selection as
  Linux; leg mode uses its declared concerns in canonical order.
- `kind` is `reports` or `package`.
- `attempt` is `github.run_attempt`. Re-runs keep earlier evidence; uploads never
  overwrite an artifact from an earlier attempt.
- `representative` is the qualification repository basename, present only for
  Release Check calls. It separates representative checkouts in the caller's run.
  Qualification callers must use distinct repository, leg and concern sets within
  one run; repeated identical qualifications would otherwise collide and warn.

Examples: `test-gate-linux-test-typecheck-e2e-package-reports-1` and
`test-gate-macos-test-reports-1-SaneBar`. Readers such as coverage workflows and
org-ops should list a run's artifacts, filter by this contract and select the
wanted attempt, not assume that every leg wrote a package. Use the run id from the
aggregate check's details URL. Ruleset-injected runs may not resolve through
`gh run list --workflow`; the runs REST API includes them:

```sh
gh api "repos/OWNER/REPO/actions/runs?head_sha=PR_HEAD_SHA&event=pull_request" \
  --jq '.workflow_runs[] | select(.name == "Test Gate") | .id'
gh api repos/OWNER/REPO/actions/runs/RUN_ID/artifacts \
  --jq '.artifacts[] | {name, size_in_bytes, expired}'
gh run download RUN_ID -R OWNER/REPO --name ARTIFACT_NAME --dir evidence
```

A result recorded without re-execution creates no new artifact. Follow its source
run link, or resolve the merged commit through `repos/OWNER/REPO/commits/SHA/pulls`
and query the returned pull request head as above. Evidence expires after seven
days, so coverage consumers must capture their baseline promptly. A privileged
`workflow_run` consumer must treat the files as untrusted data, not execute them;
that trigger's behavior for injected workflows needs separate live verification.

### File and credential boundary

`actions/gate-artifacts` stages only regular files from those two fixed roots into
runner temp. It omits all dotfiles and dot-directories, including `.cache`, and
all symlinks, including linked roots, directory links, internal aliases and broken
links. It neither reads nor follows links outside the workspace. The pinned
[upload action follows symlinks](https://github.com/actions/upload-artifact/blob/cf430e030ddbb5b0abf93d22962f4752f3646cd9/src/shared/search.ts)
and exposes no no-follow input. Staging therefore uses Python's no-follow directory
traversal and file descriptors, not a glob over the original tree. Upload's own
hidden-file exclusion remains enabled as a second boundary. Special files are
omitted. Copy/read errors warn rather than silently dropping unreadable evidence.

Artifacts do not preserve executable bits or symlinks. Package an `.app` or other
permission-sensitive tree into an archive under `dist/` first. Compose-produced
files must be readable by the runner user, normally directories `755` and files
`644`; staging copies become `644`. No Docker is used for upload: the action uses
Node 24 supplied by the Actions runner, including CI Image runners, ARM and macOS.

Never write credentials or sensitive request/response bodies into these roots.
Hidden-file filtering cannot redact secrets embedded in visible logs, traces or
bundles. Avoid tracing authenticated requests with real credentials. The gate
passes only the automatic job token to Lifecycle steps, not qualification reader
App credentials or arbitrary secrets. Private artifacts require repository read
access; public artifacts can be downloaded by signed-in GitHub users. Fork PR
artifacts are untrusted and receive the same treatment.

Uploads authenticate with the Actions runtime token, not an additional
`GITHUB_TOKEN` permission. Default-branch callers keep `contents: read` and
`packages: read`; uploading needs neither `actions: write` nor inherited secrets.
Reading artifacts from another run is separate and requires `actions: read`.

Default-branch uploads are intentional: they retain coverage baselines and the
packaged output of the exact merged tree. At 600 PR/merge-group runs plus roughly
300 default-branch runs per weekday, the earlier 7.7 GiB seven-day storage estimate
scales to about 12 GiB (about $3/month if entirely billable). A large-package bound
scales to about 143 GiB, or $36/month. Actual volume depends on repository mix;
seven-day retention bounds accumulation without discarding passing-run evidence.

## Default-branch execution

`default-branch-check.yml` calls this workflow as `test-gate-execution` without any
qualification inputs or inherited secrets. The image and cache publishers select
default-branch pushes and dispatches; see
[default-branch verification](standards-check.md#default-branch-verification).
The production route, policy reader, applicable Lifecycle concerns and executed
JUnit validation are unchanged, including native shapes and declared macOS legs.
The reader-App token steps are qualification-only. Normal checkout/setup use the
automatic read-only job token, so the publisher callers need no additional secrets.
Dependency review remains public-PR-only, not a whole-tree push check.

This workflow's aggregate remains named `test-gate`. The caller separately reports
`test-gate-main / test-gate`, requiring successful production execution or exact-files
PR evidence for both check families. That visible aggregate name is unchanged when
execution is reused. Behind branches and ambiguous evidence execute this entire
production path, and dispatch always executes it. These extra default-branch results
are not required by a ruleset. Existing reasoned policy exceptions still apply.
There is no automatic retry or red-run issue/label/notification.
The caller's concurrency group remains occupied through completion.

## Exceptions

Missing `.github/standards.toml` means enforcement is enabled. Each whole-component
exception requires a non-empty string reason:

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

### Per-concern Opt-out

A repository can opt out of one concern while keeping every other applicable
concern enforced. This is an exception listed by `org-ops standards status`, not a
replacement for bringing the concern back into CI. Record the reason and tracking
issue in the same policy file:

```toml
[test-gate.e2e]
off = "The wire suite needs a macOS host that can run a virtual machine; no CI runner can today. Tracked in https://github.com/Rubio-Enterprises/noVNC-ARD/issues/124"
```

The supported concern tables are `test-gate.test`, `test-gate.typecheck`,
`test-gate.e2e` and `test-gate.package`. The script stays in `bin/` for manual
execution and the lifecycle Check still requires it. Both Linux and macOS legs
(shape and declared-concern modes) omit the concern, print a notice naming it and
pointing to the recorded reason, and exclude it from the artifact name. No JUnit
evidence is required for an opted-out test/e2e concern; an opted-out package is
not attempted or uploaded. Other concerns and the aggregate result are unchanged.
This also applies to merge groups and default-branch `test-gate-execution` calls.

Unknown concern names, non-table entries, missing/empty/non-string `off` reasons,
and per-concern entries combined with whole-gate `test-gate.off` fail closed. An
entry also fails if its `bin/<concern>.sh` no longer exists: remove stale entries.
`scripts/test-gate-policy.py` is the single policy and concern-selection reader
used by both execution jobs. Each job stages it in runner temp, removes the
central checkout before repository setup, then selects concerns after setup.
On macOS, `actions/setup-python` supplies Python 3.13 for both reads without
changing the job's toolchain PATH. The absolute interpreter path stays stable
when repository tools are provisioned.

Only read-only contents/package permissions are requested. Test Gate does not
publish images, mint OIDC, persist reader credentials or forward all secrets.
Fork/Dependabot credentials retain GitHub's restrictions. Actual ruleset secret
availability and event semantics must be proven by rollout canaries.

## Policy reader delivery compatibility

Production stages `scripts/test-gate-policy.py` from central `main`; a qualification
can select a central candidate ref. The reader checkout resolves when its step
runs, which can be later than the workflow's resolution when the run was created.
Queued runs can therefore pair an older workflow with a newer reader.

The reader's path and interface must never change in the same change as the
workflow. Keep `scripts/test-gate-policy.py`, its `--select linux|macos` interface,
stdout concern list, and `opted-out`, `concerns` and `artifact-concerns` outputs
compatible with workflows already created. Add a compatible interface first,
change callers separately, and retain the older interface for queued runs.

A revert of the workflow must keep the reader file and its compatible interface
on `main`. Do not revert the reader's introduction by deleting the file. Stop
staging it in a separate change before considering removal; the contract test
requires the source file while either execution job still stages it. Temporary
candidate refs need the same retention discipline while qualification runs exist.

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
JUnit, macOS tools and hosted Docker preparation actions for local composite calls.
The shared policy reader also uses `qualification-central-ref`, including on a
normal non-qualification call for pre-merge proof. It defaults to central main;
production checks out the other actions from central main. Each checkout exists only while
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
