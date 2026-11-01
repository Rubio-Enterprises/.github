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

The [project router](reusable-workflows/project-route.md) preserves these routes and their hosted
fallbacks. For an admitted private/internal repository on a self-hosted small/medium route, it
selects that repository's CI Image label instead. Admission remains the central Terraform-owned
`CI_IMAGE_REPOS` ID array; this workflow does not change it.

The repository itself must be onboarded: `npm init @rubio-enterprises/standards` writes
`lefthook.yml`, the starter `mise.toml` with its `setup` task, and the
`.config/mise/conf.d/standards.toml` link the package's tasks load through.

## What the job does

1. Checks out the pull request with full history and no persisted credentials.
2. Fails with `repository not onboarded` when `lefthook.yml` is missing.
3. On plain runners, installs uv, then mise (`jdx/mise-action`, pinned `MISE_VERSION`, no tool
   install yet). CI Image runners use the image's tools and explicitly trust the checkout instead.
4. Restores the trivy vulnerability database the default branch seeded, if any.
5. Runs the [`standards-setup`](../actions/standards-setup/action.yml) action: on plain runners it
   restores the [Standards Setup Caches](standards-setup.md) (mise's tool installs and npm's
   package cache) that the default branch seeded, then runs `mise run setup` with
   `NODE_AUTH_TOKEN` set to the job token. The repository's `setup` installs the package into
   `.standards/`, the missing tools, the git hooks and every `setup:*` task (the repository's own
   dependencies). The check never saves a cache.
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
- **Routing is a separate repository-agnostic job.** It consumes declared `ci-platform`/`ci-size`,
  reading the base repository properties API if either is absent from the event. The required
  `standards` job also runs after router failure and fails before checkout, rather than allowing
  a skipped dependency to make the required context green. The failure-only job uses the ordinary
  small route and executes no project commands.
- **Caches come from the default branch.** The workflow never runs there, and a cache saved by
  a `pull_request` run serves only later runs of the same pull request. So the check only
  restores, and the repository's `standards.yml` seeds the caches on push to the default branch
  and weekly through `standards-seed.yml` ([Standards Setup Caches](standards-setup.md)). Until a
  repository's caches are seeded, its first run is cold.
- **This repository is skipped.** `.github` is not onboarded; the job's `if` excludes it so its
  own pull requests are not failed by the missing `lefthook.yml`.
- **Full history** costs little: cloning karakeep (94 MB, 3,322 commits) took 4.7 s against
  2.2 s at depth 20. `check:pr` needs the commit range and forks need `origin/upstream`.
