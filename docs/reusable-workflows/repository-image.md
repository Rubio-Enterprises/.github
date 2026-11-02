# Repository image builder

`repository-image.yml` is the generic builder for ADR-0036. It builds repositories
wired to the Standards Package (v2); Copier-era and unwired repositories fail
preparation with an instruction to adopt the package. Repository images are built and published for **linux/amd64 only**. Public `base` and
`base-ci` images remain multi-architecture for local arm64 builds.

Merging the x64-only workflow makes every caller's next image build x64-only.
Apple Silicon pulls of `:main` or `:sha-<commit>` fail with no matching manifest
until the fleet adds `.devcontainer/local/devcontainer.json` with a local build
recipe. This transition is owner-accepted (standards#848); forcing amd64 emulation
is not the local-build path.

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
run, and shares immutable references across its native amd64 jobs. Resolution
reads them anonymously through the GHCR registry API and checks every
digest-addressed manifest and config against its digest, so the resolving job
runs on the `small` route without a Docker daemon:

- `ghcr.io/rubio-enterprises/devcontainers/base:latest`
- `ghcr.io/rubio-enterprises/devcontainers/base-ci:latest`

The `base` and `base-ci` inputs can instead supply those same package names with
`@sha256:<64 lowercase hex>` for qualification. Both must contain exactly one
`linux/amd64` entry; other architectures are not fetched or validated by CI.
The amd64 base-ci config must label
`org.opencontainers.image.base.digest` with the resolved **base index digest**,
and preserve the compressed/uncompressed base layer prefix. Mismatches fail;
there is no fallback to another base or architecture emulation.

The central composite action obtains its assets through `GITHUB_ACTION_PATH`.
The caller cannot substitute a lookalike Dockerfile/script in its checkout.
`git archive HEAD` supplies committed source, excluding the working checkout's
Git database, untracked credentials and the installed package.

The exact v2 `.config/mise/conf.d/standards.toml` symlink to
`../../../.standards/node_modules/@rubio-enterprises/standards/mise/standards.toml`
is preserved in both the source and tool-input contexts, without dereferencing its
cold target. Other package wiring links are not substituted. The self-hosting `Rubio-Enterprises/standards`
repository instead preserves its exact `../../../mise/standards.toml` link. This requires
its own package manifest and regular from-tree mise/Lefthook sources; other callers
with that link still fail closed. Tracked `.standards/` payloads, Git
directories and Copier-era `.standards-config/` checkouts are rejected, as are
links into either standards location (including the retired `tombi.toml` Config
Stub) and links that escape the snapshot.

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
`CI=true mise run setup` after system tool installation. The `setup` task must be
build-safe; Linux cannot provide Xcode/Homebrew build steps.
The self-hosting standards repository's native `standards:install` packs its own
checkout, so its tool-input context includes the complete validated source rather
than only the usual tool inputs. Source changes invalidate that repository's tool
layer. No task is replaced: both tool installation and setup use its candidate
package/from-tree checks, while package prefix and npm cache remain tmpfs and all
layer audits remain unchanged.

### Build environment

Builds install tools with `mise install --system`, never fetch standards
in-container, and set `UV_LINK_MODE=copy`. The recipe keeps mutable
uv/npm/pnpm/Go/mise/Cargo caches under `/home/vscode/.cache/`. Rustup and
uv-managed Python SDK homes are `/home/vscode/.local/share/rustup` and
`/home/vscode/.local/share/uv/python`. Directories are created as vscode,
including intermediate data directories. The Dev Container CLI re-owns that home
when it remaps the remote user's UID/GID on Linux; fixed paths outside the home
would retain the build UID. Do not mount over the home or cache tree if baked
caches must remain available.

### Standards Package provisioning (v2)

V2 delivers the validated source through a deterministic Git tar archive,
bind-mounted read-only and extracted as vscode with `tar --no-same-owner` into a
vscode-owned workspace. Older bundled BuildKit versions can leave nested archive
directories root-owned with `ADD --chown`; extracting as the setup user avoids that
backend-dependent behavior without recursive ownership or permission changes.
The archive mount does not persist in an image layer. An application's `.dockerignore`
remains unchanged inside that archive/image, but cannot filter setup files from this different build.

Detection happens before loading consumer mise configuration. It requires the exact
package symlink and root `setup`/`standards:install` tasks, without Copier answers or
the Copier bootstrap file. Missing, mixed or incomplete wiring fails closed.

The preparation job resolves `latest` or the root `STANDARDS_VERSION` exact Release
Pin once to a version and SHA-512 integrity. Each native job uses npm to pack that
exact release with its existing repository token and verifies its SHA-512 integrity.
Only then does it read the single regular `package/image/Dockerfile` member into
job-local `package/Dockerfile`; it never extracts the package tree or follows archive
links. Duplicate, empty, oversized and non-UTF-8 recipes fail closed. Registry
cache/log state is removed at job end. The token stays outside Docker: only the
credential-free tarball is supplied as a BuildKit secret. No token build argument,
image environment or credential file is involved.

The v2 build selects `--target ci` from that package-owned recipe, with the same
`pins`/`expected` named contexts, `BASE`/`REPO` arguments, clean source archive and
`standards_package` secret mount. The recipe's `environment` stage supplies the
shared environment, paths and permissions. `.github` owns orchestration, not a
second v2 Dockerfile; there is no network recipe fetch or local fallback.

Releases before 2.1.0 lack the recipe and fail with an error naming the resolved
version and the remedy: remove the older root `[env].STANDARDS_VERSION` Release
Pin or raise it to 2.1.0+. An API inventory on 2026-11-02 checked all 112 active
organization repositories: 100 have image definitions, 98 are package-shaped and
none has an active older Release Pin in its root mise configuration or profiles.
The two remaining Copier-era image repositories, `pi-ai-accounts` and
`model-directory-service`, adopted the package the same day, which retired the
legacy Dockerfile, `standards-bootstrap` and the reader-App secret inputs.
Commented sample Release Pins are not active pins.

The package's `ci` target installs Node from the repository's runtime pin, installs the
package into tmpfs, then installs the tool pins. Only that package bootstrap uses
`mise run --skip-tools standards:install`: auto-installing consumer tools first
would require tools such as uv that the cold package has not provided yet.
The subsequent full tool installation and canonical setup are unchanged. Project setup runs unchanged as
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

The dev artifact's all-layer audit rejects package prefix/Git files, copied package
content and compressed tarballs, including copies hidden in dependency caches.
The CI artifact relies on exact rebase verification rather than a second audit.
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

Each amd64 job uses a job-local registry (loopback ephemeral port), builds the
dev image, and uses digest-pinned crane 0.22.1 to rebase onto the matching base-ci.
It compares compressed repository layer descriptors, uncompressed diff IDs,
architecture and full runtime config before/after. Both variants must
have manifest `org.opencontainers.image.base.digest` annotations matching the
resolved base index for dev and the resolved base-ci index for CI. Missing or
incorrect provenance fails the build. Rebase retains dev config;
base-ci ENV/USER/labels are not assumed to become the CI environment.

The local registry remains alive through scanning and publication. The audit pulls
the digest-addressed dev artifact with crane; Trivy scans that same immutable
registry reference. The CI variant relies on `validateRebase`'s exact repository
layer descriptors/diff IDs and runtime-config equality, plus `validateProvenance`
for both base annotations. Its only additional bytes come from the validated
public base-ci prefix, not another repository build. It is not audited or scanned
a second time; this does not claim to scan the runner base's additional layers.
After verification, audit, smoke tests and the scan pass, crane copies both exact manifests
to GHCR and verifies destination digest equality. Docker-loaded copies are only
for runtime smoke tests: classic Docker image storage can drop annotations
and reserialize the manifest when pushing. The workflow's
always-cleanup step removes the registry on success, failure or cancellation.

Before external push, the dev artifact is inspected across **all layers**,
including files hidden/deleted by later layers, for persisted standards paths,
links, exact package-only file hashes (files at least 128 bytes) and a
`STANDARDS_PIN` environment entry.
The package-root `package/LICENSE` is public legal metadata and does not supply a
file-content fingerprint. The complete package tarball and every other package
file remain fingerprinted; an image filename such as `LICENSE` grants no exemption.
The content comparison also excludes fingerprints already present in regular files
from the prepared `git archive` source snapshot. The builder passes that snapshot,
not the working checkout; untracked files, symlink targets and Git/standards
checkout metadata cannot supply exemptions. Standards path/link and environment checks
remain unconditional, even for source-matched content. Config/history is inspected
too. This catches direct bundle-only copies, not arbitrary transformations of
policy by trusted project setup; source/mount discipline is still necessary.
Reports never render matching contents. The CI smoke runs as
UID 1000 under no-new-privileges and checks the runner, mise/gh/jq, writable
cache/tool/runner paths and uv copy mode. Cache smoke seeds nested cache and
SDK-home metadata and checks append/create/rename/delete:

| Variant / UID | Coverage |
| --- | --- |
| dev / 1000 | Original vscode ownership, without UID migration; retained. |
| dev / 1001 | Dev Container CLI home-only UID/GID migration; retained. |
| CI / 1000 | Fixed runner identity on the different merged base-ci filesystem; retained because runner-base additions can affect cache paths despite repository-layer identity. |
| CI / 1001 | Omitted: Container Pool runners use UID 1000, do not run the CLI's UID migration, and dev / 1001 already tests that migration. |

Only the dev / 1001 identity migration runs as root; cache writes and project setup
run as vscode. This is not a test of arbitrary Feature
lifecycle commands: plain Container Pool runners do not interpret Dev Container
metadata. Runtime setup and Feature-specific behavior require caller qualification.
Docker/KVM/ARM project jobs stay on Machine Pools; macOS native jobs stay native.

Checksum-pinned Trivy 0.76.1 scans the dev artifact's filesystem and image
config/history once with no caller ignore
policy and no matched secret text. Public mise install trees, Rustup/uv-managed
SDK directories and Go/Bun/Cargo/package caches, including RubyGems/Bundler
packages and cached gem archives, use scan-root-anchored exclusions. Home
credentials/configuration, first-party source outside preserved dependency exclusions, and environment/history retain
the same severities. The exact scopes, preserved defaults and blind spots are
documented under [Secret checks](repository-image-secrets.md). No scan failure/scanner error
can proceed to either external push. A failed build blocks both moving indices
and pruning.

PR jobs have only contents/packages read; default-branch push/schedule/manual
jobs have package write. The reusable receives no secrets.
No privileged PR trigger is supported. All third-party actions are SHA-pinned.
Both packages use `:main` and `:sha-<commit>` as manifest lists containing exactly
one `linux/amd64` child. `imagetools create --prefer-index=true` explicitly wraps
the single child instead of copying a bare manifest (which could allow implicit
amd64 emulation on an arm64 host). Publication checks both indices' raw media
type and platform count. `:sha-<commit>-amd64` and `:main-amd64` remain single-image
tags; no arm64 tags or cache aliases are produced.
Inline BuildKit cache is exported and the dev `:main-amd64` alias is imported.
Cache misses are valid; a real second build must demonstrate reuse. Base refresh
does not fan out child builds. Per-repository/ref concurrency cancels superseded
runs. The build job itself advances both indices, after its verified push; there
is no second Docker-capable publication job. Registry writes across two packages are
not atomic. Retention keeps five untagged versions per package, never deleting
tagged history or index children; it runs after a build, not after an alias.

## Build Service path (canary)

The caller input `build-service: true` builds on the shared Build Service instead of a Machine Pool
runner, as mac-dev-playbook ADR 0057 and 0058 decide. The input exists for the canary and is removed
when the cutover deletes the Machine Pool path; the default is `false`, so callers that do not set
it are unchanged. **The caller's `repository-image` job must grant `id-token: write`**, which the
starter `standards.yml` does from Standards Release 3.0.0. GitHub rejects a called workflow whose job
asks for more permission than its caller grants, even when the job is skipped, so a caller without
the grant fails at startup. Merge this workflow only after the fleet carries the permission.

The `build-service` job runs on the image Scale Set (`RUNNERS.image`, falling back to the label
`linux-x64-image`). It has **no hosted fallback**: the daemon is reachable only from that pool. It is a
thin client with no Docker daemon, no root and no socket.

| Step | Where it runs |
| --- | --- |
| Resolve base pair and Standards Release, input key, skip or alias | `prepare` and `alias`, small route, unchanged |
| Client tools: Docker CLI, buildx, crane, step | Job, checksum-pinned, skipped for any already on `PATH` |
| Client certificate, builder, Docker configs | Job: `connect.sh` |
| Features image, recipe build (`ci` target) | Build Service, pushed to the staging registry |
| `crane` rebase, `verify` | Job, against the staging registry (metadata only) |
| Image audit | Job, on the layers above the public base only |
| Runtime and cache smoke checks | Build Service, as `--network=none` build stages (`smoke.Dockerfile`) |
| Trivy secret scan | Job, `--image-src remote` against the staging registry |
| Copy to GHCR, one-entry indices, aliases | Job, with its own `GITHUB_TOKEN` |
| Prune untagged versions | `prune`, small route, unchanged |

**Coordinates come from the image runner**, so this public workflow names no internal host:

| Variable | Meaning |
| --- | --- |
| `BUILD_SERVICE_BUILDER` | `tcp://host:port` of the BuildKit daemon |
| `BUILD_SERVICE_REGISTRY` | `host:port` of the staging registry (TLS, password) |
| `BUILD_SERVICE_REGISTRY_AUTH_FILE` | File with one line, `user:password` |
| `BUILD_SERVICE_CA_URL`, `BUILD_SERVICE_CA_ROOT` | Private CA API and the file holding its root certificate |
| `BUILD_SERVICE_SERVERNAME` | Optional TLS server name of the daemon (default: the endpoint's host) |
| `BUILD_SERVICE_AUDIENCE` | Optional OIDC audience (default `homelab-build-service`) |

**Identity.** The job requests a GitHub OIDC token for the audience and exchanges it with the Private
CA (`step ca certificate`) for an hour-long client certificate that names the repository. The CA's
template accepts only this workflow on the default branch. The exchange lives in one function,
`request_certificate` in `connect.sh`, so a change to the CA's provisioner changes one place. The
daemon trusts only that CA. The Private CA is on every image build's path: its outage stops image
builds.

**Credentials stay scoped.** buildx forwards its configured credentials to the daemon during a build,
so the job keeps two Docker configs under `$RUNNER_TEMP/build-service/`. `docker-build` holds only
the staging registry's credential and serves every build step. `docker-publish` adds GHCR and serves
only the copy and index steps, after the checks pass. The daemon never sees a GHCR token, which is
why the build does not use `--cache-from` or inline cache: the daemon's own cache replaces them. The
cleanup step deletes the certificate, key and configs on success, failure and cancellation.

**Failure handling.** Every contact with a daemon that rejects the client hangs at "waiting for
connection", so the bootstrap carries a 60 second timeout and one retry. A restarting daemon fails its
in-flight clients with `Unavailable`; its cache survives, so each build step retries once after 20
seconds. Anything else fails the build. There is no fallback to the Machine Pool: a builder outage
stops image builds until it is fixed.

**Checks that moved.**

- The audit reads only the dev image's layers above the public base. `verify` has already proven the
  base layers byte-identical to the published base, which holds no standards content, and skipping them
  saves 40 to 70 percent of the download. `audit.py` accepts a directory of `config.json` and ordered
  `layer-*` blobs besides a saved-image archive.
- The smoke checks run as stages of `smoke.Dockerfile`: the CI image's runtime check and the dev
  image's cache check for UID 1000 and 1001. `--no-cache-filter` makes every stage execute on every
  build. BuildKit has no equivalent of `--memory 1g --pids-limit 512`; the daemon's own cgroup bounds
  the checks.
- **The per-build CI image cache check (CI / UID 1000) is gone.** `verify` proves the repository layers
  and config identical to the dev image's, so only the runner-base additions in `base-ci` can change
  cache paths. It is meant to run **once per base pair release in `Rubio-Enterprises/devcontainers`**:
  after publishing a new `base` and `base-ci`, build a reference repository image (the recipe's
  `environment` stage over a small fixture), rebase it onto the new `base-ci`, and run
  `cache-smoke.sh 1000` against the result. Until that check exists, `base-ci` releases are unchecked
  for it.

**Staging.** Builds push `features`, `dev` and `ci` to `<registry>/<owner>/<repo>/…:run-<run>-<attempt>`.
The registry is disposable; nothing prunes it from here.

**Features image.** The Dev Container CLI builds Features with `--push --omit-syntax-directive` and
`BUILDX_NO_DEFAULT_ATTESTATIONS=1`. Without attestations the pushed tag is one manifest, so `crane
digest` is the image digest and the recipe's `BASE` is stable. Without a reachable Docker engine the
CLI otherwise writes a syntax directive that makes the daemon fetch the `docker/dockerfile:1.4`
frontend from Docker Hub on every build.

**Canary and rollout notes.**

- The called workflow is part of the certificate's claims (`job_workflow_ref`). A canary that calls
  this workflow from a branch must have that ref allowed by the Private CA template, and the
  workflow's `Rubio-Enterprises/.github/actions/repository-image@main` references must point at the
  same branch.
- Any change to a file in `actions/repository-image/` changes every repository's image input key, so
  each repository rebuilds once at its next push. Merging this workflow before the cutover therefore
  rebuilds the fleet on the Machine Pool.
- For a repository with extra Features, build the Features image twice on the real builder and require
  the same digest before the cutover.

## Image input key

A default-branch push of a v2 repository builds only when the image's inputs
changed. The small-route `prepare` job computes an **image input key** for the
commit and reads the published `:main` pair through the registry API (no Docker
daemon, `packages: read`). The pair stands in for the commit only when all hold:

- both `:main` tags are one-entry `linux/amd64` manifest lists;
- both image configs carry `org.rubio-enterprises.image.input-key` equal to the
  commit's key (the CI image inherits the dev config, which the rebase check
  keeps identical);
- the dev and CI manifests' `org.opencontainers.image.base.digest` annotations
  equal the resolved base and base-ci indices;
- `org.rubio-enterprises.image.built-at` is at most seven days old, so floating
  inputs (apt packages a Feature installs, fuzzy tool versions) refresh weekly.

Then the small-route `alias` job (`packages: write`) installs checksum-pinned crane
0.22.1 and tags the existing index as `:sha-<commit>` and its child as
`:sha-<commit>-amd64`, by digest, and verifies both tags and the one-entry
index. `:main` and `:main-amd64` do not move, and no Docker-capable job runs.
Anything else, including an unreadable registry answer, builds as before, and the
build records the key and build time as labels and verifies them before
publication. Manual (`workflow_dispatch`) runs always build. Pull requests keep
the `validate` path.

### What the key covers

The key is a SHA-256 over: the key schema; a digest of every file in this action
(the recipe orchestration, `build.sh`, preparation and the key rules) and the Dev
Container CLI version; the repository and image names; the resolved base and
base-ci digests; the extra-Features definition and its `devcontainer-lock.json`;
the image-relevant Standards Package content; and tracked files, by Git object ID,
chosen by the repository's rule:

- **inputs**: the tool pins that preparation copies (`.mise*.toml`, `mise*.toml`,
  `mise.lock`, `.config/`), other mise configuration and file tasks, version files
  (`.tool-versions`, `.node-version`, `.nvmrc`, `.python-version`,
  `rust-toolchain.toml`, …), Lefthook configuration, and dependency manifests,
  lockfiles, registry configuration and patches anywhere in the tree (npm, pnpm,
  Yarn, Bun, uv/pip/Poetry, Cargo and `.cargo/`, Go modules, Bundler), plus
  `.gitattributes`, `.gitmodules` and gitlinks.
- **tree**: every tracked file.

Two normalizations make release-please commits match: the root `package.json`
without its top-level `version`, and the root `package-lock.json` (or
`npm-shrinkwrap.json`) without its top-level and root-package `version`. Nested
manifests are not normalized. A `.gitattributes` `export-subst` adds the commit SHA,
because `git archive` then writes commit-specific bytes.

The Standards Package **version** is not an input. The CI target reads only
`image/Dockerfile` and, through the package wiring symlink, `mise/standards.toml`
(tool pins, tasks, hooks and environment); `standards:install` and `lefthook install`
write only to the build's tmpfs `.standards/` and `.git/`. The inputs rule therefore
keys on `image/Dockerfile`, `mise/**` and `package.json` without `version`. A
release that changes only checks configuration, Lefthook, starter files or skills
(2.1.0 → 2.1.1 → 2.2.0 changed none of these) does not rebuild the fleet. The tree
rule keys on every package file, because arbitrary setup could read any of them.

### Which rule a repository gets

A repository gets the inputs rule only when `mise run setup` provably reads nothing
else. The checks fail closed: any doubt selects the tree rule, and the prepare log
lists each reason.

- `setup` and `standards:install` match the starter exactly, and the package
  defines no `setup`, `setup:*` or `standards:install` task.
- Every task reached from `setup:*` uses only `description`, `run`, `dir` and
  dependencies, and every command (split on `&&`) is one of `npm ci`,
  `npm --prefix <dir> ci`, `pnpm [--dir <dir>] install --frozen-lockfile`,
  `bun install --frozen-lockfile`, `uv sync --frozen|--locked`,
  `uv venv … .venv` with `uv pip install --python .venv -r <file>`, and
  `cargo fetch --locked`, with the exact flags in `key.mjs`. Any other command
  (code generation, `go list`, `bundle install`, scripts) selects the tree.
- No file task named `setup*`, task includes, `[env]` directives other than
  `_.path`, install hooks, `exec()`/`read_file()` templates, or `path:`/`ref:` or
  `postinstall` tools in any mise configuration.
- JavaScript installs: no tracked `package.json` declares a lifecycle script
  (`preinstall`, `install`, `postinstall`, `prepare`, …) unless every install uses
  `--ignore-scripts`; no `file:`, `link:`, `portal:` or path dependency; no
  `.pnpmfile`. Dependency install scripts must come from a reviewed list of
  packages that only touch their own directory (`ALLOWED_INSTALL_SCRIPTS`): npm's
  `hasInstallScript` lockfile entries, pnpm 10+'s `allowBuilds` or
  `onlyBuiltDependencies`, and Bun's `trustedDependencies` plus locked packages on
  Bun's default trusted list (`bun-default-trusted.txt`, a verbatim copy from
  `bun-v1.4.4`). Older pnpm selects the tree.
