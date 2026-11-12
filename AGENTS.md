# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) and other agents when
working with code in this repository. `CLAUDE.md` is a symlink to this file — **edit
`AGENTS.md`**.

## What this repo is

`Rubio-Enterprises/.github` (local dir `Governance/dot-github`, remote `origin =
git@github-personal:Rubio-Enterprises/.github.git`). Public org repo holding the
**GitHub Actions workflows** that run the onboarded repositories' PR checks: the
injected **`standards`** Required Workflow, plus the reusables a repository's starter
`.github/workflows/standards.yml` (or a release workflow) calls. In the three-repo
governance system it is the *delivery vehicle*: `standards` ships the checks in
`ghcr.io/rubio-enterprises/base:main`, **this repo runs them in CI**, and
`dot-github-private` enforces repo settings (the `standards` and `test-gate` rulesets
target `standards-onboarded = true`).

This is a **bootstrap repo**, not standards-onboarded: the injected workflow skips
`Rubio-Enterprises/.github`, and there is no `lefthook.yml`. Its own tests and workflow
linters still run in the dev image through `just`.

## Local commands

First command: `just build`. Host prerequisites are Docker, git and `just`, plus a
GHCR login. Never install project toolchains on the host.

- `just setup` syncs the test dependencies into the project volume.
- `just test` runs the contract suite and writes `reports/test/junit.xml`.
- `just check` runs actionlint and zizmor, including the central composite actions.
- `just exec <command...>` runs a command in the same image. For one test:
  `just exec uv run --frozen pytest tests/test_host_first.py::Build::test_mismatched_copy_cannot_move_main`.
- `just shell` opens an interactive shell. `just fmt` and `just check-pr` expose the
  base image's commands, but full standards onboarding is not claimed for this bootstrap.

Pins live in `image/mise.toml`; repository code and language tools execute only in
Compose. The editor attaches through `.devcontainer/devcontainer.json`.

## The workflows (`.github/workflows/`)

**`standards.yml` — the injected Required Workflow** ([`docs/standards-check.md`](docs/standards-check.md)).
The `standards` org ruleset injects it into every repository with `standards-onboarded = true`.
The route job selects a project runner. CI Image jobs call `standards check` and
`standards check-pr`; other jobs use the repository's literal `just` recipes.
Terraform owns the injected ref at `refs/heads/main`.

**`test-gate.yml` — the injected Test Gate** ([`docs/test-gate.md`](docs/test-gate.md)).
Before changing its routing, policy reader, Lifecycle execution, JUnit validation or
artifact consumers, read that contract and its Release Check qualification interface.
Each execution leg keeps `reports/` and attempted `package` output from `dist/` for
seven days, including failures and default-branch runs. Read the artifact naming
and file-boundary contract before changing output paths or adding a consumer. The org ruleset
requires the workflow identity; the required result stays `test-gate`, aggregating
`test-gate-linux` and `test-gate-macos`. The macOS job preserves shape-mode coverage
or runs declared `ci-test-macos` concerns directly. Repositories keep only exception
reasons in `.github/standards.toml`. `bootstrap-test-gate.yml` independently tests this source repo.

The Copier-era gate workflows (`standards-gates`, `lint-hooks`, `typecheck-ts`, `build`),
`cloud-setup-smoke`, the `devcontainer-run` project-execution action and the Copier Renovate
preset are retired (standards v2 flag day, 2026-10-31).

**Reusables** — invoked via `uses:` / `workflow_call` from a repository's starter
`standards.yml` (or a release workflow):

