# App image builder

`app-image.yml` is the builder of the [App Image pipeline](app-image-pipeline.md): it builds one App Image
on the Build Service (mac-dev-playbook ADR 0057) as a **candidate** pushed to GHCR, and **promotes** a
tested candidate by retagging it. It serves private repositories only: public repositories cannot use
self-hosted runners and stay GitHub-hosted. [`repository-image.yml`](repository-image.md) builds the CI
runner image and is separate.

**Only `app-image-pipeline.yml` calls this workflow.** A repository calls the pipeline, which chains the
candidate build, the structure checks (T1), the smoke test, the repository's `just test-image` and the
promotion (build once, test, promote). This page describes the builder. The earlier modes (a build check
and a direct publish that rebuilt after testing, with `verified-image`) are gone.

The build job runs on the image Scale Set (`RUNNERS.image`, falling back to `linux-x64-image`, with no
hosted fallback) with no Docker daemon and no root. It builds **linux/amd64 only**: the builder is native
amd64 and has no emulation, and an arm64 App Image builds natively on the arm pool in the pipeline instead.

## Inputs

**Grant `contents: read`, `packages: write` and `id-token: write` on every calling job, pull requests
included.** A called workflow whose job asks for more than its caller grants fails the caller at startup,
even when the job would not run. `id-token: write` is what the certificate exchange needs; `packages:
write` lets a candidate reach GHCR. The workflow asks for no `attestations: write`: the GitHub artifact
attestation steps these repositories had ran only for public repositories. Provenance and SBOM
attestations come from BuildKit instead.

| Input | Meaning |
| --- | --- |
| `candidate` or `promote` | Exactly one. `candidate` builds the image and pushes it to GHCR under a temporary tag for a later job to pull by digest. `promote` gives a tested candidate its final tags: no build, no builder, no certificate |
| `image-name` | Untagged GHCR image: `ghcr.io/<owner>/<repo>` (the default) or `ghcr.io/<owner>/<repo>/<name>`. An image belongs to the repository that builds it; any other name is refused |
| `context`, `file`, `target` | Context directory, Dockerfile and stage, relative to the repository root. `file` defaults to `<context>/Dockerfile` |
| `build-args`, `labels` | One `NAME=value` / `key=value` per line. `labels` are extra OCI labels merged by `docker/metadata-action` |
| `tags`, `flavor` | `docker/metadata-action` rules. `promote` gives the image these tags; `latest` is always off |
| `attestations` | Add provenance (`mode=max`) and an SBOM to the candidate (default true). The pipeline sets it on a publishing run |
| `candidate-image` | Promote only: the tested candidate, the `image` output of the candidate build |
| `moving-tags` | Promote only: tags that may move to another image, one exact tag per line. Every other existing tag is never overwritten |
| `server`, `block-vulnerabilities` | Tier 1: a server's configuration must declare a `HEALTHCHECK`; a fixable CRITICAL vulnerability fails the build (default: report only) |
| `ref`, `submodules` | Passed to `actions/checkout`. A `ref` other than the event's also switches `docker/metadata-action` to `context: git`, so tags and labels describe what was built |
| `timeout-minutes` | Build job timeout (default 45) |

Outputs are `digest`, `image` (`image@digest`), `version` and `tags` (promote only).

## Candidates

A job that must run the image (Testcontainers, Compose, KVM) stays on a Docker route, which cannot reach
the Build Service host. It pulls the candidate from GHCR by digest, which is what the pipeline's smoke and
test jobs do.

A candidate is tagged `candidate-<run>-<attempt>-<12 hex digits of its digest>`. Unless `attestations` is
false it carries the provenance and SBOM its promotion will publish, so it is an index whose two children
(the image, the attestations) are untagged versions of their own. The digest part keeps two candidates of
one image in the same run from overwriting each other's tag, which would leave the first untagged and out
of prune's reach. Each later candidate build deletes candidates older than 24 hours (`prune.sh`), touching
only versions whose every tag looks like that, together with each one's untagged child versions (read from
its index through `docker buildx imagetools inspect --raw`), and pages through all of the package's
versions. A candidate that was promoted also carries final tags, so neither it nor its children are touched.
The newest few therefore stay until the next run: that is the cost of not needing a cleanup job
in every caller. **Deleting a package version with `GITHUB_TOKEN` needs the repository to hold
the Admin role on the package** (Manage Actions access), not Write: with Write the push works and
every delete is refused. A refused or failed prune only warns and never fails the build, so a
package that is not set up leaves its candidates behind; check the first run's log.

## Tier 1 and promotion

