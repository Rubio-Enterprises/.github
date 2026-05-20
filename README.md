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

`audit.yml` and `secret-scan.yml` check out `Rubio-Enterprises/standards` at a pinned tag and execute its audit-side content there. The pin is on the **audit stream only** — `audit/v1.X.Y` (or floating `audit/v1`):

- `audit/vX.Y.Z` advances only when files in `standards/scripts/`, `standards/schemas/`, `standards/policy/`, `standards/data/`, or audit-side `.mise.toml` change.
- Template-side changes in `standards/template/` and `standards/copier.yml` advance the **template stream** (`template/vX.Y.Z`) and **do not require a `.github` release**. They reach consumers via `copier update`, not via `.github`.

See [`standards/RELEASES.md`](https://github.com/Rubio-Enterprises/standards/blob/main/RELEASES.md) for the full dual-layer model.

When the audit pin here changes, cut a new `.github` tag (`vX.Y.Z` + advance moving `v1`). Renovate then bumps the consumer-side SHA pin on its next pass.

## scripts/

- `rotate-sops-recipients.sh <OLD> <NEW> [--add|--replace]` — fan-out helper for §6.10 key rotation. Produces N reviewable PRs (N = current sops-adopting repo count) with `sops updatekeys -y` already applied per-repo. v6 reality: N = 0 at Phase-1 publication; grows as repos opt in.

### Common usage patterns

The script is the fleet-wide primitive for the per-host key architecture (Plan 1 header + §8.10 appendix). Two operations:

**Onboarding a new persistent host** (new Mac, new tailnet VM joins the fleet):
```bash
# After running Plan 1 Task 20 Step 4 on the new host and recording its pubkey:
bash rotate-sops-recipients.sh "" "<NEW_HOST_PUB>" --add
```
`--add` is non-destructive — it appends the new pubkey to each blob's recipient list without removing anyone. Use this when a new operator host or prod deploy target joins.

**Decommissioning a host** (Mac dies, VM destroyed, key compromise):
```bash
bash rotate-sops-recipients.sh "<OLD_HOST_PUB>" "" --replace
```
`--replace` substitutes the old pubkey with the new one (empty `NEW` means "remove old without adding anything"). Use this when a host leaves the fleet.

After either of the two, run `copier update --skip-answered --vcs-ref=v1` against the fleet so `.sops.yaml` files re-pin to the updated recipient list (Plan 2 Task 12 established the fleet-wide rollout pattern).

## Release history

- 2026-05-20 — Reconciled `v1` floating tag off its collision with `v1.1.19` (`89a811f`). One-time backfill of the floating-tag floor-advance ritual mandated by [`standards#12`](https://github.com/Rubio-Enterprises/standards/pull/12).