| Workflow | What it does |
| --- | --- |
| `project-route.yml` | Selects native runs-on JSON for project jobs from declared platform/size, RUNNERS and central CI Image admission. Read [`docs/reusable-workflows/project-route.md`](docs/reusable-workflows/project-route.md) before changing routing, adding a caller or skipping image-job bootstrap |
| `actions/macos-tools` | Bootstrap mise for macOS shape jobs or provision locked tools for a declared macOS leg. Both workflows share its temporary recipe-rename compatibility script; see `docs/test-gate.md` for the cache/environment contract |
| `default-branch-check.yml` | Read-only whole-tree Standards and production Test Gate execution. Before changing its callers or triggers, read `docs/standards-check.md`'s default-branch contract |
| `standards-cache.yml` | Verifies public default-branch heads (`docs/reusable-workflows/standards-cache.md`). Writes, on the default branch, the weekly workspace-local trivy-database cache the injected `standards` job restores (its pull-request runs cannot share caches); the starter caller runs it on push to `main` and dispatch with `packages: read`. Keys must match `standards.yml`'s (`tests/test_standards_cache.py`) |
| `release-please.yml` | release-please with the `rubio-release-please` App token; the starter caller runs it when `release-please-config.json` exists. See [`docs/reusable-workflows/release-please.md`](docs/reusable-workflows/release-please.md) |
| `repository-image.yml` | Builds the repository's `ci` target from its own `Dockerfile` on the Build Service (amd64) and publishes `devcontainer-ci:main`; dev images are never built or published (host-first shape §7). See [`docs/reusable-workflows/repository-image.md`](docs/reusable-workflows/repository-image.md) |
| `app-image-pipeline.yml` | One App Image, built once, tested and promoted: a candidate on the Build Service (amd64) or natively on the arm pool, tier 1 structure checks, a smoke test, the repository's `just test-image` (`bin/test-image.sh`), then a retag of the tested digest. Callers grant `contents: read`, `packages: write` and `id-token: write`. Read [`docs/reusable-workflows/app-image-pipeline.md`](docs/reusable-workflows/app-image-pipeline.md) before changing the job graph or where repository code may run |
| `app-image.yml` | The pipeline's builder, called only by it (amd64 only, private repositories): a `candidate` image or a `promote` of a tested candidate. Callers grant `contents: read`, `packages: write` and `id-token: write` on every calling job. Repository code never runs in the credentialed `build` job: the Dockerfile consumes `/opt/standards` from the base image, not an installed npm bundle. Read [`docs/reusable-workflows/app-image.md`](docs/reusable-workflows/app-image.md) before changing it; its job's `job_workflow_ref` is on the Private CA's allowed list in mac-dev-playbook |
| `codewiki-pages.yml` | Push-to-default deploy of a committed CodeWiki (`docs/codewiki/` minus `temp/`, requires the `index.html` viewer) to Pages; the starter caller runs it when `docs/codewiki/metadata.json` exists. Caller grants `contents: read`, `pages: write`, `id-token: write`; Pages enablement/visibility is `.github-private` Terraform's. See [`docs/reusable-workflows/codewiki-pages.md`](docs/reusable-workflows/codewiki-pages.md) |
| `secret-scan.yml` | Scheduled / dispatched trufflehog full-history scan (`mode: trufflehog`, `--results=verified`); rejects any other mode. The PR-range gitleaks scan runs in the package's `check:pr` |
| `e2e.yml` | Runs `bin/setup.sh` and `bin/e2e.sh` in a CI Image, otherwise `just setup` and `just e2e`; requires executed JUnit cases at `reports/e2e/junit.xml`. Uses `project-route.yml`, including the native macOS route |
| `testflight.yml` | The signed Apple build reusable — see [`docs/reusable-workflows/testflight.md`](docs/reusable-workflows/testflight.md) and [ADR-0002](docs/adr/0002-shared-apple-testflight-release-architecture.md) |
| `bump-brew.yml` | Bumps a `:git`-strategy Homebrew formula in `homebrew-tap` to the **release tag that triggered the caller** — rewrites the top-level source `tag:` + `revision:` and inserts/updates `version` (no tarball/sha256, since `:git` formulae build from source) |

`bootstrap-test-gate.yml`, `copilot-setup-steps.yml` and `workflow-validation.yml` are this repo's own
ops, not reusables: the bootstrap gate runs `tests/` and reports the `test-gate` context;
`copilot-setup-steps` pins Copilot to a hosted runner because this repo is public.

`bump-brew.yml` is invoked from a consumer's **release/tag workflow**, not from `standards.yml` (the Go/Swift CLIs that ship a `:git` formula in the tap call it on release). Push auth: preferred is the **rubio-tap-push App** — callers use `secrets: inherit` and the reusable mints a per-run token (contents:write, scoped to the tap repo) from the `TAP_PUSH_APP_ID`/`TAP_PUSH_APP_PRIVATE_KEY` org secrets. A caller that does not pass them fails fast with an explicit error. **Filename ≠ display name** — the file is `bump-brew.yml` (renamed from `bump-homebrew-git`) but its internal `name:` still reads `bump-homebrew-git (reusable)`; `uses:` the *path* `…/bump-brew.yml@main`.

