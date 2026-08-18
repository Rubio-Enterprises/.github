# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other agents when
working with code in this repository. `CLAUDE.md` is a symlink to this file — **edit
`AGENTS.md`**.

## What this repo is

`Rubio-Enterprises/.github` (local dir `Governance/dot-github`, remote `origin =
git@github-personal:Rubio-Enterprises/.github.git`). Public org repo holding the
**GitHub Actions workflows** that run the fleet's PR gates — the five
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

**Gate workflows — the five property-targeted Required Governance Workflows.** A
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
| `typecheck-ts.yml` | `gate-typescript` | `mise run typecheck` (repos declaring `has_typescript`), graceful no-task notice | — |

Canonical non-E2E tests are deliberately NOT a Gate Family workflow: the
`gate-tests` org ruleset requires each enforcing repository's own repo-local
`test-gate` status context (the Test Gate Contract — standards ADR-0020). The
central `test-py.yml` / `rust-test.yml` overlap workflows and their
`gate-python-tests` / `gate-rust-tests` families are retired (standards#389,
2026-07-30).

Beyond the gates and the thin-called reusables below, this repo also holds
`testflight.yml` (the signed Apple build reusable — see
[`docs/reusable-workflows/testflight.md`](docs/reusable-workflows/testflight.md)
and [ADR-0001](docs/adr/0001-shared-apple-testflight-release-architecture.md)),
`test-gate.yml`, `copilot-setup-steps.yml`, `workflow-validation.yml`, and
`plumbing-ref-publish.yml` (this repo's own ops, not reusables).

**Thin-called reusables** — still invoked via `uses:` / `workflow_call` from a
consumer's rendered `standards.yml` (or a release workflow):

| Workflow | What it does | Pin |
|---|---|---|
| `lint-hooks.yml` | `lefthook run pre-commit --all-files` + a commit-msg smoke test — the CI floor for tools with no config-path flag (shellcheck, pyright, clippy…) that `gate-lint-format` doesn't cover; **stays rendered in `standards.yml`** | — |
| `e2e.yml` | Playwright harness; detects `scripts.e2e`, then runs `mise run e2e` / `npm run e2e`. Does **not** start a dev server (see the dev-server contract in its header) | — |
| `bump-brew.yml` | Bumps a `:git`-strategy Homebrew formula in `homebrew-tap` to the **release tag that triggered the caller** — rewrites the top-level source `tag:` + `revision:` and inserts/updates `version` (no tarball/sha256, since `:git` formulae build from source). Replaces `mislav/bump-homebrew-formula-action`, which can't handle source-build formulae or private-repo archives | — |
| `cloud-setup-smoke.yml` | Runs the consumer's Claude Code on the web setup chain (`cloud-setup-shim.sh` → `common/cloud-setup.sh` → `repo-local/cloud-setup.sh`) on Linux under `CLOUD_SETUP_SMOKE`, so a cloud-environment defect fails the PR that introduces it | — |

`cloud-setup-smoke.yml` is the only reusable here that runs **hosted x64 by
default** rather than glue. That is the point of it: the cloud environment is x64
Ubuntu and the glue pool is arm64, so routing it to glue would add
architecture-specific false positives to a check whose whole question is "would
this work in the cloud?". It reads `vars.RUNNER_CLOUD_SMOKE` with an
`ubuntu-latest` fallback; that variable is deliberately **not** declared in
`.github-private`'s `runners.tf` (declaring it would create it live and could
point the job at a label no pool advertises — the 2026-07-21 stall). Absent
variable means "behave as written", per the `RUNNER_GLUE_HEAVY` precedent.

Two things about it are load-bearing and easy to break:

- **It probes before it runs, and the probe is a safety gate, not an
  optimization.** A consumer whose rendered `scripts/common/cloud-setup.sh`
  predates `CLOUD_SETUP_SMOKE` (standards < `template/v1.55.56`) does not ignore
  the variable — it runs the **full cloud path** on the runner, registering a
  global git credential helper and attempting marketplace clones. The workflow
  greps the core for `CLOUD_SETUP_SMOKE` and skips with a notice when absent.
  Never invoke a core that cannot honour the contract.
