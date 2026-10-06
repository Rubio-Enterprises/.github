# rubio-dotgithub

Organization GitHub Actions workflows for `Rubio-Enterprises`: two Gate Family workflows (`standards-gates.yml`, `typecheck-ts.yml`) injected by organization rulesets, plus the smaller set of reusables that consumers thin-call directly.

## Reusable workflows

| Workflow | Purpose |
|---|---|
| [`e2e.yml`](./.github/workflows/e2e.yml) | Playwright end-to-end harness; runs an apex `scripts.e2e` or repo-owned `mise run e2e`, requires a nonempty JUnit report, and does not start a dev server |
| [`secret-scan.yml`](./.github/workflows/secret-scan.yml) | Scheduled trufflehog full-history deep-scan (the PR-time gitleaks scan runs in `standards-gates.yml`) |

Canonical non-E2E tests are not centrally executed: each enforcing repository
gates landing through its own `.github/workflows/test-gate.yml` required status
(the Test Gate Contract — standards ADR-0020). The central `test-py.yml` /
`rust-test.yml` gate workflows retired with their rulesets (standards#389).

E2E callers must grant `contents: read` and `checks: write` to their calling
job, and their test runner must produce `reports/e2e/junit.xml`. The reusable
publishes a JUnit GitHub Check on writable-token runs. Fork pull requests and
Dependabot runs retain annotations, the job summary, and artifacts without
attempting a Checks API write. The E2E command's exit status remains the gate.

## Runner routes and dependency updates

Workflows select capabilities from the private org `RUNNERS` JSON map. Each
route has its own hosted fallback for public callers:

| Route | Self-hosted | Hosted fallback |
|---|---|---|
| `light` | `glue-x64` | `ubuntu-slim` |
| `heavy`, `docker`, `kvm` | `linux-x64` | `ubuntu-latest` |
| `arm` | `linux-arm64` | `ubuntu-24.04-arm` |
| `e2e` | `linux-arm64` | `ubuntu-latest` |
| `vrt` | `linux-x64` | self-only `linux-x64` |
| `macos` | `macos-tart` | `macos-15` |

The shared Renovate preset in `default.json` schedules ordinary updates on
weekends in `America/Chicago`; vulnerability alerts remain immediate. Existing
package-specific schedules still apply.

### Gate workflow publication

Organization rulesets load Gate workflow files from the lightweight
`gates/wf-v1` tag. This repository owns that tag through a guarded Publication
Request and exact compare-and-swap publisher; Terraform owns only the consuming
rulesets and ref name.

Normal publication is a request-only PR changing
`.github/plumbing-ref/publication-request.json`. After merge, the publisher
validates commit ancestry and the complete Gate Family workflow manifest,
requires the live ref to equal the recorded expected SHA, performs an exact Git
lease update, and verifies the final remote ref. There is no automated rollback
entrypoint; backward movement is reserved for last-resort direct owner recovery.
See
[`docs/plumbing-ref-publication.md`](./docs/plumbing-ref-publication.md).

## Standards dependency

`standards-gates.yml` checks out `Rubio-Enterprises/standards` and runs its `ci/gate-*.sh` checks (audit, lint-format, PR title, gitleaks) there. Each run **resolves the ref at runtime for the calling repository** rather than using a fixed tag, so the gate tracks `standards` as it advances:

- Audit-side changes (`standards/scripts/`, `standards/schemas/`, `standards/policy/`, `standards/data/`, or audit-side `.mise.toml`) reach consumers through that resolved ref.
- Template-side changes in `standards/template/` and `standards/copier.yml` reach consumers via `copier update`, not via `.github`, and **do not require a `.github` release**.

See [`standards/RELEASES.md`](https://github.com/Rubio-Enterprises/standards/blob/main/RELEASES.md) for the full model.

Because the ref is resolved per run, new audit-rule **content** reaches consumers as soon as `standards` advances — **no `.github` release needed**. A Gate workflow file change is evaluated at an exact candidate commit and then published through the guarded `gates/wf-v1` path. A thin-called reusable change instead rides a `.github` release (`vX.Y.Z` + release-please moves `v1`), after which Renovate bumps consumer-side SHA pins.

## scripts/

(Empty.) The previous `rotate-sops-recipients.sh` SOPS-recipient rotation helper was removed alongside [`standards#51`](https://github.com/Rubio-Enterprises/standards/pull/51) (the org-wide SOPS walk-back). No Org set repositories carry encrypted secrets, so the rotation primitive has no callers; the replacement guidance for CI secrets lives in [`standards/docs/secrets.md`](https://github.com/Rubio-Enterprises/standards/blob/main/docs/secrets.md) (GitHub Environment secrets with required reviewers).

## Release history

- 2026-05-21 — `v1.1.20`: `copier-sync.yml` now reads `_rubio_template_version` (introduced by [`standards/template/v1.24.0`](https://github.com/Rubio-Enterprises/standards/releases/tag/template/v1.24.0)) with `_commit` fallback. Backward-compatible — consumers on older standards templates continue working via fallback. Task #15 phase 2.
- 2026-05-20 — Reconciled `v1` floating tag off its collision with `v1.1.19` (`89a811f`). One-time backfill of the floating-tag floor-advance ritual mandated by [`standards#12`](https://github.com/Rubio-Enterprises/standards/pull/12).
