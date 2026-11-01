# Repository image builder

`repository-image.yml` is the generic builder for ADR-0036. It supports both the
Standards Package (v2) and Copier-era consumers during migration. The
`devcontainer-image.yml` entry point delegates v2 generic definitions here and
retains its original Dockerfile build path for legacy consumers.

## Contract

The caller carries `.github/.devcontainer/devcontainer.json` with only a name
and optional extra, versioned OCI Features:

```json
{
  "name": "repository build",
  "features": {
    "ghcr.io/devcontainers-extra/features/apt-packages:1.0.6": {
      "packages": "python3"
    }
  }
}
```

There is no repository Dockerfile, build command input, baseline Feature,
container environment override or local Feature. The public base already owns
baseline. The pinned Dev Container CLI (0.89.0) parses JSONC and applies extra
Features **before** tool/dependency setup. Its feature metadata stays on the
Repository Image. A neighboring `devcontainer-lock.json` is used when present.

The reusable defaults to these floating public indices, resolves them once per
run, and shares immutable references across native amd64/arm64 jobs. Resolution
reads them anonymously through the GHCR registry API and checks every
digest-addressed manifest and config against its digest, so the resolving job
runs on the `small` route without a Docker daemon:

- `ghcr.io/rubio-enterprises/devcontainers/base:latest`
- `ghcr.io/rubio-enterprises/devcontainers/base-ci:latest`

The `base` and `base-ci` inputs can instead supply those same package names with
`@sha256:<64 lowercase hex>` for qualification. Both must contain exactly one
Linux image per architecture. Each base-ci config must label
`org.opencontainers.image.base.digest` with the resolved **base index digest**,
and preserve the compressed/uncompressed base layer prefix. Mismatches fail;
there is no fallback to another base or architecture emulation.

The central composite action obtains its assets through `GITHUB_ACTION_PATH`.
The caller cannot substitute a lookalike Dockerfile/script in its checkout.
`git archive HEAD` supplies committed source, excluding the working checkout's
Git database, untracked credentials and provisioned standards. The archived
snapshot omits only these exact template Config Stub symlink path/target pairs,
without following them:

| Source path | Exact link target |
| --- | --- |
| `tombi.toml` | `.standards-config/config/tombi.toml` |

The exact v2 `.config/mise/conf.d/standards.toml` symlink to
`../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml`
is preserved in both the source and tool-input contexts, without dereferencing its
cold target. Other package wiring links are not substituted. Tracked `.standards/`
payloads are rejected alongside Git and legacy standards checkouts.

Runtime checkouts and workspace bind mounts retain those stubs. A regular,
repository-owned `tombi.toml` remains in the snapshot. Other links into standards,
escaping symlinks and tracked Git or standards directories still fail closed;
this is not a path-wide exclusion or an all-layer audit exemption. A future
template stub needs an explicit reviewed pair here and in preparation.

Submodules qualify for **omission only** when every committed gitlink has one
unique matching path in the committed `.gitmodules`, with an exact
credential-free `https://github.com/Rubio-Enterprises/<name>` URL (optional `.git`
suffix). Git's native config parser reads that committed blob without includes;
working-checkout overrides cannot qualify a third-party URL. Third-party, SSH,
relative, credential-bearing, malformed, duplicate or mismatched declarations
fail closed. The archived `.gitmodules` must be a regular file. Each gitlink
must remain the empty, non-symlink directory emitted by `git archive`; nonempty
or missing directories fail. Notices contain only the escaped path and pinned
commit SHA, never the URL. Preparation never fetches submodule content or uses
extra credentials. This URL qualification does not establish remote visibility,
availability or commit reachability.

Repository setup must work without submodule content, as default CI checkouts
already do. A setup needing that content requires a separate design. Service
packaging with recursive submodule checkout is independent and unchanged;
workspace bind mounts retain any locally initialized submodules.

A separate pin-sensitive context contains top-level mise TOML/lock files, `package.json`
and `.config/`; source-only changes do not invalidate that tool context. Projects
with other tool-input paths need qualification before adoption, not a second
Dockerfile. The complete source and language lockfiles feed ordinary
`CI=true mise run setup` after system tool installation. Repositories without
mise config skip tool/project setup. A mise-bearing repository must expose a
build-safe `setup` task; Linux cannot provide Xcode/Homebrew build steps.

### Copier-era provisioning

`standards-bootstrap` independently provisions a clean full-history checkout and
exports `STANDARDS_PIN` before preparation. The build mounts standards read-only
as a named BuildKit context for the tool and setup steps. A separate read-only
context supplies the expected pin, avoiding pin build arguments in image history.
Neither context is copied. No standards credential reaches Docker. Builds install
tools with `mise install --system`, never fetch standards in-container, and set
`UV_LINK_MODE=copy`. Both generic builders keep mutable uv/npm/pnpm/Go/mise/Cargo
caches under `/home/vscode/.cache/`. Rustup and uv-managed Python SDK homes are
`/home/vscode/.local/share/rustup` and `/home/vscode/.local/share/uv/python`.
Directories are created as vscode, including intermediate data directories.
The Dev Container CLI re-owns that home when it remaps the remote user's UID/GID
on Linux; fixed paths outside the home would retain the build UID. Do not mount
over the home or cache tree if baked caches must remain available. Current standards and a
validated runtime pin must be provisioned again before runtime setup.

