# `standards-bootstrap` action

`actions/standards-bootstrap` is a composite action that sets up mise in a
repository using the Shared Config Checkout (standards ADR-0033). Such a
repository's `.mise.toml` includes shared pins from a gitignored Shared Config
Checkout at `.standards-config/`, a clone of the private `standards` repository.
No mise command works in CI until that checkout exists, so every job that runs
mise in a native repository must call this action first.

The action:

1. Checks for the standards-reader credentials and a mise version of at least
   2026.10.19.
2. Mints a read-only standards-reader token scoped to `standards`, and routes
   `https://github.com/<owner>/standards` through it with Git's `insteadOf`.
3. Runs `jdx/mise-action` with `bootstrap: true`. This runs `mise bootstrap
   --only repos,tools` in place of `mise install`: it clones the checkout at
   the ref declared in the consumer's `.mise.toml`, runs its `post-repos` hook,
   installs its tools, and exports mise's environment. With
   `bootstrap-parts: repos`, it provisions only the checkout and runs the
   readiness hook, without tool installation, caching or environment export.
4. Fails unless `mise bootstrap repos status --missing` passes and
   `.standards-config/config/lefthook/base.yml` exists. It records the
   checkout's ref and commit in the step summary.
5. Removes its token route even when an earlier step failed, unless
   `keep-route: "true"` is requested.

Bootstrap is unconditional: no classification answer selects a different path.
This is the branch-following bootstrap; it neither exports nor supplies
`STANDARDS_PIN`. Existing stable/canary declarations and main declarations
continue to follow their declared branches until the separate pin provisioner
is activated after fleet convergence.

The central workflows use it: the `lint-hooks`, `typecheck-ts` and `build` gates
and the `e2e`, `cloud-setup-smoke` and `devcontainer-image` reusables.

## Calling it from a repository workflow

```yaml
permissions:
  contents: read
jobs:
  test:
    runs-on: >-
      ${{ fromJSON(vars.RUNNERS || '{}').small ||
      fromJSON('["ubuntu-slim"]') }}
    steps:
      - uses: actions/checkout@<sha> # v7
        with:
          persist-credentials: false
      # mise routes pipx installs through uv (see AGENTS.md).
      - uses: astral-sh/setup-uv@<sha> # v10.1.0
        with:
          version: "0.12.17"
      - uses: Rubio-Enterprises/.github/actions/standards-bootstrap@main
        with:
          app-id: ${{ secrets.STANDARDS_READER_APP_ID }}
          private-key: ${{ secrets.STANDARDS_READER_APP_PRIVATE_KEY }}
      - run: mise run test
```

- **Ref.** First-party `.github` workflows and actions follow `@main` directly.
  The shared Renovate preset disables updates and digest pinning for the exact
  `Rubio-Enterprises/.github` dependency in every filename. Third-party actions
  remain SHA-pinned.
- **Secrets.** Pass the `STANDARDS_READER_APP_ID` and
  `STANDARDS_READER_APP_PRIVATE_KEY` organization secrets as inputs. A composite
  action cannot read `secrets` itself. A native repository that omits them fails
  with an error naming both secrets.
- **Replace the mise step.** In a native repository the action installs mise.
  Do not also run `jdx/mise-action` with its default `install: true`. That
  action loads the repository's config, so it fails before the checkout exists.
- **Reusable workflows.** A called workflow sees organization secrets only when
  its caller forwards them, with `secrets: inherit` or by name. Declare both as
  optional `workflow_call` secrets, as `e2e.yml` does.

## Inputs

- `app-id`: pass `secrets.STANDARDS_READER_APP_ID`.
- `private-key`: pass `secrets.STANDARDS_READER_APP_PRIVATE_KEY`.
  Both credentials are required.
- `mise-version`: 2026.10.19 or later. The default is the runner's installed
  mise, or 2026.10.19 when none is installed, so the action never
  self-updates a warm runner's mise; on a runner whose mise is older, it fails.
  Pass a version to have mise-action install or update to it.
- `cache-key-prefix`: mise-action's `cache_key_prefix`. Default `mise-v1`.
- `bootstrap-parts`: `repos,tools` (default) or `repos`. The latter provisions
  and verifies only the checkout for jobs that install tools elsewhere, such as
  [Dev Container image builds](reusable-workflows/devcontainer-image.md). It
  skips mise-action's tool cache and environment export; no preceding setup-uv
  step is needed because no pipx tools are installed. Unknown values fail before
  token minting.
- `keep-route`: `true` keeps the token route for the rest of the job. Default
  `false`.

The action declares no outputs.

Leave `keep-route` at `false` unless a later step must reach `standards` again.
`cloud-setup-smoke` keeps the route because the cloud setup chain it runs may
refresh the checkout. `lint-hooks` also keeps it for its native path, as it did
before the action existed. Either way the token stays read-only, scoped to
`standards`, and expires within an hour. Its revocation runs when the job ends.

## Tool-cache Python is not the consumer runtime

Before bootstrap, the central workflows fingerprint a uv-managed interpreter
into the mise cache key to protect cached pipx/uvx tools from stale shebangs.
This is a toolchain seed, not the Python used for consumer tests or linting.
[uv tool environments ignore consumer project Python requests](https://docs.astral.sh/uv/concepts/tools/#python-versions).

The fingerprint step sets `UV_NO_PROJECT=true`
so `uv python find` also ignores consumer `requires-python`. A native checkout
pinned to Python 3.13 with `requires-python <3.14` can therefore fingerprint a
3.14 tool interpreter before mise loads its runtime pin. The setting is scoped
to that step; bootstrap and later consumer commands retain their own Python
selection.

## Secret reachability

Checked read-only on 2026-10-25:

- `GET /repos/Rubio-Enterprises/<repo>/actions/organization-secrets` lists both
  `STANDARDS_READER_APP_ID` and `STANDARDS_READER_APP_PRIVATE_KEY` for all 100
  non-archived organization repositories, the 12 public ones included.
- The injected `lint-hooks` gate already mints from these secrets in every
  repository that carries the `gate-lint-hooks` property.

GitHub withholds secrets in these cases, and a native job then fails at the
credentials check:

- a `pull_request` from a fork, including a contributor's personal fork;
- runs triggered by Dependabot;
- a reusable workflow whose caller does not forward the secrets.

Renovate branches live in the repository itself, so their runs receive the
secrets. Organization secret access is set in GitHub's settings, not in
`.github-private` Terraform. If it is narrowed, native repositories that lose
access fail closed.
