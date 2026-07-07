# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other agents when
working with code in this repository. `CLAUDE.md` is a symlink to this file — **edit
`AGENTS.md`**.

## What this repo is

`Rubio-Enterprises/.github` (local dir `Governance/dot-github`, remote `origin =
git@github-personal:Rubio-Enterprises/.github.git`). Public org repo holding the
**GitHub Actions workflows** that run the fleet's PR gates — the seven
property-targeted gate workflows the org rulesets **inject** into consumer PRs,
plus the handful of reusables a consumer's `.github/workflows/standards.yml` still
thin-calls. In the three-repo governance system it is the *delivery vehicle*:
`standards` defines the rules, **this repo runs them in CI**, and
`dot-github-private` enforces repo settings. The macro architecture (the three
repos, the `standards` content layers, the channel release model, and the four
propagation paths) is documented canonically in the sibling `standards` repo's
`AGENTS.md` → "How changes propagate to the fleet"; this file covers only what is
specific to working *inside* `dot-github`.

This is a **bootstrap repo**: not rendered from the Copier template, not standards-onboarded,
and excluded from its own audit. Consequence: there is **no `.mise.toml`, no `lefthook.yml`,
no `.editorconfig`, and no local task runner** here. The `mise install` / `mise run lint|test`
steps in `CONTRIBUTING.md` describe the *consumer* dev loop and do **not** apply in this repo.
Work here is editing workflow YAML + JSON config directly; `actionlint` is the practical
local check for the workflow files before pushing. Otherwise validation happens in GitHub CI.

## The reusable workflows (`.github/workflows/`)

Two delivery mechanisms live here now.

**Gate workflows — the seven property-targeted Required Governance Workflows.** A
repo runs one iff it carries the matching `gate-*` custom property (set in
`.github-private` Terraform); the org gate rulesets **inject** them into the
consumer's PR checks. They are **not** thin-called from `standards.yml`. The
content-bearing gates resolve their `standards` content at runtime from the
repo's channel — `canary` for ring repos, `stable` for the fleet (read from the
`ring` custom property). The ruleset pins the workflow *file* to the
Terraform-managed `gates/wf-v1` tag; the *content* floats on the channel.

| Gate workflow | Gate Family | What it does | Standards content |
|---|---|---|---|
| `audit.yml` | `gate-audit` | Layer A (`check.sh`) + B (`check-jsonschema`) + C (Conftest `--combine`) + npm-lockfile integrity + managed-content strict gate | channel (`canary`/`stable`), runtime-resolved |
| `lint-format.yml` | `gate-lint-format` | runtime-renders the canonical lint configs from the channel and runs the config-flag linters (markdownlint, yamllint, ruff, biome) | channel, runtime-resolved |
| `secret-scan.yml` | `gate-secret-scan` | `mode: gitleaks` (PR diff) / `mode: trufflehog` (scheduled full-history, `--results=verified`) | channel, runtime-resolved |
| `pr-title.yml` | `gate-pr-title` | commitlint on the PR title with rules from the channel (not a hardcoded types list) | channel, runtime-resolved |
| `typecheck-ts.yml` | `gate-typescript` | `mise run typecheck` (ts-* archetypes), graceful no-task notice | — |
| `test-py.yml` | `gate-python-tests` | `uv run pytest` (py-* + `has_test`) | — |
| `rust-test.yml` | `gate-rust-tests` | Swatinem cache + `mise run test` (nextest JUnit); Cargo.toml guard + bucket job | — |

**Thin-called reusables** — still invoked via `uses:` / `workflow_call` from a
consumer's rendered `standards.yml` (or a release workflow):

| Workflow | What it does | Pin |
|---|---|---|
| `lint-hooks.yml` | `lefthook run pre-commit --all-files` + a commit-msg smoke test — the CI floor for tools with no config-path flag (shellcheck, pyright, clippy…) that `gate-lint-format` doesn't cover; **stays rendered in `standards.yml`** | — |
| `e2e.yml` | Playwright harness; detects `scripts.e2e`, then runs `mise run e2e` / `npm run e2e`. Does **not** start a dev server (see the dev-server contract in its header) | — |
| `bump-brew.yml` | Bumps a `:git`-strategy Homebrew formula in `homebrew-tap` to the **release tag that triggered the caller** — rewrites the top-level source `tag:` + `revision:` and inserts/updates `version` (no tarball/sha256, since `:git` formulae build from source). Replaces `mislav/bump-homebrew-formula-action`, which can't handle source-build formulae or private-repo archives | — |

`bump-brew.yml` is the odd one out: it's invoked from a consumer's **release/tag workflow**, not from `standards.yml` (the My-Tools Go/Swift CLIs that ship a `:git` formula in the tap call it on release). It needs a `tap-token` secret (PAT with `contents:write` on the tap). **Filename ≠ display name** — the file is `bump-brew.yml` (renamed from `bump-homebrew-git`) but its internal `name:` still reads `bump-homebrew-git (reusable)`; `uses:` the *path* `…/bump-brew.yml@v1`.

