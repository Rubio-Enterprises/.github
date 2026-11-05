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
