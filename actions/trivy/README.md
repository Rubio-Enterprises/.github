# Trivy from the vendor image

Both central image scanners use `install.sh`. It reuses only Trivy 0.76.1,
otherwise extracts `usr/local/bin/trivy` with the job's existing crane. The source
is the multi-platform `ghcr.io/aquasecurity/trivy:0.76.1` image, pinned by its OCI
index digest. Crane verifies the selected manifest and layer digests. The extracted
binary must also report the pinned version. Nothing runs in a new container, so
scanner flags, exit codes, working directories, policies and `TRIVY_*` database
environment remain the callers' own, unchanged behaviour.

## Source and authentication

The vendor's [0.76.1 release configuration](https://github.com/aquasecurity/trivy/blob/v0.76.1/goreleaser.yml#L137-L173)
publishes the same release to Docker Hub, GHCR and ECR Public, including amd64 and
arm64. GHCR and ECR Public both resolve to index
`sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c`.
Its amd64 manifest is `sha256:2e6c00d711ab4ae41d8a30c2c1fd3bd3b62704c177bb120f95012e939edfb3df`;
its arm64 manifest is `sha256:fc223b6f033406fd0c5efe59b566295e90cff76ccd2af696bd0d66d53ad83a2d`.

The GitHub release API refuses the read-only job token on both the
[hosted Docker route](https://github.com/Rubio-Enterprises/org-ops/actions/runs/43265048202/job/131346245293)
and the [self-hosted image pool](https://github.com/Rubio-Enterprises/org-ops/actions/runs/43265048202/job/131346246174)
because of aquasecurity's IP allow list. There is no release-asset fallback.

GHCR package access is different: the same contents:read job token is accepted
for the public package on [hosted](https://github.com/Rubio-Enterprises/org-ops/actions/runs/43265548284/job/131348017668)
and [self-hosted](https://github.com/Rubio-Enterprises/org-ops/actions/runs/43265548284/job/131348017059)
runners. Those probes also export the amd64 and arm64 binaries anonymously from
GHCR successfully, but production uses the existing job token, not anonymous
access. Credentials live in a new temporary Docker configuration, isolated from
the job's publishing configuration, and are removed on exit. A refusal fails;
there is no anonymous fallback and no new credential or permission.

Docker Hub's [anonymous pull quota](https://docs.docker.com/docker-hub/usage/pulls/)
and ECR Public's [one unauthenticated pull per second quota](https://docs.aws.amazon.com/AmazonECR/latest/public/public-service-quotas.html)
make them unsuitable anonymous fleet sources. GHCR [supports public image access](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
and does not document a per-address pull quota there. Production authenticates
regardless; the GitHub REST release-download budget is not a GHCR pull budget.
This does not promise immunity to service failures or abuse controls.

## Updating

The local Renovate regex manager extracts the image tag and `sha256:` digest
from `install.sh`, using the built-in `docker` datasource with `pinDigests: true`.
The datasource resolves the new OCI digest natively. It needs no checksum script,
release-attachment lookup or controller allowlist change. An image version bump
also changes the expected executable version, which is derived from the image tag.