**The content gates resolve `standards` content by channel, not by a frozen
`audit/v1` pin.** `audit.yml` and `secret-scan.yml` were moved off the frozen
`audit/v1` clone to channel resolution in the sweep (a repo's `ring` property
picks `canary` vs `stable` at runtime). Still read the actual `ref:` a gate
resolves before reasoning about which `standards` content it runs.

`copier-sync.yml` / `copier-check.yml` are **gone** — the consumer template-drift
ritual is replaced by Renovate's native copier manager (auto-merged re-render
PRs; see the Renovate section). `release-please.yml` and
`floating-tag-floor-check.yml` are this repo's own ops, not reusables.

There is intentionally **no `workflow-templates/`** (org "New workflow" picker starters).
The two starters that once lived there (`standards-audit-starter`, `e2e-starter`) were
removed: zero repos ever adopted them, and the audit one silently broke when `standards`
went private (a picker-copied caller passes no `secrets: inherit`, so the standards-reader
token mint fails). Repos join the fleet via Copier (`/onboard-repo`), which renders
`standards.yml` directly. Don't reintroduce starters without wiring the secrets contract.

## The load-bearing release ritual

This is the single most important thing to get right in this repo. Three pins
coexist, and they move differently.

1. **Gate content resolves by channel, not by an `audit/v1` pin.** The
   content-bearing gate workflows (`audit.yml`, `lint-format.yml`,
   `secret-scan.yml`, `pr-title.yml`) clone `standards@<channel>` at runtime —
   `canary` for ring repos, `stable` for the fleet (the repo's `ring` property
   picks). So an audit-rule **content** change reaches the fleet when `standards`
   promotes `canary` → `stable` (see `standards` `RELEASES.md`), with **no
   `.github` release**. There is no `audit/v1` tag to move any more.
2. **The gate workflow *files* are pinned by the org rulesets to `gates/wf-v1`.**
   That tag is Terraform-managed in `.github-private`. A change to a gate
   workflow's own code (steps/logic) is *plumbing*: land it here, soak it via a
   temporary evaluate-mode duplicate ruleset at `refs/heads/main`, then advance
   `gates/wf-v1` to the new SHA via a **TF PR** in `.github-private` (and remove
   the duplicate). release-please does **not** own `gates/wf-v1`.
3. **Thin-called reusables (`lint-hooks`, `e2e`, `bump-brew`) still ride the
   `.github` release + floating `v1`.** Consumers pin them by SHA with a trailing
   `# v1`; Renovate bumps that SHA when the floating `v1` moves. Releases are
   automated by **release-please** (`release-please.yml`, `release-type: simple`,
   manifest `.release-please-manifest.json`): the release PR's **title** decides
   the bump; on merge it tags `vX.Y.Z`, creates the release, then **moves `v1`
   onto the release commit** and verifies the push landed.
4. `floating-tag-floor-check.yml` (daily cron) is the backstop for point 3:
   **`v1` MUST equal the latest strict-semver release commit.** (Its sister
   invariant in `standards` no longer exists — `standards` releases by channel
   now, not by advancing a floating tag.)

**Never create or move `v1` or `gates/wf-v1` by hand** — release-please owns
`v1`; Terraform owns `gates/wf-v1`. A hand-moved tag on an unreleased SHA is
exactly the drift these checks exist to catch.

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
  `ubuntu-slim`) — including `lint-hooks` and `rust-test`. Only `e2e`
  still uses `ubuntu-latest`.

## Renovate config — two files, two roles

- **`default.json`** is the **org-wide shared preset**. Every consumer's `renovate.json` does
  `extends: ["github>Rubio-Enterprises/.github"]`, which resolves to this file — so editing it
  changes Renovate behavior fleet-wide. Key rules: the **native `copier` manager enabled with
  trust** (Layer 3c — reads `_commit`/`_src_path` from `.copier-answers.yml`, tracks the
  `standards` template `template/vX.Y.Z` tags, and ships an **auto-merged** full `copier update`
  re-render inside its own PR — this replaces the retired `copier-sync`/`copier-check` ritual and
  the old `_commit` regex customManager); built-in **`mise` manager disabled** (consumer
  `.mise.toml` pins are template-owned; letting Renovate bump them thrashes against the copier
  re-render); **`github-actions` manager disabled for the rendered `.github/workflows/standards.yml`**
  (its action pins are template-owned too — same drift thrash; a re-enable rule keeps the ONE
  exception, the `Rubio-Enterprises/.github` reusable-workflow `# v1` digest, Renovate-driven);
  **automerge** for non-major updates of stable (≥ 1.0.0) deps; **human-merge-only** for the
  `jdx/mise` CLI pin (`KEEP LAST`), while the `Rubio-Enterprises/standards` template re-render
  auto-merges through Renovate's own merge engine (waits for all checks green). One `customManager`
  remains — the `# renovate: … jdx/mise` workflow `version:` markers; the standards re-render is
  the native `copier` manager above, not a regex manager.
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
