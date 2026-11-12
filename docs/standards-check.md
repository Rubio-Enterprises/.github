# The Standards Check (`standards.yml`)

The OpenTofu-owned `standards` ruleset injects `.github/workflows/standards.yml`
at `refs/heads/main` into repositories with `standards-onboarded=true`.
The required check remains `standards`; `test-gate` remains repository-owned.

## Execution

The [project router](reusable-workflows/project-route.md) reads `ci-platform`,
`ci-size`, `RUNNERS` and the central `CI_IMAGE_REPOS` admission list before checkout.
The router's `linux` selection uses `ci-size` for the Linux leg even when
`ci-platform=macos`, and returns the declared platform separately. A route failure
fails the required aggregate without running project code.

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