- `uv sync`: no tracked `pyproject.toml` is installable (`[build-system]` or
  `tool.uv.package = true`) or has path, workspace or editable sources.
- `uv pip install -r`: requirement files contain no editable or local entries.

On 2026-11-02, 78 of the 100 image repositories get the inputs rule. The tree rule
covers 20 repositories whose setup installs a local Python package (9), runs
other programs (`go list`, `bundle install`, scripts, Playwright, Prisma), has a
project lifecycle script, a local tarball dependency or an unreviewed install
script (`yorkie`), plus the self-hosting standards repository. The two other
repositories, Copier-era at the time, have since adopted the package. Applying the
key to every first-parent main commit, with bases and package fixed, skips 60% of builds from 2026-10-16 to 10-28
(1,658 of 2,752) and 44% during the 10-29 to 11-02 migration wave.

### Baked source

An aliased `:sha-<commit>` carries the source snapshot of the commit that was
built, not of `<commit>`. No consumer reads that snapshot: the Dev Container CLI,
the hosted [project command](../project-command.md) and Sprites mount their own
checkout over `/workspaces/<repo>`; CI Image runners check out into the runner work
directory; Claude Code cloud sessions do not use the image. Use the checkout, not
the image, when exact source matters. To remove content from published images,
such as a leaked credential, run the caller's workflow manually, which always
rebuilds.

