# CodeWiki Pages reusable workflow contract

- **Status:** Implemented in `.github/workflows/codewiki-pages.yml`
- **Caller:** the `codewiki-pages` job in a consumer's rendered
  `standards.yml`, present when the repository declares `has_codewiki`
  (standards template)

## Purpose

The reusable workflow publishes a repository's committed CodeWiki to GitHub
Pages. CodeWiki output lives at `docs/codewiki/` in every repository that
declares `has_codewiki`. It is generated locally with `mise run codewiki`
(`codewiki generate --output docs/codewiki --github-pages`) and lands on the
default branch through normal pull requests. The workflow deploys that
committed output; it never runs CodeWiki.

## Interface

`on: workflow_call` only.

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | string | `docs/codewiki` | Repo-relative CodeWiki directory |
| `timeout-minutes` | number | `15` | Timeout for each of the two jobs |

There are no secrets and no outputs. The org convention is one path,
`docs/codewiki/`; the `path` input exists for callers outside that convention
and for testing, not as a per-repository setting.

## Behavior

1. **`build` job** (`contents: read`):
   1. Checks out the caller revision, sparse to `path`, without persisting
      credentials.
   2. Fails if `path` is absolute or contains a `..` segment, or if
      `path/index.html` is missing. `index.html` is the viewer CodeWiki writes
      under `--github-pages`; without it the site has no entry point, so the
      job fails with a message naming the fix.
   3. If `path` does not exist, emits a notice and stops with nothing to
      publish: the build job sets output `publish=false`, skips the upload,
      and the `deploy` job is skipped. A repository may declare `has_codewiki`
      before its first build is committed (the central controller generates
      it on dispatch), so this is a pending CodeWiki, not an error.
   4. Copies `path` into a staging directory under `RUNNER_TEMP` and removes
      the top-level `temp/` directory. `temp/` is CodeWiki's state directory
      (dependency graphs and the artifact index, megabytes). It stays committed
      for later `--update` runs but is never published.
   5. Fails if the staged site contains a symlink.
      `actions/upload-pages-artifact` archives with `tar --dereference`, so a
      committed symlink would publish its target, which may sit outside the
      CodeWiki directory.
   6. Uploads the staging directory with `actions/upload-pages-artifact`.
      Hidden files are excluded by that action's default.
2. **`deploy` job** (`pages: write`, `id-token: write`; needs `build`; runs
   only when `build` set `publish=true`):
   - Runs in the `github-pages` environment, with the environment URL set to
     the deployed `page_url`.
   - Deploys with `actions/deploy-pages`.
   - Uses job concurrency group `pages` with `cancel-in-progress: false`,
     GitHub's Pages starter pattern: one deployment per repository at a time,
     and an in-flight deployment is never cancelled. The group is a literal on
     purpose. In a called workflow `github.workflow` resolves to the caller's
     name, and reusing the caller's group would deadlock (see
     `secret-scan.yml`).

Both jobs use the `light` runner route with the `ubuntu-slim` hosted fallback.
The artifact step needs GNU `tar` (for `--hard-dereference`) on a Linux
runner, which both the self-hosted `glue-x64` pool and `ubuntu-slim` provide.
Neither action needs Docker or `zstd`.

## Caller contract

A reusable workflow can narrow the `GITHUB_TOKEN` but never widen it. The
calling job grants:

```yaml
permissions:
  contents: read
  pages: write
  id-token: write
```

The standards template renders the caller in `standards.yml` when
`has_codewiki` is true: the default-branch `push` trigger, plus a
`codewiki-pages` job guarded to push events and pinned to
`reusable_workflow_sha`. Deployment runs on every push to the default branch.
A paths filter is deliberately absent, because the shared `push` trigger would
also gate the workflow's other jobs.

## Ownership boundary

This workflow owns staging and deployment only. It does not:

- generate or update CodeWiki output;
- enable GitHub Pages, set its build type, or choose the site's visibility.

Pages enablement belongs to the GitHub Settings Plane. `.github-private`
Terraform enables Pages with `build_type = "workflow"` for repositories whose
generated `repo_governance` entry sets `codewiki_pages`, and the site's
visibility mirrors the repository's (private repository, private site).
`standards`' `gen-gates-tfvars.sh` derives `codewiki_pages` from the
`has_codewiki` answer. A repository that calls this workflow without Pages
enabled fails at the deploy step.

## Versioning and consumer updates

This is a thin-called reusable, not a Gate Family workflow. It is not in
`.github/plumbing-ref/gate-family-workflows.json` and does not ride
`gates/wf-v1`. It ships through this repository's release-please release and
the floating `v4` major tag. Consumers call it at the SHA in their rendered
`standards.yml` (`@<sha> # v4`), which Renovate advances when `v4` moves. A
consumer can call the workflow only once its pinned SHA includes it, so the
standards template's `reusable_workflow_sha` placeholder must be at or after
the first release that contains this file.