CI images build the caller's root Dockerfile `ci` target. The central
`repository-image` action handles build, secret scan and publication;
`build-service` owns the shared mTLS transport and client tool installer.

There is intentionally **no `workflow-templates/`** (org "New workflow" picker starters).
Repositories join the onboarded set with `npm init @rubio-enterprises/standards`, which copies
the starter `standards.yml`; the Test Gate is centrally injected.

## Workflow delivery

Reviewed merges to `main` deliver workflow files, reusables and actions.
There is no separate publication request, floating-tag movement or release-please
release for this repository. Recover regressions through reviewed reverts.

## Cross-cutting workflow patterns (easy to break when adding/editing a reusable)

- **Source-repository no-op guard.** Consumer-shaped workflows declare direct triggers so
  GitHub accepts them as required-workflow plumbing, but `.github` itself cannot satisfy those
  consumer contracts. Guard direct `pull_request` and `merge_group` events from this source
  repository explicitly. The Rust workflow skips its workload and lets the bucket report the
  source no-op; consumer direct events and `workflow_call` remain strict.
- **Runner Route selection.** Private org variable `RUNNERS` is a JSON map;
  `org-ops ci mode` owns live values. Each key has an independent hosted
  fallback, so public consumers (which cannot see private org variables)
  remain runnable. Use
  `runs-on: ${{ fromJSON(vars.RUNNERS || '{}').small || fromJSON('["ubuntu-slim"]') }}`
  for small work. Route by actual capability:

  | Key | Self-hosted | Hosted fallback | Work |
  | --- | --- | --- | --- |
  | `small` | `linux-x64-small` | `ubuntu-slim` | small work without Docker |
  | `medium` | `linux-x64-medium` | `ubuntu-latest` | multi-GiB work without Docker |
  | `docker` | `linux-x64-large` | `ubuntu-latest` | x64 Docker/Compose/builds |
  | `image` | `linux-x64-image` | `linux-x64-image` (self-only) | Build Service client jobs (Dev Container Images) |
  | `kvm` | `linux-x64-large` | `ubuntu-latest` | nested x86 virtualization |
  | `arm` | `linux-arm64` | `ubuntu-24.04-arm` | ARM builds, VMs, Playwright |
  | `macos` | `macos-arm64` | `macos-26` | Xcode and signing |

  Project workflows use the shared router. Small/medium jobs outside a CI Image need Docker: generic self-hosted routes use the `docker` route, and `ubuntu-slim` becomes `ubuntu-latest`. Native macOS projects retain `macos` for Xcode.
  `bump-brew` uses `arm` and its Ruby preflight can install
  through passwordless sudo on Tart Ubuntu. Long Xcode builds such as `testflight` use `macos`.
- **Size and platform are DECLARED as custom properties, never derived from files.**
  `standards.yml` routes from the repository's `ci-platform` (`linux`/`macos`) and `ci-size`
  (`small`/`medium`) custom properties (hand-authored in `.github-private` Terraform). The route
  reusable uses event properties or the base repository properties API before checkout. Admitted
  small/medium self-hosted jobs select their CI Image labels. The injected workflows
  opt in with `inspect-image-inputs: true`. For their Linux `linux`/`test`
  pull-request and merge-group calls, image-input changes or an uncertain comparison
  select Docker and build the checkout's Dev Image instead. Before changing detection
  or adding an image-input path, read `docs/reusable-workflows/project-route.md` for
  compare-API limits, rename/deletion handling and the canary. ARM test overrides,
  macOS shapes, public repositories and other callers keep their existing routes.
  The medium route falls back to hosted `ubuntu-latest` (8 GB), never the small fallback.

## Renovate config — two files, two roles