Between the build and the copy to GHCR, the build job runs **T1** (`structure.sh`) against the staged image with the
staging credential only, so an image that fails is never pushed to GHCR: a non-root `USER`, a `HEALTHCHECK`
when `server` is true, the `org.opencontainers.image.source` and `revision` labels, a Trivy secret scan of
layers and configuration (a finding fails; the match is never printed), and a vulnerability report of the
fixable HIGH and CRITICAL findings (it fails only with `block-vulnerabilities`, and only on a fixable
CRITICAL). Trivy is the checksum-pinned release `repository-image.yml` uses and runs outside the checkout, so a
`trivy.yaml`, `.trivyignore` or `trivy-secret.yaml` in the repository changes nothing.

`promote: true` runs a separate job on the small route with only `contents: read` and `packages: write`.
It computes the tags, checks the candidate is a digest of this image that exists, refuses `latest`, a
`candidate-` lookalike and any existing tag that points at another image (unless listed in `moving-tags`;
re-running with the same digest passes), and copies the digest with `imagetools create`. A tag that
resolves to another digest afterwards fails the job.

## How a job gets the image to GHCR

1. `connect` (the `build-service` action's `connect.sh`, reused so there is one
   implementation) trades the job's OIDC token for a one-hour client certificate and configures
   the remote buildx builder.
2. `build` runs `docker buildx build` into the staging registry beside the builder. The builder
   holds no registry credential, so no `--cache-from`/`--cache-to`: its own cache persists.
3. `candidate` uses the GHCR publication credential. The build step forwards pull authentication only through the BuildKit session. `docker buildx imagetools create
   --prefer-index=false` copies the staged digest to each tag, preserving the digest and every
   child manifest, and each tag is checked against it. `--prefer-index=false` matters: by default
   buildx wraps a bare image manifest (a candidate with `attestations: false`) in a
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
- **`promote` job** (small pool, `contents: read`, `packages: write`, no OIDC token, no certificate): the
  registry client tools and the central `promote` action; a checkout appears only for a `ref` other than the
  event's, as data for `docker/metadata-action`.

## Contract limits

- Any path from the caller must stay inside the checkout, symlinks resolved.
- `--secret` and SSH mounts are not offered. No repository's image build used one.
- The first BuildKit restart-retry (`code = Unavailable`) is the only retry. It is `retry_unavailable` in
  `actions/build-service/retry.sh`, shared with the CI image builder.
- `image-name` is the repository's name or a name under it (`<repo>/<name>`): the workflow and every
  script refuse any other package, so one repository cannot publish into another's. GHCR separately
  refuses a push to an existing package the repository has no access to.

### What a build can reach on the shared builder

Every image build runs on one daemon, and the owner accepted these exposures under ADR 0057,
because every repository and every pull request is the owner's, bounded by the daily staging wipe
and the weekly rebuild of the builder:

- **Not "pulls with no credentials".** The build's Docker config holds the shared staging registry
  credential, and BuildKit hands it to a build that asks (`FROM`, a `# syntax=` frontend, a
  `COPY --from`). A Dockerfile can therefore `FROM` another repository's staged image
  (`<staging>/rubio-enterprises/<repo>/run-<run>-<attempt>/<package>:run-…`) and read it. Staged images are disposable
  and the registry is emptied daily. Base images from public registries need no credential. The
  GHCR pull credential is forwarded only through the BuildKit registry-auth session; the client private key never reaches the daemon.
- **Cache mounts are daemon-wide.** `RUN --mount=type=cache` shares a cache by `id`, and the
  default id is the target path, so two repositories mounting `/root/.cache/uv` share one cache.
  A repository can poison another's package cache, and the poison lands in the other repository's
  published image. Give cache mounts a repository-prefixed `id` (`id=<repo>-uv`) to keep them
  apart; the workflow does not enforce it. The weekly rebuild clears every cache.

## Validation boundary

`tests/test_app_image.py` runs the scripts against stubs that model the tools they call
(including buildx's wrapping of a bare image manifest, so the `--prefer-index=false` test fails
without the flag) and checks the workflow's shape and trust boundaries.

Functional runs on 2026-11-03 used the real scripts against BuildKit v0.34.1 over mTLS, a TLS and
password-protected staging registry and a stand-in registry aliased `ghcr.io`, with a stub for the
Private CA exchange only:

- a candidate copy with the pre-fix script failed its digest check (`c1f7d2…` against `2821c2…`),
  and passes with `--prefer-index=false`;
- an attested index copied with the same digest, children present, no referrers tag, then promoted to
  two tags with the digest unchanged.

The production builder, the Private CA exchange and GHCR's package
deletion are qualification gates for the canary, not claims of the fixtures.
