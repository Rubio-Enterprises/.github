# The Standards Check (`standards.yml`)

The OpenTofu-owned `standards` ruleset injects `.github/workflows/standards.yml`
at `refs/heads/main` into repositories with `standards-onboarded=true`.
The required check remains `standards`; the separately injected Test Gate keeps
its required `test-gate` result.

## Execution

The [project router](reusable-workflows/project-route.md) reads `ci-platform`,
`ci-size`, `RUNNERS` and the central `CI_IMAGE_REPOS` admission list before checkout.
This workflow sets `inspect-image-inputs: true`; other router callers default to
no image-input inspection. The router's `linux` selection uses `ci-size` for the
Linux leg even when `ci-platform=macos`, and returns the declared platform separately. A route failure
fails the required aggregate without running project code.

For an admitted Linux-shape pull request or merge group, the router compares the
event's base and head. Image-input changes, or an unavailable/incomplete file list,
select the `docker` route and `ci-image=false`. `standards-linux` then uses the same
hosted/non-image steps below, including hosted disk preparation and the
container-visible trivy context, to check the pull request's own Dev Image.
Non-image changes keep the CI Image. Public repositories already build through
Compose; macOS shapes retain both existing legs. The path list, API limits and
post-merge canary are in [project routing](reusable-workflows/project-route.md#pull-request-image-inputs).

- **CI Image:** calls `standards check`, then `standards check-pr`. There is no
  standards installation, toolchain bootstrap or Docker daemon inside the job.
- **Hosted/non-image Linux:** installs the checksum-pinned `just` release, logs
  into GHCR with the job token, then runs `just build`, `just check`, `just check-pr`.
  For macOS repositories only, the whole-tree command is `just exec standards check`
  so the native repository's combined `just check` does not invoke SwiftLint on Linux.
- **Native macOS:** only for `ci-platform=macos`, a parallel `standards-macos` job
  uses `RUNNERS.macos` (fallback `macos-26`), checks out with no persisted credential,
  installs `just`, then runs `setup-macos` and `check-macos` when listed by `just --summary`,
  otherwise the corresponding `-native` recipes. No Docker
  login/build, image Checks, PR Checks or native tests run in this leg. The
  read-only package token lets the setup recipe fetch shared config without Docker;
  the repository owns its native toolchain setup. Native tests remain in `test-gate`.

The execution job is named `standards-linux` for every repository. The sole job
named `standards` is a Docker-free aggregate with `if: always()`: routing and
`standards-linux` must succeed; `standards-macos` must succeed for macOS and must
be skipped for Linux. Failed, cancelled or unexpectedly skipped required legs fail
the aggregate. This preserves the required result while adding a native leg only
where declared. OpenTofu requires this workflow by path/ref, not a new status
context per leg; no ruleset change is needed.

Checkout uses full history and no persisted credential. PR Checks still run when
whole-tree Checks fail, so one failure does not hide another. Fixer rewrites are
summarized on failure. `.github` itself is a bootstrap repository and is skipped.

## Default-branch verification

The existing repository-owned publisher calls the central image and cache reusables.
Their `verify` jobs select pushes and `workflow_dispatch` on the repository's actual
default branch, including `master`. They exclude this non-onboarded source repository.
Private repositories verify after successful image publication, unless `image-name`
is set. Public repositories verify through `standards-cache.yml`, independently of
cache warming. No required-check setting changes. Before deploying the shortcut,
every publisher caller must grant `actions: read` in addition to its existing grants.

Both call `default-branch-check.yml`, a read-only, workflow_call-only reusable with
no event/ref guard or qualification inputs. Its optional `central-ref` selects the
central evidence script for pre-merge canaries (default `main`). When no conclusive
PR evidence exists, it executes whole-tree Checks:
`standards check` in a CI Image, the existing hosted Linux recipes otherwise, plus
the native Standards leg for `ci-platform=macos`. Admission and the shared router,
not visibility alone, select image execution. Hosted builds use the same disk
preparation and container-visible trivy context as PR checks. No `check-pr` or
pull-request metadata is used. The PR execution path is unchanged; contract tests
compare the duplicated whole-tree steps to it to prevent drift.

The fail-closed aggregate is `standards-main`. `test-gate-execution` calls the
production [Test Gate](test-gate.md) with no inputs or inherited secrets. A separate
aggregate named `test-gate-main / test-gate` requires that execution to succeed or
records the same PR evidence as `standards-main`. Thus both visible aggregate
names remain `<caller> / verify / standards-main` and
`<caller> / verify / test-gate-main / test-gate`, including when execution is reused.
The caller is `repository-image` or `standards-cache`; execution legs now appear
under `test-gate-execution`. These additional results are not required checks.

### Exact-files PR evidence

Reuse is all-or-nothing. `scripts/checked-tree.py` returns both passing run links,
or `{}`. For a single-parent push P with parent B, it requires matching trees for
P and PR head H, and a compare result proving B is an ancestor of H. This shortcut
only accepts an evidence head containing the push's entire base. The organization
does not require up-to-date PR branches (`strict_required_status_checks_policy=false`),
so a behind merge normally executes checks. Another PR's successful up-to-date head
can still qualify if it contains that base and has exactly the pushed tree.
Two-parent merge commits and unmatched direct pushes execute the checks.

Both latest completed PR runs for H must succeed. Identity is the injected
workflow path plus its repository-specific `required_workflows` URL/ID namespace,
not its display name or a repository-owned workflow at the same path. Fork heads
are excluded. A newer failed run disarms reuse for that head. Repository-wide run
listing is necessary because ordinary per-workflow endpoints do not resolve the
injected IDs. The newest 100 completed PR runs are only a candidate window, so
large repository histories still qualify. Once a matching head is nominated, a
separate listing filtered to that head must be complete. Both latest completed
injected runs in that listing must pass, including re-runs of older runs outside
the candidate window. An incomplete or malformed per-head page executes checks.
The lookup makes at most four calls: pushed Git commit, candidate window,
complete per-head runs, and ancestry comparison. Neither listing is paginated.

A dispatch always executes the checks without an API lookup. Every failed,
incomplete or ambiguous lookup, including a 403, also executes checks. Both
aggregates summarize and link both passing runs when reused. There are no
artifacts, log scraping, caching or retries in this module. Same-workflow duplicate
suppressors cannot supply this cross-workflow tree-and-ancestry proof.

A failure is a failed run only: no retry, notification, issue, label or merge block. Publication failure leaves verification
unrun, not passing. The publisher's existing concurrency group remains held until
verification finishes. GitHub may replace pending runs during a burst; the contract
is verification of the latest head, not every intermediate commit.

Verification explicitly requests `actions: read`, `contents: read` and
`packages: read`; only the evidence job gets Actions access, and it has no package
access. Execution jobs keep their existing read-only grants. The automatic job
token needs no reader-App secret, OIDC or write permission. Both publisher callers
must grant Actions read before this central change is merged. Merge also waits
for a released starter carrying the grant and API verification of every onboarded
default-branch caller. An ungranted caller
is rejected by GitHub at workflow load time, before any fail-open script can run.
A loaded script receiving a 403 instead returns no evidence and executes checks. An explicit direct call can exercise these same checks
on a non-default branch without changing production trigger policy in the callers.

Default-branch macOS Standards jobs restore and save the same mise download-store
path and key as PR jobs. A default-branch save is visible to PRs, unlike PR-scoped
saves. Repository setup must use the shared CI cache location described in
[Test Gate's cache contract](test-gate.md); legacy checkout-local helpers cannot
populate that store just because the workflow contains a save step.

### Pre-merge canary evidence

The read-only reusable passed direct branch calls in
[private org-ops](https://github.com/Rubio-Enterprises/org-ops/actions/runs/43192815621),
[public rubio-cli-kit](https://github.com/Rubio-Enterprises/rubio-cli-kit/actions/runs/43192823362)
and [native SaneBar](https://github.com/Rubio-Enterprises/SaneBar/actions/runs/43192812689).
Observed aggregate check-run names were `execution / standards-main` and
`execution / test-gate-main / test-gate`. Linux execution jobs were
`execution / standards-linux` and `execution / test-gate-main / test-gate-linux`;
SaneBar also ran `execution / standards-macos` and used
`execution / test-gate-main / test-gate-macos` instead of the Linux Test Gate leg.
`execution` is the temporary direct caller's job ID, not the publisher's prefix.
The exact post-merge publisher-prefixed names must be read from its first dispatch.

The private/public runs also loaded the candidate publishers with the starter's
exact permissions and no inherited secrets. Their `verify` jobs and private build
skipped off-default, while direct execution used the current org-ops CI Image or
hosted Linux respectively. Only the two new nested reusable references were
redirected to the temporary candidate ref for load testing; guards and permissions
were unchanged. These runs prove load compatibility and execution, not newly
published image freshness or default-branch cache visibility.

SaneBar's native setup and Checks passed, but the cache save warned that the shared
download path did not exist. Its legacy setup uses a checkout-local cache. The
workflow's restore/save layout matches PRs; successful content population and
cross-PR visibility remain unproven until the shared macOS environment is deployed.

## Caches and environment

`standards-cache.yml` writes the weekly `standards-trivy-db-<OS>-<ISO week>` entry
on the repository's default branch. The injected job restores it but never saves
PR-scoped entries. The cache path is under the gitignored workspace `.cache/`,
visible at the same host path inside Compose. The PR conventions' npm packages
are baked into the image, so there is no npm cache to warm, and nothing writes
to `/opt/standards`, which the workspace-owner remap leaves read-only. A restored trivy database younger than eight days disables DB updates.
This accepts advisory lag until the next weekly cache refresh.

Compose passes `CI`, `BASE_SHA`, `HEAD_SHA`, `PR_TITLE`, `GH_TOKEN`, `GITHUB_TOKEN`,
`XDG_CACHE_HOME`, `TRIVY_CACHE_DIR`, `TRIVY_SKIP_DB_UPDATE` and GitHub event/run
metadata. Hosted PR Checks copy the event JSON under `.cache/ci/` and write a
container-visible summary there, then append it to the real Actions summary.
The host's temporary event/summary paths are not assumed to be mounted.

The Linux job grants only `contents: read`, `packages: read`, `pull-requests: read`
and `issues: read`. The native job grants only `contents: read` and `packages: read`;
the aggregate has no token permissions. None can comment or publish. Editing a PR title/body does not
rerun an injected ruleset workflow: push a commit or rerun its failed job.
