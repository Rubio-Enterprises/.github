# .github

Org community files and shared workflows

Organization GitHub Actions workflows for `Rubio-Enterprises`: one injected
workflow, [`standards.yml`](./.github/workflows/standards.yml) (the Standards
Check; see [`docs/standards-check.md`](./docs/standards-check.md)), which the
`standards` organization ruleset runs on every repository with
`standards-onboarded = true`, plus the reusables that repositories call directly.
The Copier-era Gate Family workflows (`standards-gates.yml`, `typecheck-ts.yml`,
`lint-hooks.yml`, `build.yml`) and `cloud-setup-smoke.yml` were retired on the
standards v2 flag day.
In this workspace, the repository is checked out as `Governance/dot-github`;
its GitHub repository name remains `.github`.

## Table of Contents

- [Install](#install)
- [Usage](#usage)
- [Shared issue forms](#shared-issue-forms)
- [Reusable workflows](#reusable-workflows)
- [Runner routes and dependency updates](#runner-routes-and-dependency-updates)
- [Standards dependency](#standards-dependency)
- [scripts/](#scripts)
- [Release history](#release-history)
- [Contributing](#contributing)
- [License](#license)

## Install

This repository distributes workflow files and Renovate presets, not an
installable application. Clone it to inspect or maintain the shared files:

```bash
git clone https://github.com/Rubio-Enterprises/.github.git dot-github
cd dot-github
```

Repositories adopt standards with `npm init @rubio-enterprises/standards`, which
copies the starter `standards.yml` caller and `test-gate.yml`. The organization
ruleset injects the Standards Check; there is no `workflow-templates/` picker
installation.

## Usage

Use the shared Renovate preset in a consumer's `renovate.json`:

```json
{
  "extends": ["github>Rubio-Enterprises/.github"]
}
```

The Renovate runner must have access to the consumer repository. The preset
updates repository-owned runtime pins in `mise.toml` (the `mise` manager) and
disables the retired `copier` manager. See the workflow contracts below before
adding a reusable caller.

The shared preset extends the native Dev Container manager to read
`.github/.devcontainer/devcontainer.json` as well as its default runtime
locations. Build-definition feature versions remain managed. Docker-datasource
images matching `ghcr.io/rubio-enterprises/<repo>/devcontainer` are excluded
from both updates and digest pinning: consumers intentionally use private,
floating `:main` prebuilds. Other images and Dev Container features keep their
existing policy. The runtime lockfile-refresh task remains scoped to
`.devcontainer/devcontainer.json`; it does not refresh build-definition locks.

## Shared issue forms

The canonical organization defaults live in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/):

| Form | Use for | Native issue type |
| --- | --- | --- |
| [Bug](.github/ISSUE_TEMPLATE/bug.yml) | Broken behavior or a regression | `bug` |
| [Enhancement](.github/ISSUE_TEMPLATE/enhancement.yml) | New or improved behavior | `enhancement` |
| [Refactoring](.github/ISSUE_TEMPLATE/refactoring.yml) | Structural code changes that preserve behavior | `task` |
| [Task](.github/ISSUE_TEMPLATE/task.yml) | Documentation, maintenance, infrastructure, or other concrete work | `task` |

Every form starts with only `needs-triage`; kind labels and Stage fields are not
assigned. Proposal forms require Outcome, Problem / motivation, In scope, Out of
scope, Done when, Verification, and Alternatives considered; Context links are
optional. Bug intake is diagnostic: a complete reproduction is optional at
filing, but needed before unattended implementation. Examples are placeholders,
not prefilled answers.

Repositories without local issue templates inherit the complete set from this
public `.github` repository's default branch. Any local issue template or
`ISSUE_TEMPLATE/config.yml` overrides **the entire inherited set**, not just one
form. Review all local template locations (root, `.github/`, and `docs/`) and
remove redundant overrides through reviewed changes; a repository retaining an
override must maintain its complete local chooser. No release, renderer or
synchronization job is needed.

Before publishing, prepare compatible issue consumers, ensure the lowercase
native types and `needs-triage` label exist where used, and pilot the complete
chooser in one repository. These files do not provision GitHub metadata or change
existing issues. Required fields are not a universal enforcement boundary,
particularly in private repositories or for API/CLI-created issues.

## Reusable workflows

| Workflow | Purpose |
| --- | --- |
| [`e2e.yml`](./.github/workflows/e2e.yml) | End-to-end harness for a suite that needs its own job; runs `mise run setup`, then an apex `scripts.e2e` or repo-owned `mise run e2e`, requires a nonempty JUnit report, and does not start a dev server. `ci-platform = macos` callers run on `RUNNERS.macos` |
| [`devcontainer-image.yml`](./.github/workflows/devcontainer-image.yml) | Builds native amd64/arm64 Dev Container images, publishes a private GHCR multi-arch image, and prunes untagged versions; see [its contract](./docs/reusable-workflows/devcontainer-image.md) |
| [`secret-scan.yml`](./.github/workflows/secret-scan.yml) | Scheduled trufflehog full-history deep-scan (the PR-range gitleaks scan runs in the package's `check:pr`) |
| [`standards.yml`](./.github/workflows/standards.yml) | Injected, not called: the Standards Check. Installs the repository's Standards Release and runs `mise run check` and `mise run check:pr`; routed by the `ci-platform`/`ci-size` properties; see [its contract](./docs/standards-check.md) |
| [`release-please.yml`](./.github/workflows/release-please.yml) | release-please with the `rubio-release-please` App token, a root `uv.lock` refresh and auto-merge on the release PR; called by the starter `standards.yml` when `release-please-config.json` exists; see [its contract](./docs/reusable-workflows/release-please.md) |
| [`codewiki-pages.yml`](./.github/workflows/codewiki-pages.yml) | Deploys a committed CodeWiki (`docs/codewiki/`, minus `temp/`) to GitHub Pages when the starter caller finds `docs/codewiki/metadata.json`; see [its contract](./docs/reusable-workflows/codewiki-pages.md) |

A repository workflow that needs the package's tools runs `jdx/mise-action` and
`mise run setup` with `NODE_AUTH_TOKEN` and `packages: read`. The
[`standards-bootstrap`](./actions/standards-bootstrap/action.yml) composite
action is legacy: only the image builders still use it, for a repository that
has not migrated off the Copier-era Shared Config Directory (see
[`docs/standards-bootstrap.md`](./docs/standards-bootstrap.md)).

Tests are not centrally executed: each onboarded repository gates landing
through its own starter `.github/workflows/test-gate.yml` required status (the
Test Gate).

E2E callers must grant `contents: read`, `checks: write` and `packages: read` to their calling
job, and their test runner must produce `reports/e2e/junit.xml`. The reusable
publishes a JUnit GitHub Check on writable-token runs. Fork pull requests and
Dependabot runs retain annotations, the job summary, and artifacts without
attempting a Checks API write. The E2E command's exit status remains the gate.

## Runner routes and dependency updates

Workflows select capabilities from the private org `RUNNERS` JSON map. Each
route has its own hosted fallback for public callers:

| Route | Self-hosted | Hosted fallback |
| --- | --- | --- |
| `small` | `linux-x64-small` | `ubuntu-slim` |
| `medium` | `linux-x64-medium` | `ubuntu-latest` |
| `docker`, `kvm` | `linux-x64-large` | `ubuntu-latest` |
| `arm` | `linux-arm64` | `ubuntu-24.04-arm` |
| `vrt` | `linux-x64-medium` | self-only `linux-x64-medium` |
| `macos` | `macos-arm64` | `macos-26` |

The `e2e.yml` reusable uses `arm`, including the `ubuntu-24.04-arm` fallback
when the private map is unavailable. A caller whose `ci-platform` property is
`macos` runs on `macos` instead (hosted `macos-26` fallback), because an Apple suite needs Xcode
and the iOS Simulator. The map has exactly seven active keys; `light`, `heavy`
and `e2e` are retired, and non-macOS E2E workflows use `arm` instead
([standards ADR-0030](https://github.com/Rubio-Enterprises/standards/blob/main/docs/adr/0030-size-route-keys-and-hosted-fallbacks.md)).

The shared Renovate preset in `default.json` schedules ordinary updates on
weekends in `America/Chicago`; vulnerability alerts remain immediate. Existing
package-specific schedules still apply.

### Workflow delivery

The organization ruleset loads `standards.yml` from `main`; Terraform owns
the ruleset and ref. Reusable workflows and actions also follow
`@main`. Reviewed merges take effect without release-please, floating tags or
a separate publication request. Recover a regression with a reviewed revert.

## Standards dependency

The Standards Check installs `@rubio-enterprises/standards` (internal, GitHub
Packages) at the repository's Standards Release: `latest`, or the Release Pin
in its `mise.toml`. Check content therefore follows standards releases without
a `.github` change.

Internal `.github` workflow/action calls follow `@main`; the shared Renovate
preset disables updates and digest pinning for that exact dependency, including
TestFlight callers. Third-party actions retain SHA pins and normal updates.

## scripts/

There is no root `scripts/` directory. The previous
`rotate-sops-recipients.sh` SOPS-recipient rotation helper was removed alongside
[`standards#51`](https://github.com/Rubio-Enterprises/standards/pull/51)
(the org-wide SOPS walk-back). Replacement guidance for CI secrets lives in
[`standards/docs/secrets.md`](https://github.com/Rubio-Enterprises/standards/blob/main/docs/secrets.md)
(GitHub Environment secrets with required reviewers). Workflow implementation
helpers still live under `.github/scripts/`.

## Release history

- 2026-05-21 — `v1.1.20`: `copier-sync.yml` began reading
  `_rubio_template_version` (introduced by
  [`standards/template/v1.24.0`](https://github.com/Rubio-Enterprises/standards/releases/tag/template/v1.24.0))
  with `_commit` fallback. Backward-compatible for older standards templates.
  Task #15 phase 2. That workflow has since been retired.
- 2026-05-20 — Reconciled `v1` floating tag off its collision with `v1.1.19`
  (`89a811f`). One-time backfill of the floating-tag floor-advance ritual
  mandated by
  [`standards#12`](https://github.com/Rubio-Enterprises/standards/pull/12).

## Contributing

Organization maintainers should raise questions in this repository's GitHub
issues and submit changes through pull requests following
[.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md).
Use a Conventional Commits title, link the work item, and fill in What?, Why?,
How? and Testing? (with feedback, dependencies and screenshots when relevant).
Keep the body within 400 words. See also [CONTRIBUTING.md](CONTRIBUTING.md),
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [SECURITY.md](SECURITY.md).

This repository has no mise tasks or local lefthook configuration; the consumer
setup commands in `CONTRIBUTING.md` do not apply here. With actionlint and Python
installed, check workflow syntax and the standard-library contract tests:

```bash
actionlint
python3 -m unittest discover -s tests
```

Issue-form YAML/structure checks also use an existing `yq` v4 executable (no new
Python dependencies); those checks skip when it is unavailable. To run only the
issue-form tests:

```bash
python3 -m unittest discover -s tests -p 'test_issue_forms.py' -v
```

These checks validate source paths and form structure, not GitHub rendering or
submission behavior.

CI runs workflow validation and the repository-local `test-gate`.
All workflow and action changes ship on merge to `main`; review their consumer
impact before landing.

## License

[MIT](LICENSE) © 2026 Rubio Enterprises.
