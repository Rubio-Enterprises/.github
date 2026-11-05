# The Standards Check (`standards.yml`)

The OpenTofu-owned `standards` ruleset injects `.github/workflows/standards.yml`
at `refs/heads/main` into repositories with `standards-onboarded=true`.
The required check remains `standards`; `test-gate` remains repository-owned.

## Execution

The [project router](reusable-workflows/project-route.md) reads `ci-platform`,
`ci-size`, `RUNNERS` and the central `CI_IMAGE_REPOS` admission list before checkout.
A route failure fails the required job before any project code runs.

- **CI Image:** calls `standards check`, then `standards check-pr`. There is no
  standards installation, toolchain bootstrap or Docker daemon inside the job.
- **Other runners:** installs the checksum-pinned `just` release, logs into GHCR
  with the job token, then runs `just build`, `just check`, `just check-pr`.
  Native macOS repositories provide native recipes for the parts requiring Xcode.

Checkout uses full history and no persisted credential. PR Checks still run when
whole-tree Checks fail, so one failure does not hide another. Fixer rewrites are
summarized on failure. `.github` itself is a bootstrap repository and is skipped.

## Caches and environment

`standards-cache.yml` writes the weekly `standards-trivy-db-<OS>-<ISO week>` and
`standards-npm-<OS>-<ISO week>` entries on the repository's default branch. The
injected job restores them but never saves PR-scoped entries. Both cache paths
are under the gitignored workspace `.cache/`, visible at the same host path inside
Compose. A restored trivy database younger than eight days disables DB updates.
This accepts advisory lag until the next weekly cache refresh.

Compose passes `CI`, `BASE_SHA`, `HEAD_SHA`, `PR_TITLE`, `GH_TOKEN`, `GITHUB_TOKEN`,
`XDG_CACHE_HOME`, `TRIVY_CACHE_DIR`, `TRIVY_SKIP_DB_UPDATE` and GitHub event/run
metadata. Hosted PR Checks copy the event JSON under `.cache/ci/` and write a
container-visible summary there, then append it to the real Actions summary.
The host's temporary event/summary paths are not assumed to be mounted.

The job grants only `contents: read`, `packages: read`, `pull-requests: read` and
`issues: read`. It cannot comment or publish. Editing a PR title/body does not
rerun an injected ruleset workflow: push a commit or rerun its failed job.
