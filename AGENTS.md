# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other agents when
working with code in this repository. `CLAUDE.md` is a symlink to this file — **edit
`AGENTS.md`**.

## What this repo is

`Rubio-Enterprises/.github` (local dir `Governance/dot-github`, remote `origin =
git@github-personal:Rubio-Enterprises/.github.git`). Public org repo holding the
**reusable GitHub Actions workflows** that every consumer's
`.github/workflows/standards.yml` thin-calls. In the three-repo governance system it is
the *delivery vehicle*: `standards` defines the rules, **this repo runs them in CI**, and
`dot-github-private` enforces repo settings. The macro architecture (the three repos, the
`standards` content layers and release streams, and the four propagation paths) is documented
canonically in the sibling `standards` repo's `AGENTS.md` → "How changes propagate to the
fleet"; this file covers only what is specific to working *inside* `dot-github`.

This is a **bootstrap repo**: not rendered from the Copier template, not standards-onboarded,
and excluded from its own audit. Consequence: there is **no `.mise.toml`, no `lefthook.yml`,
no `.editorconfig`, and no local task runner** here. The `mise install` / `mise run lint|test`
steps in `CONTRIBUTING.md` describe the *consumer* dev loop and do **not** apply in this repo.
Work here is editing workflow YAML + JSON config directly; `actionlint` is the practical
local check for the workflow files before pushing. Otherwise validation happens in GitHub CI.

## The reusable workflows (`.github/workflows/`)

Consumer-facing reusables, invoked via `uses:` / `workflow_call` from a consumer's `standards.yml`:

| Workflow | What it does | Standards pin |
|---|---|---|
| `audit.yml` | Standards Layer A (`check.sh`) + B (`check-jsonschema`) + C (Conftest `--combine`), plus npm-lockfile integrity and the managed-content strict gate (`check-managed-content.sh`) | floating **`audit/v1`** |
| `secret-scan.yml` | `mode: gitleaks` (PR-diff scan) or `mode: trufflehog` (scheduled full-history, `--results=verified`) | floating **`audit/v1`** |
| `e2e.yml` | Playwright harness; detects `scripts.e2e`, then runs `mise run e2e` / `npm run e2e`. Does **not** start a dev server (see the dev-server contract in its header) | — |
| `lint-hooks.yml` | `lefthook run pre-commit --all-files` + a commit-msg smoke test against the consumer | — |
| `rust-test.yml` | Swatinem cache + `mise run test` (nextest JUnit) | — |
| `bump-brew.yml` | Bumps a `:git`-strategy Homebrew formula in `homebrew-tap` to the **release tag that triggered the caller** — rewrites the top-level source `tag:` + `revision:` and inserts/updates `version` (no tarball/sha256, since `:git` formulae build from source). Replaces `mislav/bump-homebrew-formula-action`, which can't handle source-build formulae or private-repo archives | — |

`bump-brew.yml` is the odd one out: it's invoked from a consumer's **release/tag workflow**, not from `standards.yml` (the My-Tools Go/Swift CLIs that ship a `:git` formula in the tap call it on release). It needs a `tap-token` secret (PAT with `contents:write` on the tap). **Filename ≠ display name** — the file is `bump-brew.yml` (renamed from `bump-homebrew-git`) but its internal `name:` still reads `bump-homebrew-git (reusable)`; `uses:` the *path* `…/bump-brew.yml@v1`.

**Both audit reusables track the floating `audit/v1`.** (Historically `secret-scan.yml` froze
an *exact* `audit/v1.X.Y` while `audit.yml` floated — that pin asymmetry was removed in `v1.4.3`.)
Moving `standards`' `audit/v1` therefore reaches *both* on each consumer's next run. Still read
the actual `ref:` before reasoning about which `standards` content a given reusable executes.

`copier-sync.yml` and `copier-check.yml` are also consumer-called reusables, but they
orchestrate consumer-side `copier update` rather than running `standards` audit content
(see their own section below). `release-please.yml` and `floating-tag-floor-check.yml` are
this repo's own ops, not reusables.

There is intentionally **no `workflow-templates/`** (org "New workflow" picker starters).
The two starters that once lived there (`standards-audit-starter`, `e2e-starter`) were
removed: zero repos ever adopted them, and the audit one silently broke when `standards`
went private (a picker-copied caller passes no `secrets: inherit`, so the standards-reader
token mint fails). Repos join the fleet via Copier (`/onboard-repo`), which renders
`standards.yml` directly. Don't reintroduce starters without wiring the secrets contract.

## The load-bearing release ritual

This is the single most important thing to get right in this repo.

1. The audit reusables check out `standards` at the **floating `audit/v1`**. So audit-rule
   **content** reaches the fleet when `standards` moves `audit/v1` — on each consumer's next
   run, with **no `.github` release** (the safety gate for that propagation is `standards`'
   pre-flight `audit-canary`, not a release here). A `.github` release is needed only for the
   **plumbing**: a change to a reusable's own logic/steps, or **repointing** a reusable to a
   different ref (e.g. `audit/v1` → `audit/v2`) — because consumers pin the reusable *file* by SHA.
2. To ship such a reusable-code change: consumers pin these reusables by SHA with a trailing
   `# v1` comment, and Renovate bumps that SHA only when the floating `v1` tag moves (which only
   release-please does — next point). So `standards`' floating tags gate the *content*; the
   `.github` release gates the *workflow code* that runs it.
