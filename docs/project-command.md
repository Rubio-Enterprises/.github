# Project command transport

`actions/project-command` transports repository-owned Bash steps into a job-scoped Repository
Image on GitHub-hosted Linux x64 runners. It does not choose tests, interpret Lifecycle Tasks,
validate JUnit or replace a Test Gate. Native/self-hosted CI Image commands execute directly.

Use the [project router](reusable-workflows/project-route.md) with
`enable-hosted-image: true`. Existing callers default to false until they adopt this transport.
Keep checkout credentials disabled and grant only `contents: read`, `packages: read` plus any
read scopes the commands already require.

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
  with:
    persist-credentials: false
- name: Prepare the hosted Repository Image
  if: needs.route.outputs.hosted-image == 'true'
  uses: Rubio-Enterprises/.github/actions/project-command@main
  with:
    operation: prepare
    hosted-image: 'true'
    token: ${{ github.token }}
- name: Trust the checkout in an image
  if: needs.route.outputs.ci-image == 'true' || needs.route.outputs.hosted-image == 'true'
  uses: Rubio-Enterprises/.github/actions/project-command@main
  with:
    hosted-image: ${{ needs.route.outputs.hosted-image || 'false' }}
    run: mise trust "$GITHUB_WORKSPACE"
- id: setup
  uses: Rubio-Enterprises/.github/actions/project-command@main
  with:
    hosted-image: ${{ needs.route.outputs.hosted-image || 'false' }}
    run: mise run setup
    forward-env: NODE_AUTH_TOKEN MISE_GITHUB_TOKEN
  env:
    NODE_AUTH_TOKEN: ${{ github.token }}
    MISE_GITHUB_TOKEN: ${{ github.token }}
- name: Repository-owned tests
  uses: Rubio-Enterprises/.github/actions/project-command@main
  with:
    hosted-image: ${{ needs.route.outputs.hosted-image || 'false' }}
    run: mise run test
```

The example omits the caller's native bootstrap, task selection and JUnit assertions. Retain them.
Install uv/mise and restore plain mise data only when neither image output is true. Restore other
host caches only if the container explicitly mounts them; the hosted container does not share
the runner's home. Do not replace image tool directories with a plain-runner cache.

## Action interface

- `operation`: `prepare` or `run` (default).
- `hosted-image`: exact string `true` or `false`. False executes directly without Docker.
  True without a successfully prepared environment fails, never falls through to the host.
- `run`: caller-owned Bash content. It runs with `bash --noprofile --norc -e -o pipefail`.
  Keep the original step ID, condition, command content and failure handling.
- `forward-env`: whitespace-separated extra environment **names**, never secret values.
  Hosted invocations forward only those names plus GitHub ref/event/run and BASE_SHA/HEAD_SHA
  context. Context values remain data, including multiline PR_TITLE. Do not interpolate
  untrusted event text into the script.
- `token`: only hosted prepare needs this repository's existing packages:read GITHUB_TOKEN.
  No PAT, credential scope expansion or package visibility change is required by the action.

Canonical `mise run setup` runs in the actual checkout with setup-local NODE_AUTH_TOKEN,
materializing current `.standards/`. Forward the package token for that invocation only.
Later checks can explicitly forward GH_TOKEN/GITHUB_TOKEN where they already need read access.
Neither Standards Package contents nor an old standards Git checkout/pin come from the image.

GitHub's event file is copied read-only; per-step GITHUB_OUTPUT, GITHUB_ENV, GITHUB_PATH and
GITHUB_STEP_SUMMARY channels are bridged back after execution, even when the command fails.
Other host paths are not automatically translated. In particular, a container path appended to
GITHUB_PATH is not a host executable path. Service-network layouts and bespoke mounts require
separate qualification; capability jobs are not migrated by this action.

## Lifecycle and safety

Prepare checks the actual runner is github-hosted/Linux/X64, installs unmodified CLI 0.89.0,
pulls `ghcr.io/<owner>/<repo>/devcontainer:main` using disposable Docker auth, removes that auth
and freezes its fully qualified `repository@sha256:…` reference for the job. A bare Docker image
ID is not a Dockerfile FROM reference: BuildKit can interpret `sha256:…` as a Docker Hub name
during the CLI's UID-adjustment build. Generated config uses the checkout, not a
PR-supplied Dev Container initializeCommand or arbitrary host mounts. Merged image metadata must
retain vscode, the baseline shim-ownership hook and the declared mounts, without added
privileges or lifecycle commands. Only the shared mise tool/shim directories receive root UID
repair; the CLI migrates home-owned download caches, and project commands remain nonroot.

The checkout bind mount can hide dependencies baked in the image workspace, so setup still
reconciles them. Image-baked tool and download-cache locations remain available. No host home,
Docker socket or blanket runner environment enters the container. Forwarded values are resolved
through symbolic localEnv references for each invocation, not written into action state/config.
They are removed from config after the command; explicit forwarded credentials exist during
that CLI invocation under its ordinary trust model.

A native Actions post hook cleans up after partial prepare, failed commands and ordinary
cancellation. It removes only this preparation's labelled containers, their newly created
volumes/images and job-local state, retaining pre-existing Docker resources. It does not prune
the shared Docker builder cache. Hard VM/process termination ultimately relies on hosted-runner
teardown, not a guaranteed last shell command.

This transport is not a hosted-routing switch and must never be used to nest Docker inside
self-hosted image runners. Docker/KVM/ARM/macOS/VRT exceptions and image publication stay on
their existing execution paths.

## Measurement

Prepare outputs `image` (Docker ID), `reference` (fully qualified immutable digest), `pull-seconds` (login/pull/identity resolution) and `start-seconds`
(metadata/config/CLI startup and UID/lifecycle work after pull). It also reports `cli-seconds`
(pinned CLI install) and `prepare-seconds` (the complete preparation). Record task/setup and complete job/route
queue/runtime separately. Fresh hosted VMs normally make pulls cold; an image manifest or warm
tool cache does not prove a faster or cheaper job. Do not export private Docker tarballs to
Actions caches/artifacts as a workaround.