- **It installs mise before invoking the chain.** The chain's own toolchain
  bootstrap (the SessionStart hook core) opens with
  `command -v mise >/dev/null 2>&1 || exit 0` — abort-proof, so on a runner
  without mise it silently no-ops, every warm step is then skipped for want of a
  tool, and the smoke passes green while proving nothing.

`bump-brew.yml` is the odd one out: it's invoked from a consumer's **release/tag workflow**, not from `standards.yml` (the My-Tools Go/Swift CLIs that ship a `:git` formula in the tap call it on release). Push auth: preferred is the **rubio-tap-push App** — callers use `secrets: inherit` and the reusable mints a per-run token (contents:write, scoped to the tap repo) from the `TAP_PUSH_APP_ID`/`TAP_PUSH_APP_PRIVATE_KEY` org secrets. A caller that does not pass them fails fast with an explicit error. **Filename ≠ display name** — the file is `bump-brew.yml` (renamed from `bump-homebrew-git`) but its internal `name:` still reads `bump-homebrew-git (reusable)`; `uses:` the *path* `…/bump-brew.yml@v1`.

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
   release-please owns neither this tag nor its publisher. Backward movement is
   reserved for last-resort direct owner recovery. Full runbook:
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

**Never create or move a floating major tag (`v1`, `v2`, …) by hand.**
Release-please owns the floating majors. The repository-owned Plumbing Ref
publisher owns normal forward movement of `gates/wf-v1` through a request-only PR
and exact compare-and-swap. There is intentionally no automated rollback
workflow or CLI command. A backward move is last-resort direct owner recovery
only and must follow the exact lease, Gate Family validation, and final remote
reread contract in the runbook.

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
- **uv CLI pin — `setup-uv` MUST carry an exact `version:`.** Every
  `astral-sh/setup-uv` step pins an exact uv version behind a
  `# renovate: datasource=github-releases depName=astral-sh/uv` marker (human-merge only,
  like the mise pin). This is not cosmetic determinism — it changes the action's network
  behaviour. Read `setupUv()` in the action: it calls `resolveUvVersion()` **before**
  `tryGetFromToolCache()`. With no `version:` the specifier is `latest`, so
  `LatestVersionResolver` must `fetch` `astral-sh/versions`'
  `v1/uv.ndjson` from `raw.githubusercontent.com` under a hard
  `AbortSignal.timeout(5_000)` on **every** run, before the tool cache is ever consulted —
  an unconditional 5-second external dependency ahead of every linter and test in the
  fleet, and the first thing to snap when the shared runners are saturated. With an exact
  version `ExactVersionResolver` returns offline, so a warm runner tool-cache hit installs
  uv with **zero** network calls. Keep the marker and the exact pin when you add a job.
  **Caveat, so nobody over-claims this:** on a *cold* tool cache the pin does not remove
  the fetch — `downloadVersion()` → `getArtifact()` → `fetchManifest()` still reads the
  same manifest to learn the artifact URL, and the action exposes no input to bypass it
  (`manifest-file` only swaps the URL; `fetch` is undici, so a local path is not an
  option). The pin converts "every run, always" into "first run per runner per uv
  version" — it does not make the dependency disappear.
- **mise CLI pin.** Each `jdx/mise-action` version carries a
  `# renovate: datasource=github-releases depName=jdx/mise` marker so Renovate bumps them
  together (human-merge only — see below). Keep the marker when you add a job.