3. Releases are automated by **release-please** (`release-please.yml`, `release-type: simple`,
   manifest `.release-please-manifest.json`). It opens/updates a release PR on every push to
   `main`; the **PR title** is the Conventional-Commit that decides the version bump. On merge
   it tags `vX.Y.Z`, creates the release, then **moves the floating major tag `v1` onto the
   release commit** and verifies the push landed.
4. `floating-tag-floor-check.yml` (daily cron) is the backstop invariant: **`v1` MUST equal
   the latest strict-semver release commit.** This is the *inverse* of the sister `standards`
   repo's floor-check (which requires its floating tag strictly *ahead*, because `standards`
   advances its tag manually post-release; here release-please moves `v1` *onto* the release).

**Never create or move `v1` by hand** — release-please owns it; a hand-moved `v1` on an
unreleased SHA is exactly the drift the floor-check exists to catch. To ship a reusable-workflow
change: merge it via a conventionally-titled PR and let the release automation retag.

## Cross-cutting workflow patterns (easy to break when adding/editing a reusable)

- **`pull_request:` no-op guard.** Most reusables declare a `pull_request:` trigger they
  don't actually use, then guard every job with
  `if: github.event_name != 'pull_request' || github.repository != 'Rubio-Enterprises/.github'`.
  The trigger exists *only* to satisfy GitHub's org-ruleset `required_workflows` validator
  (422 without it); the guard makes it a no-op on PRs against `.github` itself (this repo
  can't satisfy the consumer-shaped contract `check.sh` enforces). Keep both when you add a
  reusable consumers must run.
- **`setup-uv` before `mise-action`.** Every mise-using job installs
  `astral-sh/setup-uv@…` *before* `jdx/mise-action`. Without uv on PATH, mise ≥ 2026.6.2
  routes pipx installs through pip's `--uploaded-prior-to`, which cold runners' bootstrap pip
  (< 26) rejects → hard fail. Copy this step into any new mise-based reusable.
- **mise CLI pin.** The `version: "2026.6.9"` on each `jdx/mise-action` carries a
  `# renovate: datasource=github-releases depName=jdx/mise` marker so Renovate bumps them
  together (human-merge only — see below). Keep the marker when you add a job.
- **`RUNNER_GLUE` runner selection.** Glue-tier jobs use
  `runs-on: ${{ fromJSON(vars.RUNNER_GLUE || '["ubuntu-slim"]') }}` (org var, defaults to
  `ubuntu-slim`) — now including `lint-hooks`, `copier-check`, and `rust-test`. Only `e2e`
  still uses `ubuntu-latest`.
- **App-token for `standards.yml` pushes.** `copier-sync.yml` mints a dedicated GitHub App
  token (`COPIER_SYNC_APP_ID` / `COPIER_SYNC_APP_PRIVATE_KEY`, passed via `secrets: inherit`)
  because the default `GITHUB_TOKEN` can't push changes under `.github/workflows/` (the
  un-grantable `workflows` permission), and a copier update that rewrites `standards.yml`
  needs exactly that.

## copier-sync / copier-check (consumer template-drift reusables)

These run *consumer-side* `copier update` against `standards`' **template** stream (default
`template/v1`), independent of the audit pin:

- `copier-sync.yml` — opens a `chore: copier update …` PR when a consumer's rendered template
  state drifts. Reads the consumer's current template ref from **`_rubio_template_version`** in
  `.copier-answers.yml` (standards `template/v1.24.0`+; Copier's built-in `_commit` is no longer
  consulted). Creates the PR *first, without labels*, then attaches them best-effort — labels
  are advisory, the PR is the load-bearing artifact.
- `copier-check.yml` — the blocking gate counterpart. **RED ⟺ copier-sync can open a real PR.**
  Drift = any `git diff` after `copier update` **excluding `.copier-answers.yml`**. It
  deliberately does NOT use `copier check-update` (tag comparison would stay permanently red as
  `template/v1` floats forward). Neither workflow uses `--overwrite` (that flag is `copier copy`
  only; `copier update` rejects it with exit 2).

## Renovate config — two files, two roles

- **`default.json`** is the **org-wide shared preset**. Every consumer's `renovate.json` does
  `extends: ["github>Rubio-Enterprises/.github"]`, which resolves to this file — so editing it
  changes Renovate behavior fleet-wide. Key rules: built-in **`mise` manager disabled**
  (consumer `.mise.toml` pins are template-owned; letting Renovate bump them thrashes against
  copier-sync); **`github-actions` manager disabled for the rendered `.github/workflows/standards.yml`**
  (its action pins are template-owned too — same drift thrash; a re-enable rule keeps the ONE
  exception, the `Rubio-Enterprises/.github` reusable-workflow `# v1` digest, Renovate-driven);
  **automerge** for non-major updates of stable (≥ 1.0.0) deps; **human-merge-only**
  for the `jdx/mise` CLI pin and `Rubio-Enterprises/standards` template-drift (the two `KEEP LAST`
  rules). Two `customManagers` track non-standard pins: `.copier-answers.yml`
  `_commit: template/vX.Y.Z` → standards template tags, and the `# renovate: … jdx/mise`
  workflow `version:` markers.
- **`renovate.json`** is *this repo's own* config and merely `extends` the preset above.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues; external PRs are not a triage request surface. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the default mattpocock/skills triage labels: `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, and `wontfix`. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo: use root `CONTEXT.md` and root `docs/adr/` when they
exist. See `docs/agents/domain.md`.

## Conventions

Conventional Commits, enforced by review + release-please (release-please reads the **PR
title** at squash-merge — the title, not individual commits, drives the version bump). There
is no local commitlint/lefthook in this repo. Default branch is `main`; open PRs as drafts by
default per the global git-workflow rules.
