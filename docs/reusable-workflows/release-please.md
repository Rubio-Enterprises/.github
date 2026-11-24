# release-please reusable workflow contract

- **Status:** Implemented in `.github/workflows/release-please.yml`
- **Caller:** the `release-please` job in a repository's starter `.github/workflows/standards.yml`,
  which runs it on push to `main` when `release-please-config.json` exists

## Purpose

Releases a repository with release-please. It replaces the per-repository `release-please.yml`
that the retired Copier template rendered, so the behavior changes in one place.

## Interface

`on: workflow_call`. No inputs. The caller passes `secrets: inherit` so the reusable can read the
org secrets `RELEASE_PLEASE_APP_ID` and `RELEASE_PLEASE_APP_PRIVATE_KEY` (the
`rubio-release-please` App).

```yaml
release-please:
  needs: files
  if: github.event_name == 'push' && needs.files.outputs.release-please == 'true'
  uses: Rubio-Enterprises/.github/.github/workflows/release-please.yml@main
  secrets: inherit
```

The workflow sets `contents: read` and `packages: read` (for the internal base image used by the
lock refresh). Every write uses a per-job App token scoped to the calling repository.

| Output | From release-please |
| --- | --- |
| `release_created` | `true` when this run created a release |
| `tag_name` | the created tag |
| `version` | the released version |
| `prs_created` | `true` when this run opened or updated the release PR |

The repository owns `release-please-config.json` and `.release-please-manifest.json` at its root.

## Behavior

1. **release** (`small`, fallback `ubuntu-slim`) mints an App token with `contents: write` and
   `pull-requests: write`, then runs `googleapis/release-please-action` with the two config files.
   Finds the PR from the action's `pr` JSON (`number`, `headBranchName`) first; otherwise queries
   `autorelease: pending` up to three times, two seconds apart. The fallback finds an existing
   open release PR even when this run did not update it, so re-runs repair failed runs.
   Probes the contents API at that head ref for root `uv.lock`, without checkout; 404 means no
   lock or a just-deleted release branch (skipping **lock** and letting **arm** act on the saved
   PR number), while other API failures fail the job.
2. **lock** (`docker`, fallback `ubuntu-latest`) runs only for a release PR with root `uv.lock`.
   Mints a `contents: write` App token and checks out the branch. On hosted Linux, temporarily
   sparse-checks out central `actions/prepare-hosted-docker` at `main`, prepares disk, then removes
   that checkout before setup-just and registry login. Runs `just build-dev` and `just exec uv lock`,
   pushing a `chore: refresh uv lock` commit only when changed. The version bump otherwise leaves
   the lock stale for the standards check.
3. **arm** (`small`, fallback `ubuntu-slim`) waits for **lock** to succeed or be skipped, and
   requires a successful **release** job with a PR. Mints an App token with `contents: write` and
   `pull-requests: write`, then arms auto-merge: squash where allowed, otherwise a merge commit
   (shared-history forks allow merge commits only). Auto-merge waits for the required checks,
   `standards` and `test-gate`. Failed or cancelled lock refreshes never arm auto-merge.

App tokens, unlike `GITHUB_TOKEN`, let the release PR's own workflows run. One release package
per repository: both discovery paths take the first release PR.
