# rubio-dotgithub

Org-level reusable GitHub Actions workflows for `Rubio-Enterprises`. Every consumer's `.github/workflows/standards.yml` thin-calls into here.

## Reusable workflows

| Workflow | Purpose |
|---|---|
| [`audit.yml`](./.github/workflows/audit.yml) | Runs `Rubio-Enterprises/standards` Layer A/B/C against the calling consumer |
| [`e2e.yml`](./.github/workflows/e2e.yml) | Archetype-aware end-to-end harness (Playwright / pexpect / testscript / assert_cmd / MCP) |
| [`rust-test.yml`](./.github/workflows/rust-test.yml) | Swatinem cache + nextest JUnit |
| [`secret-scan.yml`](./.github/workflows/secret-scan.yml) | PR gitleaks + scheduled trufflehog deep-scan |

## Standards dependency

`audit.yml` and `secret-scan.yml` check out `Rubio-Enterprises/standards` and execute its audit-side content there. Each run **resolves the ref at runtime for the calling repository** rather than using a fixed tag, so the workflows track `standards` as it advances:

- Audit-side changes (`standards/scripts/`, `standards/schemas/`, `standards/policy/`, `standards/data/`, or audit-side `.mise.toml`) reach consumers through that resolved ref.
- Template-side changes in `standards/template/` and `standards/copier.yml` reach consumers via `copier update`, not via `.github`, and **do not require a `.github` release**.

See [`standards/RELEASES.md`](https://github.com/Rubio-Enterprises/standards/blob/main/RELEASES.md) for the full model.

Because the ref is resolved per run, new audit-rule **content** reaches consumers as soon as `standards` advances — **no `.github` release needed**. A `.github` release (`vX.Y.Z` + release-please moves `v1`) is needed only when a **reusable workflow's own code** changes; Renovate then bumps the consumer-side SHA pin on its next pass.

## scripts/

(Empty.) The previous `rotate-sops-recipients.sh` SOPS-recipient rotation helper was removed alongside [`standards#51`](https://github.com/Rubio-Enterprises/standards/pull/51) (the org-wide SOPS walk-back). Zero fleet repos carry encrypted secrets, so the rotation primitive has no callers; the replacement guidance for CI secrets lives in [`standards/docs/secrets.md`](https://github.com/Rubio-Enterprises/standards/blob/main/docs/secrets.md) (GitHub Environment secrets with required reviewers).

## Release history

- 2026-05-21 — `v1.1.20`: `copier-sync.yml` now reads `_rubio_template_version` (introduced by [`standards/template/v1.24.0`](https://github.com/Rubio-Enterprises/standards/releases/tag/template/v1.24.0)) with `_commit` fallback. Backward-compatible — consumers on older standards templates continue working via fallback. Task #15 phase 2.
- 2026-05-20 — Reconciled `v1` floating tag off its collision with `v1.1.19` (`89a811f`). One-time backfill of the floating-tag floor-advance ritual mandated by [`standards#12`](https://github.com/Rubio-Enterprises/standards/pull/12).
