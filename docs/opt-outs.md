# Reusable-workflow opt-outs

The reusables in this repo (`audit.yml`, `e2e.yml`, `secret-scan.yml`, `lint-hooks.yml`, `rust-test.yml`) are opinionated and most consumer repos invoke them through the `.github/workflows/standards.yml` rendered from [`Rubio-Enterprises/standards`](https://github.com/Rubio-Enterprises/standards). A handful of repos cannot fit the shape of one or more reusables — for those cases, this doc captures the canonical opt-out pattern so it stays consistent across the fleet.

## When to opt out

Only opt out when the reusable's contract genuinely cannot fit the repo's shape, not because it's mildly inconvenient. The org floor is "every consumer runs these jobs"; deviations need a documented reason in-place so reviewers can spot stale opt-outs on future audits.

## Pattern: comment the job out, leave a note

The canonical wording is the one from `super-productivity/.github/workflows/standards.yml` (lines 59-66 at the time of writing):

```yaml
  # NOTE: standards e2e disabled — super-productivity has its own ci.yml e2e job
  # ("E2E (npm run e2e)") that knows how to spin up the Angular dev server.
  # The reusable e2e.yml from .github times out waiting for config.webServer
  # because it assumes a static webServer.command in playwright.config.ts.
  # Re-enable once the Electron + Angular dev-server contract is documented
  # in the standards e2e workflow.
  # e2e:
  #   uses: Rubio-Enterprises/.github/.github/workflows/e2e.yml@<sha> # v1
```

Required elements:

1. A `# NOTE: <reusable> disabled — <one-sentence reason>` header on the first commented line.
2. A reference to where the equivalent work lives (`<repo's own ci.yml>` job name, an upstream workflow, etc.) so reviewers can verify nothing is silently dropped.
3. A re-enable trigger: what would have to change for the reusable to be reintroduced. "Never" is a valid answer if upstream constraints make it permanent, but say so explicitly.
4. The original `uses:` line preserved (commented out) so Renovate's SHA bumps still surface in PR descriptions when the floating tag advances — opting back in then is a single comment-toggle.

Do NOT delete the block outright. Renovate keys off the `# v1` trailing comment on `uses:` lines to track SHA bumps; deleting the line removes the audit trail of *why* the reusable was opted out of.

## Common opt-out cases

### `e2e.yml` — multi-process dev-server boots

`e2e.yml` assumes Playwright's `config.webServer` self-starts the app under test. Single-process projects (Next.js dev, Vite dev, a static-site preview) fit cleanly. Multi-process projects — Angular + Electron (`ng build` → `ng serve &` → `wait-on` → `playwright test`), a backend + frontend pair, anything that needs `docker compose up` first — must self-host the e2e job in the consumer's `ci.yml`. See [e2e.yml's header comment](../.github/workflows/e2e.yml) for the dev-server contract.

### `secret-scan.yml` (scheduled trufflehog) — repos that don't justify the runtime

`secret-scan.yml` runs in two modes: gitleaks (PR-time, every PR) and trufflehog (weekly cron). The cron path is expensive on large monorepos (full-history trufflehog scan). Repos whose secret-rotation cadence makes weekly full-history trufflehog overkill can opt out of the `trufflehog:` job specifically while keeping `gitleaks:` on every PR.

### `lint-hooks.yml` — repos with non-standard lefthook layouts

`lint-hooks.yml` runs `lefthook run pre-commit --all-files` against the repo, which assumes lefthook is the canonical hook runner (the org standard per `rubio-standards/lefthook.yml.jinja`). Repos that legitimately use a different runner (husky+lint-staged, pre-commit framework) opt out, but should leave a note: the lint coverage matters, so the equivalent work has to live *somewhere* in the consumer's CI.

## When to re-enable

The `# Re-enable once <X>` line in the opt-out comment is load-bearing. When `<X>` lands (typically a `.github` release that extends the reusable's contract), the consumer's bump-PR review should toggle the comment off and the opt-out trail naturally retires.
