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
| `verified-image` | Publish only: the `image` output of the candidate a job tested. The publish fails unless its amd64 layers equal the candidate's. Omit it to publish without the check; a value that resolves empty (an output that is missing) fails the run, never skips the check |
| `standards-install` | For a Dockerfile that copies a file out of `.standards/`: a separate job runs `mise run standards:install` and hands the directory over as an artifact (see "What runs where") |
| `ref`, `submodules` | Passed to `actions/checkout`. A `ref` other than the event's also switches `docker/metadata-action` to `context: git`, so tags and labels describe what was built |
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

A candidate is tagged `candidate-<run>-<attempt>-<12 hex digits of its digest>` and built without
attestations, so it is one manifest. The digest part keeps two candidates of one image in the same
run from overwriting each other's tag, which would leave the first untagged and out of prune's
reach. Each later candidate build deletes candidates older than 24 hours (`prune.sh`), touching
only versions whose every tag looks like that, and pages through all of the package's versions.
The newest few therefore stay until the next run: that is the cost of not needing a cleanup job
in every caller. **Deleting a package version with `GITHUB_TOKEN` needs the repository to hold
the Admin role on the package** (Manage Actions access), not Write: with Write the push works and
every delete is refused. A refused or failed prune only warns and never fails the build, so a
package that is not set up leaves its candidates behind; check the first run's log.

To publish only after the run job passes, call the workflow a second time with `publish: true`
after it, and pass the candidate as `verified-image`:

```yaml
  publish:
    needs: test
    uses: Rubio-Enterprises/.github/.github/workflows/app-image.yml@main
    permissions: { contents: read, packages: write, id-token: write }
    with:
      publish: true
      verified-image: ${{ needs.image.outputs.image }}
      tags: type=semver,pattern={{version}}
```

That rebuild finds every layer in the builder's cache and attaches the attestations, which is
what the old test-then-push flow did. The cache is not a guarantee: any client may prune the
builder (ADR 0058), and a build step that is not reproducible rebuilds to different layers.
`verified-image` therefore compares the amd64 layer digests, in order, of the image just built
and of the candidate that was tested, and fails before anything is published if they differ;
re-run the workflow. The config (labels, `created`) legitimately differs.

The check cannot be skipped by accident. An omitted input defaults to `none`; one that is passed but
resolves empty (the job it reads was skipped, or the output was renamed) arrives as an empty string,
which GitHub does not replace with the default, and the workflow fails with `verified-image was passed
but is empty`. Pass `none` yourself only to publish without verification on purpose.

## How a job gets the image to GHCR

1. `connect` (the `repository-image` action's `connect.sh`, reused so there is one
   implementation) trades the job's OIDC token for a one-hour client certificate and configures
   the remote buildx builder.
2. `build` runs `docker buildx build` into the staging registry beside the builder. The builder
   holds no registry credential, so no `--cache-from`/`--cache-to`: its own cache persists.
3. `publish` is the only step with the GHCR token. `docker buildx imagetools create
   --prefer-index=false` copies the staged digest to each tag, preserving the digest and every
   child manifest, and each tag is checked against it. `--prefer-index=false` matters: by default
   buildx wraps a bare image manifest (a candidate, or a publish with `attestations: false`) in a
   new index with a different digest, so the check could never match; an index is copied byte
   for byte either way. `crane copy` is deliberately not used: it also writes a
   `sha256-<digest>` referrers tag per attestation, which GHCR cannot hide.

The Private CA admits this workflow's `job_workflow_ref`
(`Rubio-Enterprises/.github/.github/workflows/app-image.yml@refs/heads/main`) from
`cue/incus/services_build_client.cue` in mac-dev-playbook. A canary from another branch needs
its own entry there.

## What runs where

Repository files are data to the job that holds the credentials, never code.

- **`build` job** (image Scale Set; OIDC token, client certificate, staging password, GHCR token):
  runs only this repository's central actions and pinned third-party actions. It checks the
  caller out and sends it to the builder as a build context; `docker buildx build` and
  `docker/metadata-action` read files from it but execute none. No step runs `mise`, `npm`, a
  script from the checkout or anything else the repository controls, because a task there could
  mint its own OIDC token for the Build Service audience (every step of the job has the request
  variables), read the staging password, put its own `crane`, `jq` or `curl` first on `PATH`
  (the client tool installer skips a tool already on `PATH`), set `BASH_ENV` or the
  coordinates through `GITHUB_ENV`, or rewrite the downloaded action scripts. ADR 0058 rejected
  exactly that. `tests/test_app_image.py` pins this shape.
- **`prepare` job** (`standards-install` only; small pool, `contents: read`, `packages: read`, no
  OIDC token): runs the repository's `mise run standards:install` and uploads `.standards/` as a
  one-day artifact with a name unique to the call. The build downloads it into the workspace,
  where it is build-context data like any tracked file. The small pool cannot reach the Build
  Service host, and the job has nothing to write to GHCR with.
- **Inside the builder**, the Dockerfile's steps are the repository's code. See the next section.

## Contract limits

- Any path from the caller must stay inside the checkout, symlinks resolved.
- `--secret` and SSH mounts are not offered. No repository's image build used one.
- The first BuildKit restart-retry (`code = Unavailable`) is the only retry.
- `image-name` may be any new package in the owner's namespace. GHCR itself refuses a push to an
  existing package the repository has no access to.

### What a build can reach on the shared builder

Every image build runs on one daemon, and the owner accepted these exposures under ADR 0057,
because every repository and every pull request is the owner's, bounded by the daily staging wipe
and the weekly rebuild of the builder:

- **Not "pulls with no credentials".** The build's Docker config holds the shared staging registry
  credential, and BuildKit hands it to a build that asks (`FROM`, a `# syntax=` frontend, a
  `COPY --from`). A Dockerfile can therefore `FROM` another repository's staged image
  (`<staging>/rubio-enterprises/<repo>/<package>:run-…`) and read it. Staged images are disposable
  and the registry is emptied daily. Base images from public registries need no credential. The
  GHCR token and the client key never reach the daemon.
- **Cache mounts are daemon-wide.** `RUN --mount=type=cache` shares a cache by `id`, and the
  default id is the target path, so two repositories mounting `/root/.cache/uv` share one cache.
  A repository can poison another's package cache, and the poison lands in the other repository's
  published image. Give cache mounts a repository-prefixed `id` (`id=<repo>-uv`) to keep them
  apart; the workflow does not enforce it. The weekly rebuild clears every cache.

## Validation boundary

`tests/test_app_image.py` runs the three scripts against stubs that model the tools they call
(including buildx's wrapping of a bare image manifest, so the `--prefer-index=false` test fails
without the flag) and checks the workflow's shape and trust boundaries.

Functional runs on 2026-11-03 used the real scripts against BuildKit v0.34.1 over mTLS, a TLS and
password-protected staging registry and a stand-in registry aliased `ghcr.io`, with a stub for the
Private CA exchange only:

- check, candidate, and publish with provenance and SBOM to three tags;
- a candidate copy with the pre-fix script failed its digest check (`c1f7d2…` against `2821c2…`),
  and passes with `--prefer-index=false`; so does a publish with `attestations: false`;
- an attested index copied with the same digest, children present, no referrers tag;
- `verified-image` accepted an attested publish of the tested layers and refused a publish of
  different layers, publishing nothing.

The production builder, the Private CA exchange, `standards-install` end to end and GHCR's package
deletion are qualification gates for the canary, not claims of the fixtures.
