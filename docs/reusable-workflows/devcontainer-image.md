# Dev Container image reusable workflow contract

## Interface

`.github/workflows/devcontainer-image.yml` accepts `workflow_call` only.

| Input | Type | Default |
| --- | --- | --- |
| `config` | string | `.github/.devcontainer/devcontainer.json` |
| `image-name` | string | `ghcr.io/<owner>/<repo>/devcontainer`, lowercased |

Image overrides must be untagged GHCR paths under the caller's owner. Config
paths must be repository-relative without parent traversal. No additional secrets
or workflow outputs are required. The pilot consumer is `bookmark-scraper`.

The caller grants `contents: read` and `packages: write` to the calling job;
fork PRs retain GitHub's read-only token. Do not use `secrets: inherit` or
`pull_request_target`. The reusable gives PR build jobs only `contents: read`
and `packages: read`, publishing build jobs `contents: read` and
`packages: write`, and merge/prune jobs only `packages: write`.

## Caller

Pin the pilot to the candidate PR head SHA. After release, use the ordinary
release-please reusable-workflow SHA stream, not `gates/wf-v1`. This workflow
is not an injected Gate Family and needs no GitHub settings changes.

A caller owns its triggers, for example (replace `<commit-sha>` with a full SHA):

```yaml
name: devcontainer image
"on":
  push:
    branches: [main]
    paths: &inputs
      - .github/.devcontainer/**
      - .github/workflows/devcontainer-image.yml
      - .mise*.toml
      - mise.lock
      - .config/**
      - "**/package.json"
      - "**/package-lock.json"
      - "**/pnpm-lock.yaml"
      - "**/pnpm-workspace.yaml"
      - "**/pyproject.toml"
      - "**/uv.lock"
      - "**/bun.lock"
  pull_request:
    paths: *inputs
  schedule:
    - cron: "41 4 * * 0"
  workflow_dispatch:
permissions: {}
jobs:
  image:
    permissions:
      contents: read
      packages: write
    uses: Rubio-Enterprises/.github/.github/workflows/devcontainer-image.yml@<commit-sha>
```

Use the repository's actual default branch and complete build inputs (including
setup tasks, other language manifests/lockfiles, and any files they consume).
Source-only changes need not rebuild: the workspace mount hides the image's
source snapshot.

## Build and publication

- Pull requests build both native architectures without pushing, merging, or
  pruning. Only `push`, `schedule`, and `workflow_dispatch` on the default
  branch publish. Dispatches on other branches and all other events do nothing.
- amd64 uses `RUNNERS.docker` (hosted fallback `ubuntu-latest`); arm64 uses
  `RUNNERS.arm` (hosted fallback `ubuntu-24.04-arm`). Docker's architecture
  is checked before building, so a misrouted daemon fails instead of emulating.
  The ARM pool's Tart Ubuntu runner template supports Docker; its existing image
  maintenance pulls images inside that guest. Public consumers cannot see the
  private route map and use the hosted fallbacks. No QEMU is installed.
- The official CLI is installed as `@devcontainers/cli@0.89.0` under
  `RUNNER_TEMP`, with npm lifecycle scripts disabled. The direct CLI allows
  an exact version pin and the OCI source label; `devcontainers/ci` installs
  a floating `@devcontainers/cli@0` and has no label input. Only `build`
  runs: image setup belongs in the caller's Dockerfile, not lifecycle commands.
- Each architecture imports cache from `:main-amd64` or `:main-arm64`,
  then pushes `:sha-<commit>-amd64` or `:sha-<commit>-arm64`.
  Missing caches are normal on the first build. Same-repo PRs can authenticate
  read-only for cache; forks do not receive registry credentials and may build
  cold. Private base images/features must independently permit the caller.
- The CLI receives `org.opencontainers.image.source` pointing at the caller's
  repository, linking the package for inherited access. GHCR creates packages
  private by default; the pilot must verify visibility and repository access.
  This workflow never changes package visibility or grants cross-repo access.
- One merge job creates multi-arch `:main` and `:sha-<commit>` from both
  tagged architecture images, then advances the per-architecture cache aliases.
  It inspects both resulting multi-arch tags. Failure of either build prevents
  merge and prune.
- Pruning keeps five untagged package versions via the caller's
  `GITHUB_TOKEN`. Publishing must grant that repository package admin access,
  as GHCR normally does for a package created by its workflow. The package name
  includes `<repo>/devcontainer`, not just `devcontainer`.
- Architecture tags stay attached so untagged pruning cannot remove children
  still referenced by retained `:sha-<commit>` indexes. Default Buildx
  attestations are disabled to avoid separate untagged attestation children.
  Tagged history is intentionally not pruned; monitor storage.
- Workflow concurrency is scoped by image and ref, with no cancellation of an
  active build. GitHub may coalesce pending runs and does not promise FIFO.
  Do not reuse this concurrency group in the caller.

## Ownership and validation boundary

The consumer owns its build definition, Dockerfile, Docker ignore file, and
runtime `.devcontainer/devcontainer.json` referencing `:main`. Renovate's
shared preset must discover the build definition's features and exclude these
floating first-party images from pinning and updates. The runtime config's
existing lockfile-refresh rule does not cover build-definition lockfiles.

Local contract tests execute the embedded shell with stub CLIs and check
permissions, routing, tags, cache arguments, and input rejection. actionlint
checks workflow syntax; zizmor checks security. YAML anchors share the two build
jobs' matrix and steps while preserving separate token scopes. Also audit an
alias-expanded temporary copy because zizmor warns about anchor support.

Only a real caller can prove native builds, GHCR permissions/private visibility,
first-build cache misses, cache reuse, manifest platforms, pruning, and runtime
feature metadata. The pilot must verify those, plus dependency-volume seeding,
build-safe setup and ARM memory/disk headroom. Scheduled rebuilds can change the
image for the same source commit; `:sha-<commit>` is not a reproducible-build
or immutable-content guarantee.
