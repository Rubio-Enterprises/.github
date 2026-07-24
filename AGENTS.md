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
`ring` custom property). The ruleset pins the workflow *file* to the `gates/wf-v1` tag; the tag object is
published by this repository's guarded Publication Request/CAS workflow while
Terraform owns the consuming rulesets and ref name. The *content* floats on the
channel.

| Gate workflow | Gate Family | What it does | Standards content |
|---|---|---|---|
| `audit.yml` | `gate-audit` | Layer A (`check.sh`) + B (`check-jsonschema`) + C (Conftest `--combine`) + npm-lockfile integrity + managed-content strict gate | channel (`canary`/`stable`), runtime-resolved |
| `lint-format.yml` | `gate-lint-format` | runtime-renders the canonical lint configs from the channel and runs the config-flag linters (markdownlint, yamllint, ruff, biome) | channel, runtime-resolved |
| `secret-scan.yml` | `gate-secret-scan` | `mode: gitleaks` (PR diff) / `mode: trufflehog` (scheduled full-history, `--results=verified`) | channel, runtime-resolved |
| `pr-title.yml` | `gate-pr-title` | commitlint on the PR title with rules from the channel (not a hardcoded types list) | channel, runtime-resolved |
| `typecheck-ts.yml` | `gate-typescript` | `mise run typecheck` (ts-* archetypes), graceful no-task notice | — |
| `test-py.yml` | `gate-python-tests` | `uv run pytest` (py-* + `has_test`) | — |
| `rust-test.yml` | `gate-rust-tests` | validates Rust Test Execution Policy, routes `glue`/`linux-arm`, always runs `mise run test`, and fails closed through the bucket job | — |

`rust-test.yml` reads `RUST_TEST_WORKLOAD_CLASS` and `RUST_TEST_TIMEOUT_MINUTES`
for direct consumer events. Reusable callers must pass required `workload-class`
and `timeout-minutes` inputs. Direct execution is identified from the immutable
`github.workflow_ref` prefix because a reusable call keeps the caller's
`github.event_name` and workflow ref. Supported classes are `glue` and `linux-arm`; timeout
must be an integer from 5 through 120. Invalid policy runs only the hosted slim
pre-check before failing, and only an exact successful workload lets the aggregate
pass. There is no Cargo-manifest detector or successful no-project consumer path.

**Thin-called reusables** — still invoked via `uses:` / `workflow_call` from a
consumer's rendered `standards.yml` (or a release workflow):

| Workflow | What it does | Pin |
|---|---|---|
| `lint-hooks.yml` | `lefthook run pre-commit --all-files` + a commit-msg smoke test — the CI floor for tools with no config-path flag (shellcheck, pyright, clippy…) that `gate-lint-format` doesn't cover; **stays rendered in `standards.yml`** | — |
| `e2e.yml` | Playwright harness; detects `scripts.e2e`, then runs `mise run e2e` / `npm run e2e`. Does **not** start a dev server (see the dev-server contract in its header) | — |
| `bump-brew.yml` | Bumps a `:git`-strategy Homebrew formula in `homebrew-tap` to the **release tag that triggered the caller** — rewrites the top-level source `tag:` + `revision:` and inserts/updates `version` (no tarball/sha256, since `:git` formulae build from source). Replaces `mislav/bump-homebrew-formula-action`, which can't handle source-build formulae or private-repo archives | — |

`bump-brew.yml` is the odd one out: it's invoked from a consumer's **release/tag workflow**, not from `standards.yml` (the My-Tools Go/Swift CLIs that ship a `:git` formula in the tap call it on release). Push auth: preferred is the **rubio-tap-push App** — callers use `secrets: inherit` and the reusable mints a per-run token (contents:write, scoped to the tap repo) from the `TAP_PUSH_APP_ID`/`TAP_PUSH_APP_PRIVATE_KEY` org secrets; the legacy `tap-token` PAT secret remains a fallback until the last caller migrates. **Filename ≠ display name** — the file is `bump-brew.yml` (renamed from `bump-homebrew-git`) but its internal `name:` still reads `bump-homebrew-git (reusable)`; `uses:` the *path* `…/bump-brew.yml@v1`.

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
   A change to a gate workflow's own code (steps/logic) is *plumbing*: land the
   candidate here, perform proportional Candidate Validation (normally a
   temporary Terraform-owned evaluate-mode duplicate ruleset at the exact
   candidate), then merge a request-only PR changing
   `.github/plumbing-ref/publication-request.json`. The push-triggered publisher
   performs an exact expected-current compare-and-swap and verifies the final
   remote ref. Terraform owns only the consuming rulesets/ref name;
   release-please owns neither this tag nor its publisher. Rollback uses the
   validation-first manual workflow. Full runbook:
   [`docs/plumbing-ref-publication.md`](docs/plumbing-ref-publication.md).
3. **Thin-called reusables (`lint-hooks`, `e2e`, `bump-brew`) still ride the
   `.github` release + floating `v1`.** Consumers pin them by SHA with a trailing
   `# v1`; Renovate bumps that SHA when the floating `v1` moves. Releases are
   automated by **release-please** (`release-please.yml`, `release-type: simple`,
   manifest `.release-please-manifest.json`): the release PR's **title** decides
   the bump; on merge it tags `vX.Y.Z`, creates the release, then **moves `v1`
   onto the release commit** and verifies the push landed.
