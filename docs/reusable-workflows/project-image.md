# Project commands in the Repository Image

The shared lint-hooks, TypeScript typecheck and BD-01 build workflows support a
central, declared image rollout. Required job names and contexts are unchanged.
CT-01/Docker, KVM, ARM, macOS, image builders and repository-agnostic workflows
keep their existing execution paths. Shared e2e is ARM/macOS and is unchanged;
canonical tests remain repository-owned (`test-gate`).

## Rollout contract

`CI_IMAGE_REPOS` is an organization Actions configuration variable:

- absent, empty or `[]`: unchanged execution;
- a JSON array of positive decimal repository IDs, as numbers or strings:
  image execution for those repositories;
- literal `all`: image execution for every supported repository job.

Unlisted repositories retain their original runner choices, commands, dependency
and cache inputs, authentication routes and conditions. The additive exception
is job-local `packages: read` alongside `contents: read` for injected-only lint
and BD-01 jobs. Callable typecheck stays `contents: read`: existing cache-seed
callers grant only contents read, and a reusable cannot elevate that grant even
when image steps are skipped. Hosted typecheck image pulls remain held until a
coordinated caller/callee permission migration. GitHub does not permit conditional
permission scopes. No package writes are added.
Image steps are skipped until the central switch selects the repository. There
is no registry probing, readiness inference or per-repository opt-in file.

The variable's existence, **all-repository visibility** and empty initial value
are declared in `.github-private` Terraform. Change its value only through a
reviewed Terraform change and the parent-owned rollout checkpoint. The value
contains opaque IDs, never private repository names. Do not enable a repository
until both image packages, own-token access, template/canonical-test integration
and both CI Image scale sets are qualified. GitHub's listener cardinality and
shared admission budgets remain separate rollout gates.

[GitHub documents](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-variables)
all/private/selected repository access policies and an empty string for an unset
variable. Public repositories need `all`; the private `RUNNERS` map remains private.
[Reusable workflows use the caller's variables](https://docs.github.com/en/actions/reference/variables-reference),
not the shared workflow repository's variables. Repository variables can shadow
organization values: this rollout selector is not an authorization boundary.

[Pull requests from forks](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflows-in-forked-repositories)
receive no ordinary Actions secrets and a restricted `GITHUB_TOKEN`; this change
adds neither a privileged trigger nor a substitute standards credential. GitHub's
variable documentation does not explicitly guarantee organization-variable
inheritance for every fork workflow context. Missing variables retain the legacy
path, and missing standards credentials or image access fail closed. An actual
fork/injected-workflow observation is still a rollout qualification, not established
by offline tests. Workflows running in a separate fork repository use that
repository's own context, not the upstream organization's membership list.

## Routing

The existing Workload Class selects small/medium; legacy light/heavy/glue aliases
retain their sizes. Build remains medium. Capability routes win:

| Existing project route | Selected image route |
| --- | --- |
| `linux-x64-small` / `linux-x64-medium`, optionally with their standard self-hosted labels | `ci-<repository ID>-<normalized slug40>-small|medium` |
| Hosted x64 Ubuntu or missing `RUNNERS` key | `ubuntu-latest`, with the Dev Container CLI |
| Docker/KVM `linux-x64`, ARM, macOS | Unchanged, no image CLI |
| Other self-hosted labels | Explicit route-contract error |

Slug normalization matches the inventory: lowercase repository basename, replace
characters outside `[a-z0-9-]` with hyphens, trim boundary hyphens, then truncate
to 40 characters. A missing self-hosted image/scale set never causes a hosted
reroute. Public fallback needs full Docker-capable Ubuntu, not `ubuntu-slim`.

## Execution and credentials

The shared `actions/devcontainer-run@main` action owns its scripts through
`GITHUB_ACTION_PATH`, never a lookalike consumer action. Both paths first use
`standards-bootstrap` with `bootstrap-parts: repos` to provision a clean,
credential-free full-history checkout and an independently resolved pin. The
runtime rechecks origin, SHA, history, clean tree, required file and native
`mise bootstrap repos status --missing --json` readiness. No container fetches
standards or derives the pin from its own checkout.

Direct execution requires a non-root Linux x64 self-hosted CI Image runner with
runner payload, writable system tools/cache and `UV_LINK_MODE=copy`. It runs
`mise install`, `mise run setup`, then the task directly. No Docker login or
Dev Container CLI runs on this path. The pool host owns its CI Image pull.

Hosted execution uses the repository's own `GITHUB_TOKEN` to pull only
`ghcr.io/<lowercase owner>/<lowercase repo>/devcontainer:main`. A job-local Docker
config is deleted, and the pull-token environment variable removed, before any
lifecycle. The pulled image ID fixes this invocation even if the moving tag changes.
CLI **0.89.0** reads a centrally generated image-only config, starts with all
lifecycle disabled, executes the inner pin preflight, runs lifecycle/setup, then
executes the task. No repository Dockerfile/Compose/build or `initializeCommand`
is loaded. Extra host mounts, elevated privileges and non-baseline lifecycle
metadata are rejected before startup. Feature-specific lifecycle behavior beyond
the baseline helper must be qualified for direct execution before rollout; the
hosted adapter does not silently grant Docker/KVM capabilities.

The checkout, including provisioned standards, is bind-mounted into
`/workspaces/<repo>`. CLI maps `vscode` to the hosted user's UID; the adapter
re-owns only the image's tool/cache/shim directories, not the checkout, before
non-root preflight/lifecycle execution. An empty read-only Git config prevents
host URL routes or credentials from crossing into the container. Registry
credentials and standards-reader tokens are not forwarded. Only named CI/task environment values
are forwarded. No Docker socket, service containers or host ports are requested;
these shared tasks need none. Jobs needing host services/Docker remain on their
capability routes rather than importing laptop runtime mounts into CI.

Task scripts retain fork changed-file linting, knowledgebase checks, tracked-file
rewrite rejection, frozen JS dependency installation, defensive absent-typecheck
behavior and fail-closed missing build tasks. The image's canonical setup
reconciles current tool/dependency pins before execution. Legacy Actions caches
remain untouched for opt-out jobs; images supply the opt-in warm environment.
Failures propagate. EXIT/INT/TERM cleanup removes only containers carrying this
invocation's unique label plus its temporary auth/config/scripts, never shared
images or volumes. A hard-killed hosted VM remains the platform's cleanup boundary.

## Evidence boundary

Offline tests cover opt-out step-body/input/condition fingerprints, unchanged
exception workflows, route/identity sizes, bad pins/origins/shallow/dirty checkouts,
missing images, inner preflight ordering, UID/cache preparation, exit propagation,
metadata rejection and cancellation cleanup. Orchestration tests fake Docker/CLI;
they do not establish a native B1 image pass. A real pinned-CLI read probe confirms
configuration/metadata syntax. Actual hosted/direct B1 runs, fork visibility,
private-package access and Feature-specific lifecycle parity remain parent-gated
qualification. No publication or dispatch is implied by this source merge.
