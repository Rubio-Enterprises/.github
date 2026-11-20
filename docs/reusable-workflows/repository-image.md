# CI Repository Image builder

`repository-image.yml` builds the caller's Dockerfile `ci` target on the native
amd64 Build Service. Public repositories run literal `just` recipes and publish
no runner image. Dev images are built locally, never published.

## Workflow interface

Caller grants `actions: read`, `contents: read`, `packages: write`, `id-token: write`.
Deploy the caller grant before the central evidence shortcut; an ungranted caller
is rejected at workflow load, not handled by the script's fail-open API fallback.
Inputs: `file` (default `Dockerfile`), `target` (must be `ci`), `context` (default `.`),
`image-name` (default `ghcr.io/<owner>/<repo>/devcontainer-ci`). Off-default builds
must use a scratch name inside the caller repository's package namespace.
The retired `config`, `base`, `base-ci` inputs are not accepted. No workflow outputs.

Builds run on default-branch push, schedule or dispatch. Caller `standards.yml`
must invoke this reusable on `workflow_dispatch`, not just pushes.

## Default-branch verification

After `build` succeeds, the `verify` job calls `default-branch-check.yml` with only
`actions: read`, `contents: read` and `packages: read`, and no inherited secrets.
It waits for `build` to finish, including the final prune step, but does not
require pruning to succeed. Its caller guard requires a push or workflow dispatch
on the actual default branch, excludes the non-onboarded `.github` source
repository, and requires an empty `image-name` input. Scheduled and scratch-name
builds do not verify. A failed publication cannot produce passing verification.

The shared router selects image or fallback execution. Admitted image jobs start
after publication so the runner can pull the newly published `devcontainer-ci:main`.
Ordering is contract-tested. To check pull freshness, compare the published
`devcontainer-ci:main` digest with runner evidence in a real default-branch dispatch.
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

Each repository pins the Base Image in its Dockerfile's first `FROM`
(`ghcr.io/rubio-enterprises/base:main@sha256:…`) and the runner image in
`COPY --from`. A weekly Renovate pull request moves both digests. Publishing a
new base does not trigger a repository build.

Before starting BuildKit, the client uses the job's `github.token` in the
separate `docker-publish` config to resolve those literal references. It also
accepts unpinned tags and Release Pins, but rejects ARG-expanded references.
The `file` input selects the Dockerfile, including one in a subdirectory.

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
and the staged config must declare linux/amd64. The publisher compares the staged
manifest with the immutable `rubio-enterprises/base` manifest named by the staged
image's `org.opencontainers.image.base.digest` label. For each shared layer digest,
it skips blobs already present and asks GHCR to mount missing blobs from the base
package. A refused mount fails publication and names the layer; there is no upload
fallback. The mount client sends the job credential only to GHCR's token service
and does not follow redirects.

Crane then copies the verified digest to `sha-<commit>-amd64`. The job logs the
stored sizes of blobs the copy actually uploaded (compressed layers and the image
config, excluding manifests and retries). Buildx publishes one-platform indices
as `main` and `sha-<commit>`. The selected amd64 child must still equal the scanned
digest. Credentials and scan files are removed on success and failure. After a
successful build and publication, the final step prunes untagged versions with the
existing five-version retention setting. Prune errors appear in the step log but
do not fail the build or block verification. The build job has a 55-minute
ceiling, including up to 10 minutes for pruning.

The built image records the Dockerfile's base reference in
`org.opencontainers.image.base.name` and the resolved amd64 digest in
`org.opencontainers.image.base.digest`.
