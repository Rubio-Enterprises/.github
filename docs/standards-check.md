# The Standards Check (`standards.yml`)

- **Status:** Implemented in `.github/workflows/standards.yml`. It replaced the Gate Family workflows
  (`standards-gates.yml`, `lint-hooks.yml`, `typecheck-ts.yml`, `build.yml`), deleted on the
  standards v2 flag day.
- **Delivery:** injected as a Required Workflow by the `standards` org ruleset in
  `.github-private` Terraform, at `.github/workflows/standards.yml@refs/heads/main`.

## Purpose

One required check, `standards`, for every onboarded repository. The workflow is a thin shell: it
installs the repository's Standards Release (`@rubio-enterprises/standards` from GitHub Packages)
and runs the Conformance Tasks the package ships. The Checks themselves, their tool pins and the
task definitions live in `Rubio-Enterprises/standards`; change them there, not here.

The second required check, `test-gate`, is the repository's own starter workflow and is not
defined in this repository.

## How a repository opts in

| Custom property (Terraform) | Values | Effect |
| --- | --- | --- |
| `standards-v2` (renamed `standards-onboarded` after the migration) | `true` | The ruleset injects this workflow and requires it on the default branch |
| `ci-platform` | `linux` (default), `macos` | `macos` runs on `RUNNERS.macos` (hosted fallback `macos-26`), for Swift and Homebrew repositories |
| `ci-size` | `small` (default), `medium` | `medium` runs on `RUNNERS.medium` (hosted fallback `ubuntu-latest`); otherwise `RUNNERS.small` (`ubuntu-slim`) |

The repository itself must be onboarded: `npm init @rubio-enterprises/standards` writes
`lefthook.yml`, the starter `mise.toml` with its `setup` task, and the
`.config/mise/conf.d/standards.toml` link the package's tasks load through.

## What the job does

1. Checks out the pull request with full history and no persisted credentials.
2. Fails with `repository not onboarded` when `lefthook.yml` is missing.
3. Installs uv, then mise (`jdx/mise-action`, pinned `MISE_VERSION`, no tool install yet).
4. Restores two caches: mise's data directory and the trivy vulnerability database.
5. Runs `mise run setup` with `NODE_AUTH_TOKEN` set to the job token. The repository's `setup`
   installs the package into `.standards/`, the missing tools, the git hooks and every
   `setup:*` task (the repository's own dependencies).
6. Fails unless `check` and `check:pr` resolve to the package's `mise/standards.toml`. A task of
   the same name in `mise.toml` or `.mise.local.toml` wins over the package's in mise, so a
   repository could otherwise replace the Checks silently.
7. Runs `mise run check`: every Check on every tracked file, failing on any Fixer rewrite. On a
   fork with an `upstream` branch, the task itself narrows to the pull request's files.
8. Runs `mise run check:pr` even when `check` failed: gitleaks over the pull request's commits,
   the title, and the PR conventions (Danger; pull requests only, not merge groups).
9. When `check` fails, writes `git status` and `git diff --stat` to the job summary: Lefthook
   2.1 prints no per-job summary in CI when a Fixer's rewrite fails the run.

The tasks receive `BASE_SHA` and `HEAD_SHA` (the pull request's or merge group's), `PR_TITLE`,
`GITHUB_TOKEN`/`GH_TOKEN` (the job token) and `GITHUB_EVENT_NAME`. Those names are the interface
between this workflow and the package.

## Permissions

`contents: read`, `packages: read` (the internal package) and `pull-requests: read` (Danger reads
the pull request). The job token cannot comment, so the PR-conventions step reports in the log.

## Things that are easy to get wrong

- **Triggers.** Ruleset workflows ignore `types` and other event filters and run only on
  `opened`, `synchronize` and `reopened` (plus `merge_group`). Editing a title or description
  does not re-run the check; push a commit or re-run the job.
- **Runner routing reads `github.event.repository.custom_properties`.** Whether the
  `pull_request` payload carries it is unverified (flag-day DV-1). If the route always falls back
  to `small`, replace the expression with a `route` job, as the comment in the workflow
  describes.
- **Caches are scoped to the pull request.** The workflow never runs on the default branch, and a
  cache saved by a `pull_request` run is visible only to later runs of the same pull request, so
  the first run of every pull request is cold (about 16 s of tool installs plus a 6.6 s trivy
  download, measured locally). Both keys rotate weekly. The mise key hashes only the repository's
  own mise files, because the conf.d link into `.standards/` dangles until `setup`.
- **This repository is skipped.** `.github` is not onboarded; the job's `if` excludes it so its
  own pull requests are not failed by the missing `lefthook.yml`.
- **Full history** costs little: cloning karakeep (94 MB, 3,322 commits) took 4.7 s against
  2.2 s at depth 20. `check:pr` needs the commit range and forks need `origin/upstream`.
