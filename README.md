# rubio-dotgithub

Organization GitHub Actions workflows for `Rubio-Enterprises`: seven Gate Family workflows injected by organization rulesets, plus the smaller set of reusables that consumers thin-call directly.

## Reusable workflows

| Workflow | Purpose |
|---|---|
| [`audit.yml`](./.github/workflows/audit.yml) | Runs `Rubio-Enterprises/standards` Layer A/B/C against the calling consumer |
| [`e2e.yml`](./.github/workflows/e2e.yml) | Archetype-aware end-to-end harness (Playwright / pexpect / testscript / assert_cmd / MCP) |
| [`rust-test.yml`](./.github/workflows/rust-test.yml) | Policy-routed canonical Rust tests with fail-closed aggregation |
| [`secret-scan.yml`](./.github/workflows/secret-scan.yml) | PR gitleaks + scheduled trufflehog deep-scan |

### Rust test execution policy

Direct required-workflow events read repository variables
`RUST_TEST_WORKLOAD_CLASS` and `RUST_TEST_TIMEOUT_MINUTES`. Reusable callers must
pass required `workload-class` and `timeout-minutes` inputs. Supported classes are
`glue` and `linux-arm`; timeout must be an integer from 5 through 120 minutes.
The class resolves through the private organization Runner Route when available
and otherwise uses its public hosted fallback (`ubuntu-slim` or
`ubuntu-24.04-arm`). Repository policy never stores physical runner labels.

Invalid policy fails on the hosted slim pre-check before checkout. Valid active
runs always execute `mise run test`, and the aggregate succeeds only when the
workload result is exactly `success`.

### Gate workflow publication

Organization rulesets load Gate workflow files from the lightweight
`gates/wf-v1` tag. This repository owns that tag through a guarded Publication
Request and exact compare-and-swap publisher; Terraform owns only the consuming
rulesets and ref name.

Normal publication is a request-only PR changing
`.github/plumbing-ref/publication-request.json`. After merge, the publisher
validates commit ancestry and the complete Gate Family workflow manifest,
requires the live ref to equal the recorded expected SHA, performs an exact Git
lease update, and verifies the final remote ref. Backward moves use the manual,
validation-first rollback workflow. See
[`docs/plumbing-ref-publication.md`](./docs/plumbing-ref-publication.md).

## Standards dependency

`audit.yml` and `secret-scan.yml` check out `Rubio-Enterprises/standards` and execute its audit-side content there. Each run **resolves the ref at runtime for the calling repository** rather than using a fixed tag, so the workflows track `standards` as it advances:

- Audit-side changes (`standards/scripts/`, `standards/schemas/`, `standards/policy/`, `standards/data/`, or audit-side `.mise.toml`) reach consumers through that resolved ref.
- Template-side changes in `standards/template/` and `standards/copier.yml` reach consumers via `copier update`, not via `.github`, and **do not require a `.github` release**.

See [`standards/RELEASES.md`](https://github.com/Rubio-Enterprises/standards/blob/main/RELEASES.md) for the full model.

Because the ref is resolved per run, new audit-rule **content** reaches consumers as soon as `standards` advances — **no `.github` release needed**. A Gate workflow file change is evaluated at an exact candidate commit and then published through the guarded `gates/wf-v1` path. A thin-called reusable change instead rides a `.github` release (`vX.Y.Z` + release-please moves `v1`), after which Renovate bumps consumer-side SHA pins.

## scripts/

(Empty.) The previous `rotate-sops-recipients.sh` SOPS-recipient rotation helper was removed alongside [`standards#51`](https://github.com/Rubio-Enterprises/standards/pull/51) (the org-wide SOPS walk-back). Zero fleet repos carry encrypted secrets, so the rotation primitive has no callers; the replacement guidance for CI secrets lives in [`standards/docs/secrets.md`](https://github.com/Rubio-Enterprises/standards/blob/main/docs/secrets.md) (GitHub Environment secrets with required reviewers).

## Release history

- 2026-05-21 — `v1.1.20`: `copier-sync.yml` now reads `_rubio_template_version` (introduced by [`standards/template/v1.24.0`](https://github.com/Rubio-Enterprises/standards/releases/tag/template/v1.24.0)) with `_commit` fallback. Backward-compatible — consumers on older standards templates continue working via fallback. Task #15 phase 2.
- 2026-05-20 — Reconciled `v1` floating tag off its collision with `v1.1.19` (`89a811f`). One-time backfill of the floating-tag floor-advance ritual mandated by [`standards#12`](https://github.com/Rubio-Enterprises/standards/pull/12).
