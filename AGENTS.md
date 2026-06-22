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
four `standards` layers and their independent tag streams) lives in `Governance/CLAUDE.md`
one level up — this file covers only what is specific to working *inside* `dot-github`.

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
| `secret-scan.yml` | `mode: gitleaks` (PR-diff scan) or `mode: trufflehog` (scheduled full-history); `.trufflehogignore` opt-out | pinned **`audit/v1.4.0`** |
| `e2e.yml` | Playwright harness; detects `scripts.e2e`, then runs `mise run e2e` / `npm run e2e`. Does **not** start a dev server (see the dev-server contract in its header) | — |
| `lint-hooks.yml` | `lefthook run pre-commit --all-files` + a commit-msg smoke test against the consumer | — |
| `rust-test.yml` | Swatinem cache + `mise run test` (nextest JUnit) | — |

**Pin asymmetry (gotcha):** `audit.yml` tracks the *floating* `audit/v1`; `secret-scan.yml`
pins an *exact* `audit/v1.X.Y`. They are deliberately not kept in lockstep — read the actual
`ref:` before reasoning about which `standards` content a given reusable executes.

`copier-sync.yml` and `copier-check.yml` are also consumer-called reusables, but they
orchestrate consumer-side `copier update` rather than running `standards` audit content
(see their own section below). `release-please.yml` and `floating-tag-floor-check.yml` are
this repo's own ops, not reusables.

`workflow-templates/` holds the org "New workflow" picker starters, for **non-Copier repos
only**. Names are suffixed `-starter` (`standards-audit-starter`, `e2e-starter`) so they
never collide with the Copier-rendered `standards.yml` / `e2e` job names. They reference
`@v1` (the floating major), which resolves only once a `v1` release exists.

## The load-bearing release ritual

This is the single most important thing to get right in this repo.

1. The audit reusables check out `standards` at an `audit/*` pin. **Editing `standards`'
   audit layer does NOT reach the fleet until that pin advances** — which is the entire
   reason this repo cuts releases.
2. When you bump an `audit/*` ref (e.g. `secret-scan.yml`'s `audit/v1.4.0` → a newer tag),
   you must **cut a new `.github` release**. Consumers pin these reusables by SHA with a
   trailing `# v1` comment; Renovate bumps that SHA only when the floating `v1` tag moves.
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
unreleased SHA is exactly the drift the floor-check exists to catch. To ship an audit-pin
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
  `ubuntu-slim`). Heavier jobs (`e2e`, `lint-hooks`, `copier-check`, `rust-test`) use
  `ubuntu-latest`.
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
  copier-sync); **automerge** for non-major updates of stable (≥ 1.0.0) deps; **human-merge-only**
  for the `jdx/mise` CLI pin and `Rubio-Enterprises/standards` template-drift (the two `KEEP LAST`
  rules). Two `customManagers` track non-standard pins: `.copier-answers.yml`
  `_commit: template/vX.Y.Z` → standards template tags, and the `# renovate: … jdx/mise`
  workflow `version:` markers.
- **`renovate.json`** is *this repo's own* config and merely `extends` the preset above.

## Conventions

Conventional Commits, enforced by review + release-please (release-please reads the **PR
title** at squash-merge — the title, not individual commits, drives the version bump). There
is no local commitlint/lefthook in this repo. Default branch is `main`; open PRs as drafts by
default per the global git-workflow rules.