### Standards Package provisioning (v2)

V2 delivers the validated source through a deterministic Git tar archive and
Docker's native `ADD` extraction. An application's `.dockerignore` remains unchanged
inside that archive/image, but cannot filter setup files from this different build.
Only the exact stub paths already omitted during preparation are excluded by literal
Git pathspecs; source, package-absence and all-layer scan guards remain unchanged.
The generic legacy path retains its existing local named source context.

Detection happens before loading consumer mise configuration. V2 requires the exact
package symlink and root `setup`/`standards:install` tasks, without answers or the
legacy bootstrap file. Mixed or incomplete wiring fails closed. Copier consumers
retain their bootstrap/pin/readiness path. Repositories without mise retain the
source-only build path and its legacy provisioning.

The preparation job resolves `latest` or the root `STANDARDS_VERSION` exact Release
Pin once to a version and SHA-512 integrity. Each native job uses npm to pack that
exact release with its existing repository token, verifies the tarball, and removes
job-local registry cache/log state. The token stays outside Docker: only the
credential-free tarball is supplied as a BuildKit secret. No token build argument,
image environment or credential file is involved.

The v2 Dockerfile installs Node from the repository's runtime pin, installs the
package into tmpfs, then installs the tool pins. Project setup runs unchanged as
`mise run setup`, with build-local `STANDARDS_VERSION=file:/run/standards.tgz`.
A transient `mise.repository-image.toml` profile supplies that override because
root mise `[env]` values take precedence over inherited environment variables.
That profile name is reserved and rejected in committed v2 source. The file is
bind-mounted only during setup, not copied. This is the same local-candidate
package mechanism as the Standards Release Check, not a permanent Release Pin. Package prefix, npm cache/logs and setup's temporary Git
metadata are tmpfs mounts; the package tarball remains a secret mount. Setup runs
as vscode. The source snapshot has no Git DB, so temporary Git state lets its
ordinary `lefthook install` run without bypassing hooks. Neither package contents
nor temporary Git state become image layers. Npm-only download caches from that
RUN are deliberately not baked; installed project dependencies and other caches
remain. A separate package-identity context invalidates the cache when the release
changes, because BuildKit secret bytes alone do not invalidate it.

Both artifacts' all-layer audits reject package prefix/Git files, copied package
content and compressed tarballs, including copies hidden in dependency caches.
Only the exact runtime package-wiring symlink is allowed. Trivy's broad dependency
exclusions do not supply this package-absence guarantee.

At runtime, the real checkout runs `mise run setup` with `NODE_AUTH_TOKEN` (a Dev
Container uses `remoteEnv.NODE_AUTH_TOKEN`). There is no standards Git checkout or
pin to provision, and the image has no build-time Release Pin. A fresh offline
workspace cannot install the internal package; warm workspace/npm state is the
package model's offline path, not an image-baked fallback. Setup always reconciles
current standards and dependencies independently of the image build.

## Artifacts and safety gates

Default names match the inventory contract:

| Artifact | Package |
| --- | --- |
| Repository Image | `ghcr.io/<owner>/<repo>/devcontainer` |
| CI Image | `ghcr.io/<owner>/<repo>/devcontainer-ci` |

`image-name` optionally changes the first untagged GHCR name within the caller's
owner; the second is that name plus `-ci`. The source label points at the caller.
Private source may be baked into private repository images; provisioned standards
may not. A tracked source file remains repository source even when its complete
contents match a bundle file, such as the rendered `treefmt.toml` bridge.
The workflow never changes GHCR visibility, package links or access grants.
Verify both packages' visibility and same-repository token access before adoption.

Each native job uses a job-local registry (loopback ephemeral port), builds the
dev image, and uses digest-pinned crane 0.22.1 to rebase onto the matching base-ci.
It compares compressed repository layer descriptors, uncompressed diff IDs,
architecture and full runtime config before/after. Rebase retains dev config;
base-ci ENV/USER/labels are not assumed to become the CI environment.

Before external push, both saved artifacts are inspected across **all layers**,
including files hidden/deleted by later layers, for persisted standards paths,
links, exact bundle-only file hashes (files at least 128 bytes) and the build pin.
The content comparison excludes fingerprints already present in regular files
from the prepared `git archive` source snapshot. The builder passes that snapshot,
not the working checkout; untracked files, symlink targets and Git/standards
checkout metadata cannot supply exemptions. Standards paths/links and pin checks
remain unconditional, even for source-matched content. Config/history is inspected
too. This catches direct bundle-only copies, not arbitrary transformations of
policy by trusted project setup; source/mount discipline is still necessary.
Reports never render matching contents. The CI smoke runs as
UID 1000 under no-new-privileges and checks the runner, mise/gh/jq, writable
cache/tool/runner paths and uv copy mode. Both dev and CI artifacts also seed
nested cache and SDK-home metadata as UID 1000 and verify append/create/rename/delete
as UID 1000 and, in a separate disposable container, UID/GID 1001 after the CLI's
home-only ownership migration. Only that test's identity migration runs as root;
cache writes and project setup run as vscode. This is not a test of arbitrary Feature
lifecycle commands: plain Container Pool runners do not interpret Dev Container
metadata. Runtime setup and Feature-specific behavior require caller qualification.
Docker/KVM/ARM project jobs stay on Machine Pools; macOS native jobs stay native.

