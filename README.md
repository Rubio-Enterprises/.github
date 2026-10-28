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

## Reusable workflows

| Workflow | Purpose |
| --- | --- |
| [`e2e.yml`](./.github/workflows/e2e.yml) | End-to-end harness; runs an apex `scripts.e2e` or repo-owned `mise run e2e`, requires a nonempty JUnit report, and does not start a dev server. `has_swift` suites run on `RUNNERS.macos` |
| [`devcontainer-image.yml`](./.github/workflows/devcontainer-image.yml) | Builds native amd64/arm64 Dev Container images, publishes a private GHCR multi-arch image, and prunes untagged versions; see [its contract](./docs/reusable-workflows/devcontainer-image.md) |
| [`secret-scan.yml`](./.github/workflows/secret-scan.yml) | Scheduled trufflehog full-history deep-scan (the PR-time gitleaks scan runs in `standards-gates.yml`) |
| [`codewiki-pages.yml`](./.github/workflows/codewiki-pages.yml) | Deploys a committed CodeWiki (`docs/codewiki/`, minus `temp/`) to GitHub Pages for repos declaring `has_codewiki`; see [its contract](./docs/reusable-workflows/codewiki-pages.md) |

A repository workflow that runs mise in a full standards consumer
calls the [`standards-bootstrap`](./actions/standards-bootstrap/action.yml)
composite action first. It clones the Shared Config Checkout with the
standards-reader token, then removes the token route. The ref to use and the
secrets to pass are in [`docs/standards-bootstrap.md`](./docs/standards-bootstrap.md).

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
| `light` | `glue-x64` | `ubuntu-slim` |
| `heavy`, `docker`, `kvm` | `linux-x64` | `ubuntu-latest` |
| `arm` | `linux-arm64` | `ubuntu-24.04-arm` |
| `e2e` | `linux-arm64` | `ubuntu-latest` |
| `vrt` | `linux-x64` | self-only `linux-x64` |
| `macos` | `macos-tart` | `macos-15` |

The `e2e.yml` reusable uses `arm`, including the `ubuntu-24.04-arm` fallback
when the private map is unavailable. A caller declaring `has_swift` runs on
`macos` instead (hosted `macos-26` fallback), because an Apple suite needs Xcode
and the iOS Simulator. The legacy `e2e` key remains in the live
map only during retirement; new workflows should use `arm` instead
([standards ADR-0030](https://github.com/Rubio-Enterprises/standards/blob/main/docs/adr/0030-size-route-keys-and-hosted-fallbacks.md)).

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

The `standards-gates.yml` workflow checks out private `standards@main` for
every applicable consumer; there is no ring selection or Channel environment.
Audit content therefore follows reviewed standards merges without a `.github`
release. Starter inputs still reach consumers through Copier updates.

Internal `.github` workflow/action calls follow `@main`; the shared Renovate
preset disables updates and digest pinning for that exact dependency, including
TestFlight callers. Third-party actions retain SHA pins and normal updates.

The rollout is staged: remaining full consumers convert before unconditional
bootstrap lands; repository-owned callers and injected refs converge next.
Existing publication tags and release workflows remain until their separately
reviewed retirement. The branch bootstrap supplies no `STANDARDS_PIN`; pin
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

CI runs workflow validation and the repository-local `test-gate`.
Changes to Gate files require the candidate validation and publication process
in [the publication runbook](docs/plumbing-ref-publication.md); changes to
thin-called reusables use the release stream described above.

## License

[MIT](LICENSE) © 2026 Rubio Enterprises.
