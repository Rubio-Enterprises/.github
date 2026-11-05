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

GHCR pull authentication is forwarded through the BuildKit session's Docker
config, not as a Dockerfile secret, ARG or ENV. It is removed after the build.
The daemon keeps its own layer cache. The output goes to the job-scoped staging
registry, and its immutable digest is checked against BuildKit metadata.

Checksum-pinned Trivy scans the immutable staged image before publication. Scan
errors and findings fail closed, with findings kept only in transient files.
The existing central [scan policy](repository-image-secrets.md) is unchanged.
Before copying, the destination must be private/internal (or not yet created),
and the staged config must declare linux/amd64. Only then does crane copy the verified digest to `sha-<commit>-amd64` and buildx
publish one-platform indices as `main` and `sha-<commit>`. The selected amd64 child
must still equal the scanned digest. Pruning retains five untagged versions.
Credentials and scan files are removed on success and failure.
