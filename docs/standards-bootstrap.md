# `standards-bootstrap` action

`actions/standards-bootstrap` provisions a repository's gitignored Shared Config
Directory, `.standards-config/`, before mise loads its shared includes. Standards
stays private; only the trusted provisioner receives its reader credential.

The action:

1. Checks for the reader credentials and mise 2026.10.19 or later.
2. Mints a read-only App token scoped to `Rubio-Enterprises/standards`, resolves
   `main` once to a validated lowercase 40-hex SHA, and uses `actions/checkout`
   to provision that exact commit with full history and no persisted credentials.
3. Removes legacy standards token routes, including on failure. It installs no
   route; unrelated private-module and knowledgebase routes are untouched.
4. Requires a real, clean, non-shallow checkout at the resolved SHA, normalizes
   the credential-free origin to
   `https://github.com/Rubio-Enterprises/standards.git`, and exports the validated
   `STANDARDS_PIN` through `GITHUB_ENV` for later steps in the same job.
5. Installs mise without tools, caching or consumer-environment export from
   `runner.temp`. Even installation-only mise-action invokes `mise ls`, so its
   directory must not load consumer includes before checkout validation.
6. Runs `mise bootstrap repos status --missing --json` **before bootstrap or
   setup**. Its JSON must contain `.standards-config` at the expected pin in
   state `current`; an absent/skipped declaration cannot pass unchecked. The
   shared `config/lefthook/base.yml` must exist too.
7. Runs mise-action's native `bootstrap: true` with `--only repos,tools`,
   including the readiness hook, tool installation and environment export.
   `bootstrap-parts: repos` retains pin export and verification but skips tools,
   tool caching and consumer-environment export.

Bootstrap is unconditional. Every consumer must already carry the final stub:

```toml
[bootstrap.repos]
".standards-config" = { url = "https://github.com/Rubio-Enterprises/standards.git", ref = "{{ env.STANDARDS_PIN | default(value='main') }}" }
```

There is no depth limit. Cold includes use `?ref=main`; a provisioned checkout
avoids them. Older PR branches must incorporate the updated stub before running
this action. An empty, malformed or wrong pin, dirty/shallow/missing checkout or
wrong origin fails before setup. Bootstrap, repeated setup and later hooks use
one pin without standards Git transport; they do not receive its App token.

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
The action declares no outputs. `STANDARDS_PIN` persists for later steps through
`GITHUB_ENV`, including the repos-only path. The reader token is used only for
resolution and checkout, with revocation at job end; it is not put in the
consumer environment or Git config.

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
