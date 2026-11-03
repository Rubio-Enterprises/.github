# App Image pipeline

`app-image-pipeline.yml` takes one App Image from source to published tag: **build once, test that exact
digest, promote it by retagging**. Nothing is rebuilt at release, so the bytes a job tested are the bytes
that carry the version tag. A pull request runs every step except the promotion. The decision and the
alternatives are standards ADR 0044 (`docs/adr/0044-build-once-test-promote-app-images.md`).

A Candidate Image is an App Image pushed to a temporary tag (`candidate-<run>-<attempt>-<12 hex>`) so that a
job on another runner can pull and test it. To promote is to give the tested Candidate Image its final tags,
by digest, in the same package.

## The tiers

| Tier | Job | Runs on | Holds | What it checks |
| --- | --- | --- | --- | --- |
| Build | `build` (the [builder](app-image.md)) | image Scale Set | OIDC token, GHCR write | One candidate on the Build Service (amd64). Attestations (SLSA provenance, SBOM) when the run will publish |
| **T1** structure | inside `build` | image Scale Set | staging credential only | Non-root `USER`; a `HEALTHCHECK` when the image is a server; the `source` and `revision` labels; a secret scan of layers and configuration; a vulnerability report. It reads the staged image **before anything reaches GHCR**: a failing image is never pushed |
| **T2** smoke | `smoke` | `docker` route (`linux-x64-large`) | `packages: read`, no checkout | A server starts and reports healthy, and no process runs as root. A command-line tool runs its `smoke-command` and exits 0 |
| **T3** functional | `test` | `docker` route | `contents: read`, `packages: read` | `mise run test:image`, with `IMAGE` set to the candidate (`image@digest`) |
| Promote | `promote` (the builder's promote job) | small route | GHCR write, no OIDC token | `imagetools create` of the tested digest under the final tags |

T1 and T2 are always on. T3 is mandatory: the repository defines a `test:image` task, and the `test` job
fails with an instruction when it does not. What it tests is the repository's choice: its main purpose,
against fakes or a compose stack, with no production secrets. There is no minimum test count and no JUnit
requirement. Pull requests that change only documentation skip every image job (`ignore-paths`).

The vulnerability report lists fixable HIGH and CRITICAL findings in the job summary and never fails the
build. `block-vulnerabilities: true` fails it on a fixable CRITICAL; the default turns on once a baseline
exists. A secret finding always fails: only the rule, file and lines are logged, never the match.

## Calling it

Copy the starter `image.yml` (standards `starter/image/`). The Test Gate calls it on pull requests, so its
jobs appear on the pull request and `test-gate` waits for them (standards ADR 0044); tags and releases call
it on their own.

```yaml
# .github/workflows/image.yml
name: image
"on":
  push:
    tags: ["v*"]
  workflow_dispatch:
  workflow_call:
permissions: {}
jobs:
  image:
    permissions:
      contents: read
      packages: write
      id-token: write
    uses: Rubio-Enterprises/.github/.github/workflows/app-image-pipeline.yml@main
    with:
      publish: ${{ github.event_name == 'push' }}
      tags: |
        type=semver,pattern={{version}}
        type=sha,prefix=sha-,format=short
```

**Every calling job grants `contents: read`, `packages: write` and `id-token: write`, pull requests
included.** A called workflow whose job asks for more than its caller grants fails the caller at startup,
even if the job is skipped. Inside the pipeline only `build` and `promote` ask for `id-token`.

| Input | Meaning |
| --- | --- |
| `publish` | Promote the tested candidate to the final tags. Pass `false` on a pull request |
| `tags`, `flavor`, `labels` | `docker/metadata-action` rules. `tags` is required when `publish` is true |
| `image-name` | `ghcr.io/<owner>/<repo>` (the default) or `ghcr.io/<owner>/<repo>/<name>`. Any other name is refused |
| `moving-tags` | Exact tags allowed to move (a branch channel such as `main`). Every other existing tag is **never overwritten**; `latest` is **never published** |
| `smoke-command` | A command-line tool's arguments for its entrypoint (for example `--version`). Empty means a server, which must declare a `HEALTHCHECK` |
| `smoke-env`, `smoke-timeout-seconds` | `NAME=value` lines (no secrets) a server needs to start, and how long it has to become healthy (default 120) |
| `block-vulnerabilities` | Fail on a fixable CRITICAL |
| `ignore-paths` | Shell patterns, one per line; a pull request whose every change matches skips the image jobs. Default `*.md`, `docs/*`, `LICENSE` (`*` also matches a slash) |
| `arch` | `amd64` (default, the Build Service) or `arm64` (native build, below) |
| `context`, `file`, `target`, `build-args` | The build. `build-args` may carry the version a release image bakes in: the candidate is built in the publishing run |
| `standards-install` | amd64 only: see the [builder](app-image.md#what-runs-where) |
| `ref`, `submodules`, `timeout-minutes`, `test-timeout-minutes` | As named |

Outputs: `image` (the published `image@digest` of a publishing run, otherwise the tested candidate's),
`digest`, `version`, `tags`.

### What `test:image` receives

`IMAGE` is the candidate, `ghcr.io/<owner>/<package>@sha256:<digest>`, already pulled onto the runner by
digest, and `IMAGE_NAME` is the same without the digest. The registry credential is gone before the task
starts. A repository with several images calls the pipeline once per image; the one task receives each
image in turn, so it tells them apart by `IMAGE_NAME`. The job runs `mise run setup` first, as the Test
Gate does.

```toml
[tasks."test:image"]
description = "Run the candidate image against its fakes"
run = "pnpm exec vitest run test/image"
```

## arm64

The Build Service is amd64. `arch: arm64` builds the same candidate natively on the arm pool
(`RUNNERS.arm`, `linux-arm64`), with no emulation: `docker/build-push-action` pushes the candidate to GHCR
straight from the job, which holds `packages: write` but no OIDC token, so the Private CA is not involved.
T1 then reads the candidate from GHCR in a separate small-pool job that holds no repository code
(`structure-arm64`). T2 and T3 run on the arm pool, and promotion is the same retag, which is
architecture-neutral. The arm64 path has no `standards-install`, and its candidate is pushed before T1, so a
failing image leaves a candidate behind until prune deletes it.

## Why the jobs are split

Repository code runs in exactly one job, `test`, and that job holds read access only: it cannot publish, build
on the Build Service or mint a certificate. The job that holds the certificate and the registry credentials
(`build`) runs only this repository's central actions and pinned third-party actions; repository files reach
it as build-context data. T2 runs the image and nothing else. Promotion holds only the GHCR token.

## Candidates, attestations and prune

A candidate built for a publishing run is an index: the image, an SLSA provenance manifest and an SBOM
manifest. Promotion retags that index, so the published digest, attestations included, is the tested
digest. Retagging adds tags to the same version: GHCR cannot remove a tag, so a promoted version keeps its
`candidate-…` tag beside the final ones, which is harmless and tells you which run built it.

Every candidate build deletes candidates older than 24 hours from the same package, together with each
stale index's untagged child versions. A version that also carries a final tag is never touched, nor are
its children. Deleting needs the repository to hold the **Admin** role on the package (Manage Actions
access); with Write, pushes work and deletes only warn.

## Validation boundary

`tests/test_app_image_pipeline.py` pins the job graph, permissions and the place repository code may run.
`tests/test_app_image.py` runs the scripts against stubs of the tools they call. Functional runs on
2026-11-03 used the real scripts and the pinned Trivy against BuildKit v0.34.1, a TLS registry and a
stand-in aliased `ghcr.io`: an attested candidate built, structure-checked (a root image, a missing
`HEALTHCHECK` and a leaked token each failed, with the match never printed), copied, and promoted with the
digest, both children and no `sha256-…` referrers tag intact, and a stale candidate's children found and
deleted through `imagetools inspect --raw`. The Private CA exchange, GHCR's package deletion and a real
`test:image` run are the canary's gates.