The checksum-pinned Trivy 0.76.1 policy is identical in both reusables, scanning
both local artifacts' filesystem and image config/history with no caller ignore
policy and no matched secret text. Public mise install trees, Rustup/uv-managed
SDK directories and Go/Bun/Cargo/package caches, including RubyGems/Bundler
packages and cached gem archives, use scan-root-anchored exclusions. Home
credentials/configuration, first-party source outside preserved dependency exclusions, and environment/history retain
the same severities. The exact scopes, preserved defaults and blind spots are
documented in [the legacy reusable](devcontainer-image.md#secret-checks). No
scan failure/scanner error can proceed to external push. A failed native job
blocks both multiarch moving tags and pruning. A successful peer may already
have pushed its individually scanned architecture tags.

PR jobs have only contents/packages read; default-branch push/schedule/manual
jobs have package write. Fork PRs receive no standards secrets and fail closed.
No privileged PR trigger is supported. All third-party actions are SHA-pinned.
Both packages use `:main`, `:sha-<commit>`, and corresponding architecture tags.
Inline BuildKit cache is exported and the dev `:main-<arch>` alias is imported.
Cache misses are valid; a real second build must demonstrate reuse. Base refresh
does not fan out child builds. Per-repository/ref concurrency cancels superseded
runs. The merge job advances both indices only after both architectures pass;
registry writes across two packages are not atomic. Retention keeps five untagged
versions per package, never deleting tagged history or index children.

## Caller and migration

Consumer migration is coordinated separately. A legacy caller uses the actual default branch
(`master` where applicable), all setup/tool/dependency input paths and a
repository-specific staggered weekly cron. There is no base-change trigger.
For example (choose a distinct cron slot, do not copy one slot fleet-wide):

```yaml
name: repository image
"on":
  push:
    branches: [main]
    paths: &inputs
      - .github/.devcontainer/**
      - .github/workflows/repository-image.yml
      - .mise*.toml
      - mise.toml
      - mise.lock
      - .config/**
      - scripts/**
      - "**/package.json"
      - "**/package-lock.json"
      - "**/pnpm-lock.yaml"
      - "**/pnpm-workspace.yaml"
      - "**/pyproject.toml"
      - "**/uv.lock"
      - "**/Cargo.toml"
      - "**/Cargo.lock"
      - "**/go.mod"
      - "**/go.sum"
      - "**/Gemfile*"
      - "**/bun.lock*"
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
    uses: Rubio-Enterprises/.github/.github/workflows/repository-image.yml@main
    secrets:
      STANDARDS_READER_APP_ID: ${{ secrets.STANDARDS_READER_APP_ID }}
      STANDARDS_READER_APP_PRIVATE_KEY:
        ${{ secrets.STANDARDS_READER_APP_PRIVATE_KEY }}
```

Do not add a duplicate caller concurrency group. Source is a workspace snapshot;
source-only changes need not rebuild unless setup consumes them.

For v2 callers, no reader-App secrets are needed; the caller grants package access
through its existing `GITHUB_TOKEN`. The copy-once v2 workflow can call this reusable
when the generic build-definition file exists. V2 definitions with Dockerfiles are
rejected rather than adapted with token-bearing build arguments.

**For unmigrated bookmark-scraper and karakeep revisions**, their
current build definitions include Dockerfiles, baseline Features and cache paths;
the new path deliberately rejects that shape. T1 must migrate definitions/locks,
build-safe setup and runtime cache mounts together, then point callers at this
reusable only after the bases exist and both native images pass qualification.
Karakeep's Python prerequisite belongs in its extra apt Feature (installed before
setup). Bookmark-scraper's desktop/Docker Features retain runtime metadata and
need their own lifecycle qualification. Removing old Dockerfiles/ignore files and
retiring the legacy reusable are separate, coordinated changes. No implicit
fallback, publication, visibility mutation, workflow dispatch or consumer change
is part of introducing this unused reusable.

## Validation boundary

Local contracts exercise base pairing and mismatch rejection, exact rebase
layer/config equality, clean source preparation, definition rejection, the
standards-layer audit and both-artifact fail-closed scans. actionlint checks the
workflow; shellcheck checks build orchestration. Native builds, first/second cache
behavior, registry association/visibility and real runner lifecycle remain
qualification gates, not claims established by fixtures. No base publication is
required to run the fixture suite.