4. `floating-tag-floor-check.yml` (daily cron) is the backstop for point 3:
   **every floating major tag `vN` present here MUST equal the latest
   strict-semver `vN.Y.Z` release commit in its OWN major line.** Each major is
   checked independently, so an older major that has stopped receiving releases
   (`v1` once `v2.x` is shipping) stays guarded for as long as its tag exists
   rather than going unchecked. (Its sister invariant in `standards` no longer
   exists — `standards` releases by channel now, not by advancing a floating
   tag.)

**Never create or move a floating major tag (`v1`, `v2`, …) or `gates/wf-v1` by
hand.** Release-please owns the floating majors. The repository-owned Plumbing
Ref publisher owns `gates/wf-v1` through a request-only PR and exact compare-and-
swap; its manual rollback workflow owns backward moves. Direct owner mutation is
last-resort recovery only and must retain the exact lease + remote reread contract.

## Cross-cutting workflow patterns (easy to break when adding/editing a reusable)

- **Source-repository no-op guard.** Consumer-shaped workflows declare direct triggers so
  GitHub accepts them as required-workflow plumbing, but `.github` itself cannot satisfy those
  consumer contracts. Guard direct `pull_request` and `merge_group` events from this source
  repository explicitly. The Rust workflow skips its workload and lets the bucket report the
  source no-op; consumer direct events and `workflow_call` remain strict.
- **Plumbing Ref trust boundary.** Post-bootstrap Publication Request validation
  and publication execute `plumbing_ref.py` from the protected base/before
  revision, never from the proposed request commit. Only the request JSON may
  change. The publisher runs hosted with the repo-scoped `GITHUB_TOKEN`; do not
  add a PAT, App, environment, actor allowlist, or Terraform wrapper. Keep the
  workflows free of Actions concurrency — an exact Git lease is the correctness
  mechanism, while GitHub concurrency is not FIFO.
- **`setup-uv` before `mise-action`.** Every mise-using job installs
  `astral-sh/setup-uv@…` *before* `jdx/mise-action`. Without uv on PATH, mise ≥ 2026.6.2
  routes pipx installs through pip's `--uploaded-prior-to`, which cold runners' bootstrap pip
  (< 26) rejects → hard fail. Copy this step into any new mise-based reusable.
- **mise CLI pin.** Each `jdx/mise-action` version carries a
  `# renovate: datasource=github-releases depName=jdx/mise` marker so Renovate bumps them
  together (human-merge only — see below). Keep the marker when you add a job.
- **Runner Route selection.** Glue-tier jobs use
  `runs-on: ${{ fromJSON(vars.RUNNER_GLUE || '["ubuntu-slim"]') }}`. Rust workload policy
  additionally maps symbolic `linux-arm` through `RUNNER_LINUX_ARM` with
  `ubuntu-24.04-arm` as its class-specific hosted fallback. Repository policy stores only
  the symbolic class and timeout; never copy physical self-hosted labels into a public
  workflow. The Rust aggregate remains on the glue route. Only `e2e` uses `ubuntu-latest`.

## Renovate config — three files, three roles

- **`default.json`** is the **org-wide shared preset**. Every consumer's `renovate.json` does
  `extends: ["github>Rubio-Enterprises/.github"]`, which resolves to this file — so editing it
  changes Renovate behavior fleet-wide. Key rules: built-in **`mise` manager disabled** (consumer
  `.mise.toml` pins are template-owned; letting Renovate bump them thrashes against the copier
  re-render); **`github-actions` manager disabled for the rendered `.github/workflows/standards.yml`**
  (its action pins are template-owned too — same drift thrash; a re-enable rule keeps the ONE
  exception, the `Rubio-Enterprises/.github` reusable-workflow `# v1` digest, Renovate-driven);
  **automerge** for non-major updates of stable (≥ 1.0.0) deps; **human-merge-only** for the
  `jdx/mise` CLI pin (`KEEP LAST`). One `customManager` remains — the `# renovate: … jdx/mise`
  workflow `version:` markers.
- **`copier.json`** is the **copier-only preset**, composed by `default.json` via `extends`. It
  holds the two pieces of copier policy: the trust switch (`copier.ignoreScripts: false`) and the
  `Rubio-Enterprises/standards` template re-render rule (Layer 3c — reads `_commit`/`_src_path`
  from `.copier-answers.yml`, tracks the `standards` template `template/vX.Y.Z` tags via a
  `versioning` regex, and ships an **auto-merged** full `copier update` re-render inside its own
  PR through Renovate's own merge engine, which waits for all checks green — this replaces the
  retired `copier-sync`/`copier-check` ritual and the old `_commit` regex customManager).

  It is a **separate file so that repos which run their own Renovate can `extends` it directly**
  (`github>Rubio-Enterprises/.github:copier`) without inheriting the whole org preset — see
  `mac-dev-playbook`, which is self-managed and whose ~60 hand-tuned Docker managers must not
  pick up `config:best-practices`, the 7-day `minimumReleaseAge`, or the blanket automerge rule.
  The org and the self-managed repos therefore share ONE definition of the copier rule instead of
  copying it.

  **Ordering caveat:** Renovate concatenates a preset's `packageRules` *before* the extending
  config's own (`mergeChildConfig`: `parent.concat(child)`), so the copier rule now sits *ahead*
  of `default.json`'s own rules rather than last. That is currently behaviour-neutral (nothing in
  `default.json` matches `matchManagers: ["copier"]`, and the one broadly-matching rule — blanket
  automerge — sets `automerge`/`platformAutomerge` to the same values the copier rule does). If
  you ever add a rule to `default.json` that *does* match the copier dep, it will now win over
  this preset — which is the opposite of the old `KEEP LAST` behaviour.
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