- **Runner Route selection.** Glue-tier jobs use
  `runs-on: ${{ fromJSON(vars.RUNNER_GLUE || '["ubuntu-slim"]') }}`. Rust workload policy
  additionally maps symbolic `linux-arm` through `RUNNER_LINUX_ARM` with
  `ubuntu-24.04-arm` as its class-specific hosted fallback. Repository policy stores only
  the symbolic class and timeout; never copy physical self-hosted labels into a public
  workflow. The Rust aggregate remains on the glue route. `e2e` has its own tier —
  `RUNNER_E2E` with `ubuntu-latest` as its hosted fallback — because Playwright suites are
  multi-GB jobs: its self-hosted value targets the linux-arm64 VM pool and must NEVER be
  pointed at glue (tried and reverted in #154 after it OOM-killed the lightest consumer).
  macOS is two-tiered and the tier is a deliberate choice, not a detail: short (≲2 min)
  lints/shell tests take `RUNNER_MACOS_LITE` (cheap hosted) — `lint-hooks`' swift route is
  one — while `RUNNER_MACOS` (the scarce self-hosted Tart pool) is reserved for long,
  high-frequency Xcode work such as `testflight`'s signed build. Do not promote a lint job
  onto `RUNNER_MACOS` to "match" the other macOS job.
- **The `glue-heavy` tier is DECLARED by the consumer, never derived.** `lint-hooks` and
  `typecheck-ts` route their Linux job by the repo's `lint_hooks_workload_class` /
  `typecheck_workload_class` answer (standards
  [ADR-0022](https://github.com/Rubio-Enterprises/standards/blob/main/docs/adr/0022-glue-heavy-workload-class.md)),
  read out of the sparse-checked-out `.copier-answers.yml` in a small `detect` / `route`
  job — `runs-on:` is evaluated before checkout, which is the only reason that job exists.
  Both previously keyed on `has_typescript`, which is a proxy for *language*, not memory:
  it is true for `static-webpage-template` and `kodus-ai` alike, so it put **15 repos onto
  a 3-slot pool**, where a repo that does not need the tier takes a slot from one that
  does. Two answers rather than one repo-wide flag because ADR-0018 rejected the repo-wide
  scalar and required workload-keying. **Capability outranks resource:** a `has_swift` /
  `has_homebrew_formulae` repo goes to macOS regardless of its declared class, because a
  Linux tier cannot satisfy a requirement Linux cannot satisfy at all. `RUNNER_GLUE_HEAVY`
  is deliberately absent from `runners.tf` until the pool converges, so a `glue-heavy`
  declaration degrades through the fallback chain to glue rather than to hosted minutes —
  declaring a class does not create its route.
- **Consumer-set Actions variables these reusables read.** `vars` in a reusable resolves in
  the **caller's** context, so each of these is set on (or above) the consumer repo, not here.
  A repo-level value overrides an org-level one.

  | Variable | Read by | Effect when set |
  |---|---|---|
  | `GO_PRIVATE_MODULE_REPOS` | `lint-hooks` | comma-separated repo names whose private Go modules get an App-token `insteadOf` route; both steps no-op when unset |
  | `LINT_HOOKS_FORK_ENFORCE` | `lint-hooks` | `true` makes the **fork-mode** hook run blocking. Default (unset) is **warn-only** |

  `LINT_HOOKS_FORK_ENFORCE` exists because #166's `-z` fix does not tighten a working gate —
  it *revives a dead one*. Fork-mode hooks had been passing while resolving zero files, so
  every fork-typed consumer (~16 repos) would turn red simultaneously on a required check for
  pre-existing debt. Warn-only makes the gate visible now; flip repos to enforcing as they
  are cleaned, then set it org-wide once the cohort is clean. The non-fork `--all-files` path
  has no such switch on purpose — it was never broken, and a warn-only escape hatch there
  would let genuinely-green repos start hiding regressions.

## Renovate config — three files, three roles

- **`default.json`** is the **org-wide shared preset**. Every consumer's `renovate.json` does
  `extends: ["github>Rubio-Enterprises/.github"]`, which resolves to this file — so editing it
  changes Renovate behavior fleet-wide. Key rules: built-in **`mise` manager disabled** (consumer
  `.mise.toml` pins are template-owned; letting Renovate bump them thrashes against the copier
  re-render); **`github-actions` manager disabled for the rendered `.github/workflows/standards.yml`**
  (its action pins are template-owned too — same drift thrash; a re-enable rule keeps the ONE
  exception, the `Rubio-Enterprises/.github` reusable-workflow `# v1` digest, Renovate-driven);
  **automerge** for stable (≥ 1.0.0) minor, patch, pin, digest, and `pinDigest` updates after
  the global seven-day soak; **human-merge-only** for majors, pre-1.0 updates, TestFlight, and the
  `jdx/mise` and `astral-sh/uv` CLI pins. Stable and pre-1.0 npm, Cargo, and pin updates use
  distinct groups so a manual member cannot disarm an otherwise-safe stable branch. Four
  `customManager`s remain. The first
  two track the `# renovate: … jdx/mise` and `# renovate: … astral-sh/uv` workflow `version:`
  markers; they are deliberately symmetric, except that uv tags are bare semver (`0.12.1`) while
  mise's are v-prefixed and need `extractVersionTemplate`. The third tracks git-sourced entries in
  `home/.chezmoidata/uv-tools.toml`: recursive matching first binds one TOML table to its GitHub
  repo, then replaces only that table's `version` line. A narrow package rule owns
  `pinDigests: true` (the field is not valid inside a custom manager), bootstrapping tag-only
  entries before update classification; the explicit replacement always writes
  `version = "<sha>"  # <tag>`. The fourth
  reads a Claude Code version out of a Dockerfile `RUN npm install -g @anthropic-ai/claude-code@…`
  line sitting under the conventional `# renovate:` annotation.

  **Dockerfile `ARG`/`ENV` `…_VERSION` pins** ride the shipped `customManagers:dockerfileVersions`
  preset (in `extends`) rather than a hand-rolled regex, because the built-in `dockerfile` manager
  reads only `FROM` lines. That preset matches an *assignment*, which is why a version embedded
  directly in a `RUN` command needs the separate custom manager above; the two can never both
  claim the same line.

  **The `flux` manager is widened past its gotk-only default.** Renovate ships flux with
  `managerFilePatterns` of just `/(?:^|/)gotk-components\.ya?ml$/` — the Flux distribution itself
  and nothing a consumer writes. The preset adds `/(^|/)infrastructure/.+\.ya?ml$/` so a
  `GitRepository.spec.ref.tag` (github-tags) and a Flux `Kustomization.spec.images` entry (docker)
  are seen. **`managerFilePatterns` replaces the default rather than extending it**, so the
  upstream gotk pattern is repeated in the list on purpose — dropping it would stop tracking
  `gotk-components.yaml` fleet-wide. Widening is safe against false positives: Renovate's flux
  schema validates apiVersion prefixes (`kustomize.toolkit.fluxcd.io/`, `source.toolkit.fluxcd.io/`,
  `helm.toolkit.fluxcd.io/`), so a plain kustomize `kustomization.yaml`
  (`kustomize.config.k8s.io/v1beta1`) and ordinary Kubernetes YAML under `infrastructure/` yield
  no deps.

  Four consumer-driven rules are worth knowing before editing this file:

  - **The `deb` datasource has no default registry.** A `datasource=deb` marker carrying no
    `registryUrl` looks up `null` and the pin silently never advances — verified against renovate
    43.288.0, the major `renovatebot/github-action@v46.1.14` defaults to and therefore what
    `.github-private`'s runner executes. A `matchDatasources: ["deb"]` rule supplies Tailscale's
    apt repo. Its `suite=` is **coupled to the consumer's Debian base image**; a consumer on a
    different suite would be offered versions its own apt repo does not serve and needs a
    path-scoped override. The durable fix is a `registryUrl=` field on the consumer's own
    `# renovate:` comment line, which keeps the suite beside the `FROM` that determines it.
  - **The stable-version guard is `!/^v?0/`, not `!/^0/`.** Renovate evaluates
    `matchCurrentVersion` against the raw `currentValue`, so the shorter predicate classifies
    `v0.5.5` as stable. The standing safe rule and every stable npm, Cargo, and pin group use
    `!/^v?0/`; their manual pre-1.0 counterparts use the complementary `/^v?0/`. Keep the
    explicit `agent-sandbox` manual override as defense in depth: its GitRepository tag
    (CRDs/manifests) and controller image must move in one reviewed PR because a split bump briefly
    runs new CRDs against an old controller (kubernetes-sigs/agent-sandbox#844).
  - **Stable and pre-1.0 grouping is deliberately disjoint.** npm and Cargo minor/patch updates,
    plus Renovate's general pin-update branch, have distinct current-version predicates, names,
    and slugs. Stable members inherit the standing safe automerge rule and seven-day soak; pre-1.0
    members are explicitly unarmed. The broad grouping rules apply only when no earlier rule has
    already assigned `groupName`, preserving `config:best-practices`' narrower monorepo groups.
    Later local groups (GitHub Actions, tool runtime dependencies, first-party tool pins,
    TestFlight, and agent-sandbox) set both name and slug so their safer coupled-update boundaries
    remain authoritative.
  - **`@anthropic-ai/claude-code` follows the npm `stable` dist-tag, not `latest`.** `followTag`
    skips Renovate's normal major/minor/patch logic and the stability-days check, so
    `matchUpdateTypes` gating cannot hold it back and automerge is all-or-nothing; it is ON
    because the `stable` tag *is* the vendor's own soak. A weekly `schedule` collapses roughly
    three stable bumps into one PR.

  The tool dependency policy is split deliberately. A semantic-only rule maps runtime updates to
  `rubio-cli-kit` or `typer` to `fix(deps)` so release-please cuts a tool release even when the
  update remains manual. A separate grouping rule puts related non-major updates on one branch even
  while Typer is 0.x: a kit release can raise its Typer floor, and separate branches make `uv lock`
  reject the kit branch before either PR exists. Grouping does not widen automerge. Renovate sets a
  grouped branch's `automerge` only when **every** upgrade enables it, so a group containing a 0.x
  Typer update remains manual; a stable-only group can still use the fast lane. Separate fast-lane
  rules cover stable non-major runtime updates and the registry's first-party tool pins: both skip
  the release-age soak and use scoped platform auto-merge, while major and 0.x updates retain the
  standing manual posture. Registry pins are grouped into one dotfiles PR; the runtime group applies
  independently in each tool repo.

  **`platformAutomerge` is ON as of 2026-08-03** (blanket non-major rule, lockFileMaintenance,
  the first-party reusable digest, stable non-major tool runtime dependencies and registry pins,
  and — in `copier.json` — the template re-render). It was off
  because GitHub-native auto-merge waits only on **required** checks while Renovate's own engine
  waits for **all** checks green, and the fleet had no required checks beyond the audit ruleset.
  `gate-tests` going active (2026-07-30) changed that for the 34 repos carrying
  `gate_tests = true`, which now expose a required `test-gate`. The cost of leaving it off was
  concrete: Renovate merges only *during* a wave, so eight green first-party PRs sat unmerged for
  up to two days and were hand-merged.

  **The gap this flip opened was real, and it was closed the same day — do not re-derive the old
  reasoning from a stale copy of this paragraph.** When `platformAutomerge` went on, `test-gate`
  was the *only* required status **context** in the governance plane (`governance.tf` →
  `required_status_checks`); every other required check is an *injected* gate workflow. So
  `lint-hooks` was required **nowhere**, and native auto-merge — which waits only on required
  checks — could land a PR over a red `lint-hooks` on any of the 39 governed repos. That mattered
  because `lint-hooks` is the CI floor for exactly the tools `gate-lint-format` cannot cover
  (shellcheck, pyright, clippy, swiftformat).

  **`.github-private`#179 (2026-08-03) closed it**: `lint-hooks / lint-hooks` and `e2e / e2e` are
  now required status contexts, via `gate-lint-hooks` / `gate-e2e` property-targeted org rulesets,
  both `active`. Verified against live enforcement rather than the plan — a
  `repos/…/rules/branches/main` read on a gated repo returns ruleset `20320898` requiring
  `lint-hooks / lint-hooks`, and a `gate-e2e = false` repo is correctly *not* asked for `e2e / e2e`.
  Property targeting is what makes that safe: requiring a context on a repo that never renders the
  job would block it permanently, because the check would never report at all. So the
  required-checks-only wait is no longer a downgrade for the two jobs a consumer's `standards.yml`
  actually renders on a PR.

  Residual, and unchanged: the 7 governed repos with `gate_tests = false` (`claude-statusline`,
  `daily-routine`, `devenv-skills`, `homebrew-tap`, `infra-skills`, `mattpocock-skills`,
  `playwright-skills`) have no required test context — but six carry **zero language
  facets** (skills marketplaces / content carriers), so onboarding them to `gate-tests` is *not
  applicable* rather than merely undone: there are no canonical tests, and the template renders no
  `test-gate.yml` (the context is repo-owned, per standards ADR-0020).

  **`rebaseWhen: "automerging"` is the exact fleet posture.** A branch Renovate is configured to
  auto-merge may be rebased while GitHub is holding that merge; manual branches are not churned
  merely for falling behind the base. Keep this at top level because the posture is
  manager-agnostic; `copier.json`'s contract remains copier-scoped configuration only.

  **`prConcurrentLimit: 10` is the ordinary creation limit.** It bounds normal open Renovate PRs;
  it does not close existing backlog and is not a recovery drain. Any explicit force mode remains
  operationally separate from this shared preset, and force is never cleanup.

  **The lockFileMaintenance rule carries `minimumReleaseAge: "0 days"` for the same reason —
  a second, independent starvation.** Under the global 7-day soak Renovate stamps its own
  `renovate/stability-days` *pending* status whenever any refreshed transitive release is
  younger than 7 days; its merge engine waits for ALL checks including that one, and each
  weekly refresh pulls new young releases that re-arm the clock, so the PR stays
  green-but-unmergeable forever (`standards#404`: 8 days, hand-merged 2026-08-03). This does
  NOT generalize to ordinary third-party updates: a *soaking* PR whose clock genuinely runs out
  is working as designed and must not get an age-0 carve-out. The separate first-party reusable
  and tool-dependency exemptions have a different justification: the org deliberately publishes
  and gates those releases itself, so the third-party supply-chain quarantine does not apply. For
  lockfile maintenance, the distinction is instead whether new content structurally re-arms the
  clock before it can expire.
- **`copier.json`** is the **copier-only preset**, composed by `default.json` via `extends`. It
  holds the two pieces of copier policy: the trust switch (`copier.ignoreScripts: false`) and the
  `Rubio-Enterprises/standards` template re-render rule (Layer 3c — reads `_commit`/`_src_path`
  from `.copier-answers.yml`, tracks the `standards` template `template/vX.Y.Z` tags via a
  `versioning` regex, and ships an **auto-merged** full `copier update` re-render inside its own
  PR through Renovate's own merge engine, which waits for all checks green — this replaces the
  retired `copier-sync`/`copier-check` ritual and the old `_commit` regex customManager).

  **The `versioning` regex must parse the `_commit` a consumer actually carries, not just the
  tags `standards` publishes.** Renovate skips any dep whose `currentValue` fails
  `versioning.isValid()`, so a consumer whose `_commit` the regex cannot read is dropped
  *silently* — no error, no PR, forever. A repo hand-rendered from a working checkout rather
  than an exact tag carries a `git describe` `_commit` (`template/v1.55.29-5-g3d6fc78`), which
  the original `$`-anchored regex rejected. `aw-server-rust` and `vibe-kanban` both carry
  exactly that `_commit` and both sat 17 template releases behind, rendering as an eternal
  `pending` / `no PR yet`. Hence the **optional** `prerelease` group. It exists to
  parse the current value only: `standards` publishes exclusively clean `template/vX.Y.Z` tags,
  so nothing in the datasource carries a `-` suffix, and `ignoreUnstable` (default `true`)
  would refuse an unstable target regardless — it still permits the unstable-current →
  stable-target move this depends on. Keep the group optional, and keep the `^template/`
  anchor: the anchor, not the tail, is what filters the `audit/*`, `plugin/*`, `gates/*`, and
  bare `v*` streams.

  **The `prerelease` group did NOT unblock those two repos, and nobody should assume it did**
  (normalizing their `_commit` did). A scoped Renovate drain against both, run with the fix live (verified:
  `copier.json` `lastModified` matched the merge commit), still produced no PR. The
  observed dep was:

  ```
  "depName":      "https://github.com/Rubio-Enterprises/standards.git"
  "currentValue": "template/v1.55.29-5-g3d6fc78"
  "versioning":   "regex:…(?:-(?<prerelease>.+))?$"     ← the fix WAS applied
  "skipReason":   "invalid-value"
  ```

  What that rules out: `_src_path` is the correct full https URL on both repos (read
  directly), so the `gh:`-shorthand datasource failure above is **not** the cause; the
  `git-tags` datasource was queried successfully; the copier manager extracted the file
  (`fileCount: 1, depCount: 1`) and does not itself set `skipReason`; and Renovate's
  `RegExpVersioningApi._parse` is pure regex matching whose only constructor requirement —
  at least one of `<major>`/`<minor>`/`<patch>` — the pattern satisfies. The pattern matches
  the value under native JS `RegExp`, **and under RE2** — Renovate logs
  `DEBUG: Using RE2 regex engine`, and the pattern was subsequently tested against the real
  `re2` module: identical results to native `RegExp`, describe-shaped value matched, foreign
  tag streams still rejected. **RE2 is therefore eliminated as well.**

  **The mechanism of the original stall is now proven, and the caching hypothesis that once
  sat here is refuted** (standards#440, read against Renovate's shipped dist). `skipReason:
  "invalid-value"` for this dep is assigned in `workers/repository/process/lookup/index.js`:
  `isValid = isString(compareValue) && versioningApi.isValid(compareValue)`, and when that is
  false the `else if (compareValue)` branch sets the skip because the copier dep carries no
  digest (`!pinDigests && !currentDigest`). Instantiating Renovate's own `RegExpVersioningApi`
  with both patterns settles it: the `$`-anchored regex returns `isValid = false` for
  `template/v1.55.29-5-g3d6fc78`, the current pattern returns `true`, and both still reject
  `audit/v1.19.0` and bare `v1.2.3`.

  Two caches were checked and **neither can be the cause**. The **repository cache** is
  eliminated structurally: `invalid-value` is a *lookup*-stage skipReason, while the extract
  cache stores *extract*-stage `packageFiles`, so a reused entry cannot carry it. (Worth
  recording anyway, since it is true and surprising: `getFilteredManagerConfig` in
  `workers/repository/extract/extract-fingerprint-config.js` enumerates the fingerprint fields
  — manager, managerFilePatterns, npmrc, npmrcMerge, enabled, ignorePaths, includePaths,
  skipInstalls, registryAliases, fileList — and **`versioning` and `packageRules` are absent**,
  so a `versioning` edit really does not invalidate that cache.) The **preset cache** is
  eliminated too: `config/presets/index.js` persists resolved presets for 15 minutes only when
  `presetCachePersistence` is on, and `.github-private`'s `renovate/config.js` never sets it,
  so it defaults to `false` and every run refetches `copier.json`.

  What remains unexplained is only the single drain observation above, which is **not
  reproducible** against current state. Do not replace it with a new confident story; the
  point of this paragraph is that every layer with a plausible mechanism has been checked.

  Practical consequence, and the part that actually matters: **do not rely on diagnosing this
  class of failure — it is silent by construction.** An unparseable `_commit` produces no
  error, no warning, and dashboard state identical to a healthy repo awaiting its PR. The
  reliable repair is to normalize the offending repo's `_commit` to an exact
  `template/vX.Y.Z` tag, the shape every converged repo carries. The durable fix is detection,
  and it has shipped: `COPIER-COMMIT-EXACT-TAG` (standards#438, on `stable` since
  `template/v1.55.48`) FAILs a present-but-malformed `_commit` on the repo's own next PR —
  absent is a WARN, because 13 test fixtures build minimal answers files. The `prerelease`
  group is kept as defence in depth — it is now known to parse the
  value correctly under Renovate's real engine — but it has never been observed to unblock a
  repo, so it must not be treated as the mechanism anything depends on.

  It is a **separate file so that centrally dispatched repos needing bespoke repo-owned Renovate
  configuration can extend it directly using `extends`** (`github>Rubio-Enterprises/.github:copier`) without
  inheriting the whole org preset — see `mac-dev-playbook`, whose repo-owned `renovate.json` carries
  ~60 hand-tuned Docker managers and must not pick up `config:best-practices`, the 7-day
  `minimumReleaseAge`, or the blanket automerge rule. That repository is centrally dispatched; its
  bespoke configuration changes policy, not dispatch ownership. Both config paths therefore share
  ONE definition of the copier rule instead of copying it.

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