- **`default.json`** is the **org-wide shared preset**. Every consumer's `renovate.json` does
  `extends: ["github>Rubio-Enterprises/.github"]`, which resolves to this file — so editing it
  changes Renovate behavior across onboarded consumers. The shared preset schedules
  ordinary update branches for weekends in `America/Chicago`; package-specific
  rules may have narrower schedules. Security vulnerability alert PRs retain
  Renovate's immediate default. Key rules: the built-in **`mise` manager is on** (runtime pins in a
  repository's `image/mise.toml` are repository-owned; the check-tool pins live in the Standards Package);
  the **`copier` manager is off** (Copier is retired); **`github-actions` manager disabled for the
  starter `.github/workflows/standards.yml`** outside this repository (its action pins advance in the
  package's starter, and a local bump would show as Starter Drift); first-party `Rubio-Enterprises/.github`
  workflow/action dependencies are disabled in every filename, including
  TestFlight callers, with digest pinning disabled. Third-party updates retain
  their existing policy;
  **Terraform core-version bumps disabled** (`terraform` manager, `required_version` depType only —
  providers and modules still update): Renovate raises the constraint in git while the *executing*
  version is the HCP workspace's own `terraform-version` setting, which lives outside git, so a
  merged bump breaks `terraform init` for every run in that workspace until someone raises the
  setting by hand. That went unnoticed for two weeks in `agent-workspaces` and was found only when
  an outage fix could not be applied, so core raises are a paired operator move, not maintenance;
  **automerge** for stable (≥ 1.0.0) minor, patch, pin, digest, and `pinDigest` updates after
  the global seven-day soak; **held draft PRs** for majors and every pre-1.0
  update, never auto-merged. Dependency Dashboards are disabled, with no dashboard
  approval rules. The final package rule applies `draftPR: true`,
  `automerge: false`, `prConcurrentLimit: 1`, and `branchConcurrentLimit: 1` after
  every automerge rule. These limits compare against the shared per-repository
  Renovate backlog, so a held draft opens only when no other Renovate PR is open;
  closing it declines that version. Held drafts keep the weekend schedule. Named
  Python dev tools retain their exact pins and pin-only soak exemption, without a
  major/0.x automerge exception. `go.mod` directive raises and the `jdx/mise` and
  `astral-sh/uv` CLI pins remain human-merge-only.
  Stable and pre-1.0 npm, Cargo, and pin updates use
  distinct groups so a manual member cannot disarm an otherwise-safe stable branch.
  The first two `customManager`s track the
  `# renovate: … jdx/mise` workflow `MISE_VERSION:` cold fallback and
  `# renovate: … astral-sh/uv` workflow `version:` marker, respectively.
  The mise tags are v-prefixed and need `extractVersionTemplate`; uv tags
  are bare semver (`0.12.1`). The third tracks git-sourced entries in
  `home/.chezmoidata/uv-tools.toml`: recursive matching first binds one TOML table to its GitHub
  repo, then replaces only that table's `version` line. A narrow package rule owns
  `pinDigests: true` (the field is not valid inside a custom manager), bootstrapping tag-only
  entries before update classification; the explicit replacement always writes
  `version = "<sha>"  # <tag>`.

  **Mise lock sidecars** (`**/.mise/locks/**`) are generated outputs, not independent
  manager inputs. Their pins still update through `image/mise.toml`, and Renovate
  commits the regenerated sidecars with `image/mise.lock`. Keep mise lock maintenance
  on. `ignorePaths` replaces the inherited list, so preserve the module/test defaults
  alongside the sidecar glob when editing it.

  **Dockerfile `ARG`/`ENV` `…_VERSION` pins** ride the shipped `customManagers:dockerfileVersions`
  preset (in `extends`) rather than a hand-rolled regex, because the built-in `dockerfile` manager
  reads only `FROM` lines.

  The following consumer-driven rules are worth knowing before editing this file:

  - **The `deb` datasource has no default registry.** A `datasource=deb` marker carrying no
    `registryUrl` looks up `null` and the pin silently never advances — verified against renovate
    43.288.0, the major `renovatebot/github-action@v46.1.14` defaults to and therefore what
    `.github-private`'s runner executes. The preset supplies no `deb` registry: a registry's
    `suite=` is **coupled to the consumer's Debian base image**, so a consumer that adds a `deb`
    marker puts a `registryUrl=` field on its own `# renovate:` comment line, which keeps the
    suite beside the `FROM` that determines it.
  - **The stable-version guard is `!/^v?0/`, not `!/^0/`.** Renovate evaluates
    `matchCurrentVersion` against the raw `currentValue`, so the shorter predicate classifies
    `v0.5.5` as stable. The standing safe rule and every stable npm, Cargo, and pin group use
    `!/^v?0/`; their manual pre-1.0 counterparts use the complementary `/^v?0/`.
  - **Stable and pre-1.0 grouping is deliberately disjoint.** npm and Cargo minor/patch updates,
    plus Renovate's general pin-update branch, have distinct current-version predicates, names,
    and slugs. Stable members inherit the standing safe automerge rule and seven-day soak; pre-1.0
    members are explicitly unarmed. The broad grouping rules apply only when no earlier rule has
    already assigned `groupName`, preserving `config:best-practices`' narrower monorepo groups.
    Later local groups (GitHub Actions, tool runtime dependencies and first-party tool pins)
    set both name and slug so their safer coupled-update boundaries remain authoritative.

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
  stable non-major tool runtime dependencies and registry pins). It was off
  because GitHub-native auto-merge waits only on **required** checks while Renovate's own engine
  waits for **all** checks green, and the onboarded consumers had no required checks beyond the audit ruleset.
  `gate-tests` going active (2026-07-30) changed that for the 34 repos carrying
  `gate_tests = true`, which now expose a required `test-gate`. The cost of leaving it off was
  concrete: Renovate merges only *during* a wave, so eight green first-party PRs sat unmerged for
  up to two days and were hand-merged.

  Since the standards v2 flag day (2026-10-31) every onboarded repository requires exactly two
  contexts, the injected `standards` check and central `test-gate`, so native auto-merge waits on
  the same lint/format and test results Renovate's engine would. The 2026-08 `gate-lint-hooks` /
  `gate-e2e` required contexts are retired with their workflows.

  **`rebaseWhen: "conflicted"` is the exact org-wide posture.** Falling behind
  `main` alone does not trigger a rebase, even for automerge PRs. Org rulesets set
  `strict_required_status_checks_policy = false` (`.github-private`'s
  `governance.tf` and `main.tf`), so merging does not require an up-to-date branch.
  In the 2026-10-13–16 wave, 773 of 849 Renovate linux-x64 jobs ran on
  later-superseded commits, costing about eight slot-hours per day. The accepted
  trade-off is that a PR can merge after being tested against an older `main`.
  Keep this manager-agnostic setting at top level. Consumers using untagged `github>Rubio-Enterprises/.github`
  resolve `default.json` from the default branch (`main`), so this policy takes
  effect on their next Renovate run after merge, without a release or
  floating-major tag update.

  **The per-repository admission limits are `commitHourlyLimit: 1`, `prHourlyLimit: 1`,
  `prConcurrentLimit: 5`, and `branchConcurrentLimit: 5`.** The hourly commit limit is the direct
  consumer-CI guard because it counts both new branch pushes and automatic rebases; the PR limit
  separately bounds new PR admission. The concurrent limits bound future ordinary branch/PR
  population but do not close the existing backlog. All four are per repository, not org-wide.
  Explicit force modes and security PRs remain operationally separate, and force is never cleanup.

  **The lockFileMaintenance rule carries `minimumReleaseAge: "0 days"` for the same reason —
  a second, independent starvation.** Under the global 7-day soak Renovate stamps its own
  `renovate/stability-days` *pending* status whenever any refreshed transitive release is
  younger than 7 days; its merge engine waits for ALL checks including that one, and each
  weekly refresh pulls new young releases that re-arm the clock, so the PR stays
  green-but-unmergeable forever (`standards#404`: 8 days, hand-merged 2026-08-03). This does
  NOT generalize to ordinary third-party updates: a *soaking* PR whose clock genuinely runs out
  is working as designed and must not get an age-0 carve-out. The separate first-party source
  and tool-dependency exemptions have a different justification: the org deliberately publishes
  and gates those releases itself, so the third-party supply-chain quarantine does not apply. For
  lockfile maintenance, the distinction is instead whether new content structurally re-arms the
  clock before it can expire.
- **`renovate.json`** is *this repo's own* config and merely `extends` the preset above.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues; external PRs are not a triage request surface.

### Triage labels

Issue states and Gates follow the [organization standard](https://github.com/Rubio-Enterprises/standards/blob/main/docs/agents/triage-labels.md).

### Domain docs

This is a single-context repo: use root `CONTEXT.md` and root `docs/adr/` when they
exist. See `docs/agents/domain.md`.

## Conventions

- **Architecture decisions**: an ADR records why a rule exists. Before changing a decision, check
  `docs/adr/` for an accepted ADR; reversing one needs a superseding ADR. Write one only if a
  person approved it: in the session, or in an approved plan or `spec:agent` issue that
  names the ADR. Otherwise the PR description is the record. Follow
  [the format](docs/adr/0001-record-architecture-decisions.md), which states the ADR test.

Use Conventional Commits titles. There is no release-please, local commitlint or
lefthook in this repo. Default branch is `main`; open PRs as drafts by
default per the global git-workflow rules.
