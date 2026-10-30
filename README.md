# .github

Org community files and shared workflows

Organization GitHub Actions workflows for `Rubio-Enterprises`: the Gate Family
workflows (`standards-gates.yml`, `typecheck-ts.yml`, `lint-hooks.yml`,
`build.yml`) injected by organization rulesets, plus the smaller set of
reusables that consumers thin-call directly. The Build gate's repository
interface is in [`docs/build-gate.md`](./docs/build-gate.md).
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

Consumer repositories adopt standards through Copier (`/onboard-repo`), which
renders their workflow callers. Organization rulesets inject the Gate workflows;
there is no `workflow-templates/` picker installation.

## Usage

Use the shared Renovate preset in a consumer's `renovate.json`:

```json
{
  "extends": ["github>Rubio-Enterprises/.github"]
}
```

The Renovate runner must have access to the consumer repository. Consumers with
bespoke Renovate policy can extend `github>Rubio-Enterprises/.github:copier`
without inheriting the whole preset. See the workflow contracts below before
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
override must maintain its complete local chooser. No Copier release, renderer,
or synchronization job is needed.

Before publishing, prepare compatible issue consumers, ensure the lowercase
native types and `needs-triage` label exist where used, and pilot the complete
chooser in one repository. These files do not provision GitHub metadata or change
existing issues. Required fields are not a universal enforcement boundary,
particularly in private repositories or for API/CLI-created issues.

## Reusable workflows

| Workflow | Purpose |
| --- | --- |
| [`e2e.yml`](./.github/workflows/e2e.yml) | End-to-end harness; runs an apex `scripts.e2e` or repo-owned `mise run e2e`, requires a nonempty JUnit report, and does not start a dev server. `has_swift` suites run on `RUNNERS.macos` |
| [`devcontainer-image.yml`](./.github/workflows/devcontainer-image.yml) | Builds native amd64/arm64 Dev Container images, publishes a private GHCR multi-arch image, and prunes untagged versions; see [its contract](./docs/reusable-workflows/devcontainer-image.md) |
| [`secret-scan.yml`](./.github/workflows/secret-scan.yml) | Scheduled trufflehog full-history deep-scan (the PR-time gitleaks scan runs in `standards-gates.yml`) |
| [`codewiki-pages.yml`](./.github/workflows/codewiki-pages.yml) | Deploys a committed CodeWiki (`docs/codewiki/`, minus `temp/`) to GitHub Pages for repos declaring `has_codewiki`; see [its contract](./docs/reusable-workflows/codewiki-pages.md) |

A repository workflow that runs mise in a full standards consumer
calls the [`standards-bootstrap`](./actions/standards-bootstrap/action.yml)
composite action first. It provisions a full-history Shared Config Directory
at one resolved standards `main` commit, exports `STANDARDS_PIN`, and runs mise
without a standards token route. See [`docs/standards-bootstrap.md`](./docs/standards-bootstrap.md)
for the stub and secrets contract.

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
| --- | --- | --- |
| `small` | `linux-x64-small` | `ubuntu-slim` |
| `medium` | `linux-x64-medium` | `ubuntu-latest` |
| `docker`, `kvm` | `linux-x64-large` | `ubuntu-latest` |
| `arm` | `linux-arm64` | `ubuntu-24.04-arm` |
| `vrt` | `linux-x64-medium` | self-only `linux-x64-medium` |
| `macos` | `macos-arm64` | `macos-26` |

The `e2e.yml` reusable uses `arm`, including the `ubuntu-24.04-arm` fallback
when the private map is unavailable. A caller declaring `has_swift` runs on
`macos` instead (hosted `macos-26` fallback), because an Apple suite needs Xcode
and the iOS Simulator. The map has exactly seven active keys; `light`, `heavy`
and `e2e` are retired, and non-Swift E2E workflows use `arm` instead
([standards ADR-0030](https://github.com/Rubio-Enterprises/standards/blob/main/docs/adr/0030-size-route-keys-and-hosted-fallbacks.md)).

The shared Renovate preset in `default.json` schedules ordinary updates on
weekends in `America/Chicago`; vulnerability alerts remain immediate. Existing
package-specific schedules still apply.

### Workflow delivery

Organization rulesets load Gate workflow files from `main`; Terraform owns
their consuming rulesets and ref. Reusable workflows and actions also follow
`@main`. Reviewed merges take effect without release-please, floating tags or
a separate publication request. Recover a regression with a reviewed revert.

## Standards dependency

The `standards-gates.yml` workflow checks out private `standards@main` for
every applicable consumer; there is no ring selection or Channel environment.
Audit content therefore follows reviewed standards merges without a `.github`
release. Starter inputs still reach consumers through Copier updates.

Internal `.github` workflow/action calls follow `@main`; the shared Renovate
preset disables updates and digest pinning for that exact dependency, including
TestFlight callers. Third-party actions retain SHA pins and normal updates.

The rollout is staged: remaining full consumers convert before unconditional
bootstrap lands; repository-owned callers and injected refs converge next.
Legacy tag refs remain frozen pending the active-entry-point audit; their
writers are retired. The branch bootstrap supplies no `STANDARDS_PIN`; pin
provisioning activates only after the starter wave converges.

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
