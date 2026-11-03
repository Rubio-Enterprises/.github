# App image builder

`app-image.yml` builds one app or release container image on the Build Service
(mac-dev-playbook ADR 0057) and copies it to GHCR. It replaces the per-repository
`docker-image.yml`/`docker-publish.yml` copies that built on a job-local Docker daemon, and
it serves private repositories only: public repositories cannot use self-hosted runners and
stay GitHub-hosted. [`repository-image.yml`](repository-image.md) builds the Dev Container
pair and is separate.

The job runs on the image Scale Set (`RUNNERS.image`, falling back to `linux-x64-image`, with
no hosted fallback) with no Docker daemon and no root. It builds **linux/amd64 only**: the
builder is native amd64 and has no emulation, so a repository that needs another platform
cannot use this workflow.

## Calling it

```yaml
jobs:
  image:
    uses: Rubio-Enterprises/.github/.github/workflows/app-image.yml@main
    permissions:
      contents: read
      packages: write
      id-token: write
    with:
      publish: ${{ github.event_name == 'push' }}
      tags: |
        type=semver,pattern={{version}}
        type=sha,prefix=sha-,format=short
```

**Grant all three permissions on every calling job, pull requests included.** A called
workflow whose job asks for more than its caller grants fails the caller at startup, even when
the job would not run. `id-token: write` is what the certificate exchange needs; `packages:
write` lets a candidate or published image reach GHCR. The workflow asks for no
`attestations: write`: the GitHub artifact attestation steps these repositories had ran only
for public repositories. Provenance and SBOM attestations come from BuildKit instead.

| Input | Meaning |
| --- | --- |
| `image-name` | Untagged GHCR image in the caller's owner namespace. Default `ghcr.io/<owner>/<repo>` |
| `context`, `file`, `target` | Context directory, Dockerfile and stage, relative to the repository root. `file` defaults to `<context>/Dockerfile` |
| `build-args`, `labels` | One `NAME=value` / `key=value` per line. `labels` are extra OCI labels merged by `docker/metadata-action` |
| `tags`, `flavor` | `docker/metadata-action` rules. `tags` is required when `publish` is true |
| `publish` | Push the final tags, with `provenance=mode=max` and an SBOM unless `attestations` is false |
| `candidate` | Push one temporary tag for a later job to pull. Exclusive with `publish` |
| `standards-install` | Run `mise run standards:install` first, for a Dockerfile that copies a file out of `.standards/` |
| `ref`, `submodules` | Passed to `actions/checkout` |
| `timeout-minutes` | Job timeout (default 45) |

With neither `publish` nor `candidate` the workflow is a **build check**: the image is built
and left in the staging registry, which the daily wipe empties. Outputs are `digest`, `image`
(`image@digest`, empty for a check), `version` and `tags`.

## Build, then run

A job that must run the image (Testcontainers, Compose, KVM) stays on a Docker route, which
cannot reach the Build Service host. It pulls from GHCR instead:

```yaml
jobs:
  image:
    uses: Rubio-Enterprises/.github/.github/workflows/app-image.yml@main
    permissions: { contents: read, packages: write, id-token: write }
    with: { candidate: true }
  test:
    needs: image
    runs-on: ${{ fromJSON(vars.RUNNERS || '{}').docker || fromJSON('["ubuntu-latest"]') }}
    permissions: { contents: read, packages: read }
    steps:
      - run: docker pull "${IMAGE}" && ./run-the-tests
        env:
          IMAGE: ${{ needs.image.outputs.image }}
```

A candidate is tagged `candidate-<run>-<attempt>` and built without attestations, so it is one
manifest. Each later candidate build deletes candidates older than 24 hours (`prune.sh`),
touching only versions whose every tag looks like a candidate. The newest few therefore stay
until the next run: that is the cost of not needing a cleanup job in every caller. A failed
prune warns and never fails the build.

To publish only after the run job passes, call the workflow a second time with `publish: true`
after it. That rebuild finds every layer in the builder's cache, and attaches the
attestations, which is what the old test-then-push flow did.

## How a job gets the image to GHCR

1. `connect` (the `repository-image` action's `connect.sh`, reused so there is one
   implementation) trades the job's OIDC token for a one-hour client certificate and configures
   the remote buildx builder.
2. `build` runs `docker buildx build` into the staging registry beside the builder. The builder
   holds no registry credential, so no `--cache-from`/`--cache-to`: its own cache persists.
3. `publish` is the only step with the GHCR token. `docker buildx imagetools create` copies the
   staged digest to each tag, preserving the digest and every child manifest, and each tag is
   checked against it. `crane copy` is deliberately not used: it also writes a
   `sha256-<digest>` referrers tag per attestation, which GHCR cannot hide.

The Private CA admits this workflow's `job_workflow_ref`
(`Rubio-Enterprises/.github/.github/workflows/app-image.yml@refs/heads/main`) from
`cue/incus/services_build_client.cue` in mac-dev-playbook. A canary from another branch needs
its own entry there.

## Contract limits

- The repository's own tasks (`standards-install`) run before the job holds a certificate or a
  registry credential.
- Any path from the caller must stay inside the checkout, symlinks resolved.
- The Dockerfile's `FROM` images must be public: the builder pulls with no credentials.
- `--secret` and SSH mounts are not offered. No repository's image build used one.
- The first BuildKit restart-retry (`code = Unavailable`) is the only retry.

## Validation boundary

`tests/test_app_image.py` runs the three scripts against stubs and checks the workflow's shape.
A functional run on 2026-11-03 used the real scripts against BuildKit v0.34.1 over mTLS, a
TLS and password-protected staging registry and a stand-in registry aliased `ghcr.io`, with a
stub for the Private CA exchange only. It covered a check, a candidate, and a publish with
provenance and SBOM to three tags, and confirmed digest equality and child manifests in the
target. The production builder, the Private CA exchange and GHCR's package deletion are
qualification gates for the canary, not claims of the fixtures.
