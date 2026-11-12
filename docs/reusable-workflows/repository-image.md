# CI Repository Image builder

`repository-image.yml` builds the caller's Dockerfile `ci` target on the native
amd64 Build Service. Public repositories run literal `just` recipes and publish
no runner image. Dev images are built locally, never published.

## Workflow interface

Caller grants `contents: read`, `packages: write`, `id-token: write`.
Inputs: `file` (default `Dockerfile`), `target` (must be `ci`), `context` (default `.`),
`image-name` (default `ghcr.io/<owner>/<repo>/devcontainer-ci`). Off-default builds
must use a scratch name inside the caller repository's package namespace.
The retired `config`, `base`, `base-ci` inputs are not accepted. No workflow outputs.

Builds run on default-branch push, schedule or dispatch. There is no commit-tag
skip: a base update must rebuild an unchanged repository. Caller `standards.yml`
must invoke this reusable on `workflow_dispatch`, not just pushes.

## Default-branch verification

After `build` succeeds, the `verify` job calls `default-branch-check.yml` with only
`contents: read` and `packages: read`, and no inherited secrets. It does not wait
for `prune`. Its caller guard requires a push or workflow dispatch on the actual
default branch, excludes the non-onboarded `.github` source repository, and requires
an empty `image-name` input. Scheduled and scratch-name builds do not verify.
A failed publication cannot produce passing verification.

The shared router selects image or fallback execution. Admitted image jobs start
after publication so the runner can pull the newly published `devcontainer-ci:main`.
Ordering is contract-tested; deployed pull freshness must be checked by comparing
the publication record and runner evidence in a real default-branch dispatch.
Pre-merge branch execution in the current image does not establish this link.

Public repositories publish no runner image and receive verification from the
cache reusable instead. See [Default-branch verification](../standards-check.md#default-branch-verification)
for result names, execution, permissions and concurrency. The extra jobs hold the
existing publisher concurrency group longer. A red verification is a failed run
only, with no retries or issue automation.

## Build and publication

The shared `build-service` action supplies pinned client tools and the per-job
OIDC/mTLS certificate. Only central actions execute in the credentialed client;
repository content is BuildKit context data. The repository-image action uses
`docker buildx build --platform linux/amd64 --target ci --pull`, OCI mediatypes,
zstd level 9, no attestations, and no standards package secret.

Before starting BuildKit, the client uses the job's `github.token` in the
separate `docker-publish` config to resolve the Dockerfile's first `FROM`
(`ghcr.io/rubio-enterprises/base:main` by default) and external runner
`COPY --from` (`ghcr.io/rubio-enterprises/runner:main` by default). Both must be
literal tagged or digest references; ARG-expanded references are refused rather
than silently replacing a Release Pin with `main`. The `file` input selects the
Dockerfile parsed, including when it is not at the repository root.

Every build freshly resolves their amd64 digests, copies those exact images to
`$STAGING/base:$STAGING_TAG` and `$STAGING/runner:$STAGING_TAG`, and verifies each
copy. Buildx then substitutes the actual Dockerfile references with those
immutable staging digests using named-image contexts. No Dockerfile rewrite is
needed. The active `docker-build` config contains **staging auth only**: no GHCR
authentication or publishing token reaches the BuildKit session (mac-dev-playbook
ADR 0058). The daemon keeps its own layer cache. Its CI output goes to
`$STAGING/ci:$STAGING_TAG`, and its immutable digest is checked against BuildKit
metadata. Staging retains the connector's per-run repositories and expiry;
`Unavailable` builds retry once with the same resolved inputs.

Checksum-pinned Trivy scans the immutable staged image before publication. Scan
errors and findings fail closed, with findings kept only in transient files.
The existing central [scan policy](repository-image-secrets.md) is unchanged.
Before copying, the destination must be private/internal (or not yet created),
and the staged config must declare linux/amd64. Only then does crane copy the verified digest to `sha-<commit>-amd64` and buildx
publish one-platform indices as `main` and `sha-<commit>`. The selected amd64 child
must still equal the scanned digest. Pruning retains five untagged versions.
Credentials and scan files are removed on success and failure.

The built image records the actual amd64 upstream in
`org.opencontainers.image.base.name` and `org.opencontainers.image.base.digest`.
After verifying publication, the reusable uploads `repository-image-admission`
(`repository-image-admission.json`, retained for 30 days) with the repository,
revision, published `:main` index digest and base digest. This is also the
App-readable publication record for fleet coalescing when the receiver's
`GITHUB_TOKEN` cannot read a different repository's private GHCR package.

## Fleet rebuild coalescing

`fleet-ci-image-rebuild.yml` resolves `base:main`'s **amd64 child digest** from
GHCR. No standards release or producer payload change is required. Its optional
`base-digest` input / repository-dispatch payload field is an expected amd64
digest: an already-superseded request does nothing. The concurrency group cancels
older receiver loops. A registry recheck before each repository also stops an
obsolete loop if publication precedes delivery of the newer event.

The receiver reads the playbook's admission inventory, waits for cancelled
receivers to finish uploading their attribution artifacts, then checks live
repository metadata. Public, archived and disabled repositories are skipped with
a `::notice::` naming the repository and reason, not counted as failures. They
receive no cancellation, dispatch or ledger entry. Metadata lookup errors,
including HTTP 403/404, remain failures, as do dispatch and attribution errors.
The receiver reconciles each actionable private repository on its actual default
branch:

1. Read recorded fleet run IDs from earlier `fleet-dispatches` artifacts. Cancel
   active different-base runs only after verifying repository, default branch,
   `standards.yml`, `workflow_dispatch`, dispatch actor and triggering actor.
   Wait for completion before enqueueing replacement work. Pushes, schedules,
   human dispatches and unrecorded App dispatches are not cancellation targets.
2. Skip an already-current image, or an active same-base recorded fleet run.
   When direct private-package reads are denied, accept the admission record only
   from the latest default-branch `standards.yml` run using the central image
   reusable, matching the repository, revision, image name and base digest.
   Missing, expired or inaccessible records cause a rebuild, never a false skip.
3. Dispatch with `return_run_details: true`, persist the returned exact run ID
   atomically, then pace the next repository by 15 seconds. Individual failures
   are reported and the receiver fails after processing the remaining inventory.
   The attribution artifact uploads even on cancellation.

GitHub rejects undeclared workflow-dispatch inputs, and its run REST API does not
expose input values. Legacy callers therefore receive no unknown input and use
the central run-ID ledger instead. Block-form callers that explicitly declare
`workflow_dispatch.inputs.base-digest` receive it; the reusable reads
`github.event.inputs['base-digest']` defensively and refuses to build a different
base. No starter changes are required. The ledger uses only GitHub's native
Actions artifacts and dispatch response, not a separate state service.

Replay with a **fresh dispatch**, never a run rerun (which is rejected to preserve
its earlier attribution artifact):

```sh
gh workflow run fleet-ci-image-rebuild.yml --repo Rubio-Enterprises/.github --ref main
```

The first receiver after rollout has no ledger for pre-rollout dispatches and
will not guess their ownership from actor/time. If dispatch succeeds but its
response or ledger upload is lost, the outcome is reported as unknown; inspect
before retrying. Ledgers expire after 30 days. Same-base idempotency is guaranteed
for retained records, not for unrecorded or expired ones.

Fleet dispatches retain the cache reusable's ordinary lookup-only behavior.
Cache hits skip downloads and fills; cache misses still refresh the weekly cache.