Add a package to `ALLOWED_INSTALL_SCRIPTS` only after reading its install script.
Changing any file in this action changes every key, so the fleet rebuilds once,
at each repository's next push.

## Caller

The starter `standards.yml` calls this reusable on pushes to `main` and manual
runs when `.github/.devcontainer/devcontainer.json` exists. Do not add a
duplicate caller concurrency group.

The reusable takes no secrets; the caller grants package access through its
existing `GITHUB_TOKEN`. Definitions with Dockerfiles, baseline Features or cache
overrides are rejected rather than adapted with token-bearing build arguments.
Feature-specific lifecycle behavior still needs caller qualification.

The legacy `devcontainer-image.yml` reusable is retired. On 2026-11-02 an API
inventory enumerated all 112 non-archived organization repositories, pinned each
nonempty default branch to a commit, listed `.github/workflows/` through the
Contents API and read all 347 workflow blobs. There were zero references to
`devcontainer-image.yml@` and zero API errors; code search was not used as the
caller-free proof. Historical/nondefault branches must use the supported reusable
when revived.

## Validation boundary

Local contracts exercise base pairing and mismatch rejection, exact rebase
layer/config equality, clean source preparation, definition rejection, the
standards-layer audit, single dev fail-closed scan, amd64-only routing,
one-entry index publication, the image input key's rules and normalizations, the
published-pair comparison and the alias tags. actionlint checks the
workflow; shellcheck checks build orchestration. Native builds, first/second cache
behavior, registry association/visibility and real runner lifecycle remain
qualification gates, not claims established by fixtures. No base publication is
required to run the fixture suite.

The frozen `tests/fixtures/frozen-repository-image-v2.Dockerfile` is a test-only
compatibility baseline from `.github` commit `2df77da`; no action reads it. Public
PR tests remain independent of private-package credentials. Synthetic tarballs
test safe extraction and both build branches. Set `STANDARDS_IMAGE_PACKAGE` and
`STANDARDS_IMAGE_INTEGRITY` to a verified release tarball and its SHA-512 identity
to compare the real package recipe's CI environment, user, workspace and setup
commands with the baseline. Private x64 qualification also compares the built
runtime and installed tool inventory. **Follow-up:** move recipe-behavior and
source-permission tests into `standards` beside `image/Dockerfile`, then retire
the frozen baseline; it must not become a maintained alternative recipe.
