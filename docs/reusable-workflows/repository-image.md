# CI Repository Image builder

`repository-image.yml` is the reusable workflow that builds each admitted repository's CI Image
(`devcontainer-ci:main`) on the Build Service (mac-dev-playbook ADR 0057 and 0058).

Under the host-first repository environment (Knowledge `docs/host-first-repository-environment.md`),
development commands execute on the host via `just` and Docker Compose. Dev images are never built
or published centrally. Only the CI runner image (`devcontainer-ci:main`) is built and published,
used by the Container Pool on self-hosted runners.

## Contract

- **Source**: The `ci` target of the repository's own root `Dockerfile`.
- **Base image**: `ghcr.io/rubio-enterprises/base:main` and `ghcr.io/rubio-enterprises/runner:main` pulled fresh (`--pull`).
- **Platform**: `linux/amd64` only (Container Pool architecture).
- **Destination**: `ghcr.io/rubio-enterprises/<repo>/devcontainer-ci:main` and `sha-<commit>`.
- **Secrets**: No standards package secret or credentials passed to BuildKit.
- **Output format**: OCI mediatypes, zstd compression level 9.
- **Trivy audit**: Scanned with checksum-pinned Trivy for secret leaks before push to GHCR.
- **Hosted execution**: Public repositories build the same `ci` target with local Docker on `ubuntu-latest`.

