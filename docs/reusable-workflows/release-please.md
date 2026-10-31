# release-please reusable workflow contract

- **Status:** Implemented in `.github/workflows/release-please.yml`
- **Caller:** the `release-please` job in a repository's starter `.github/workflows/standards.yml`,
  which runs it on push to `main` when `release-please-config.json` exists

## Purpose

Releases a repository with release-please. It replaces the per-repository `release-please.yml`
that the Copier template rendered, so the behavior changes in one place.

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

The workflow sets `permissions: {}`: every write uses the App token, so the caller's job needs no
`GITHUB_TOKEN` scopes.

| Output | From release-please |
| --- | --- |
| `release_created` | `true` when this run created a release |
| `tag_name` | the created tag |
| `version` | the released version |
| `prs_created` | `true` when this run opened or updated the release PR |

The repository owns `release-please-config.json` and `.release-please-manifest.json` at its root.

## Behavior

1. Mints an App token with `contents: write` and `pull-requests: write` on the calling
   repository. An App token, unlike `GITHUB_TOKEN`, lets the release PR's own workflows run.
2. Runs `googleapis/release-please-action` with the two config files.
3. Finds the open release PR by release-please's `autorelease: pending` label, whether or not
   this run opened it, so a re-run or the next push repairs a run that failed half-way.
4. If the release PR branch has a root `uv.lock`, runs `uv lock` there and pushes a
   `chore: refresh uv lock` commit when it changed: the version bump otherwise leaves the lock
   stale, and the standards check's lockfile Check rejects it.
5. Arms auto-merge on the release PR: squash where the repository allows it, otherwise a merge
   commit (shared-history forks allow merge commits only). Auto-merge waits for the required
   checks, `standards` and `test-gate`.

One release package per repository: the label lookup takes the first open release PR.
