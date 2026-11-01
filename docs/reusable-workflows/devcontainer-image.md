# Dev Container image reusable workflow contract

## Interface

`.github/workflows/devcontainer-image.yml` accepts `workflow_call` only.

It detects the consumer's standards wiring before build selection. V2 consumers
with a generic name/features definition delegate to
[`repository-image.yml`](repository-image.md), including its Repository/CI Image
pair, transient package provisioning, layer audits and publication gates. No
reader-App secret or STANDARDS_PIN is used on that path. An unconverted v2
Dockerfile definition fails the generic contract and must be migrated by its
owner; the workflow never injects a package token into arbitrary Dockerfiles.

The remaining Dockerfile contract below applies to Copier-era consumers only.
Their existing build, pin, scans and publication behaviour is unchanged.

| Input | Type | Default |
| --- | --- | --- |
| `config` | string | `.github/.devcontainer/devcontainer.json` |
| `image-name` | string | `ghcr.io/<owner>/<repo>/devcontainer`, lowercased |

Image overrides must be untagged GHCR paths under the caller's owner. Config
paths must be repository-relative without parent traversal. There are no workflow
outputs. The pilot consumer is `bookmark-scraper`.

| Optional secret | Required when |
| --- | --- |
| `STANDARDS_READER_APP_ID` | Every Copier-era full consumer |
| `STANDARDS_READER_APP_PRIVATE_KEY` | Every Copier-era full consumer |

Callers explicitly forward both secrets as shown below; missing credentials
fail before building. Fork PRs do not receive them and fail closed.

The caller grants `contents: read` and `packages: write` to the calling job;
fork PRs retain GitHub's read-only token. Do not use `secrets: inherit` or
`pull_request_target`. The reusable gives PR build jobs only `contents: read`
and `packages: read`, publishing build jobs `contents: read` and
`packages: write`, and merge/prune jobs only `packages: write`.

## Caller

Call the reusable at `@main`. It is not an injected Gate Family and needs no
GitHub settings changes. The shared Renovate preset leaves first-party
`.github` references alone.

A caller owns its triggers, for example:

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
    uses: Rubio-Enterprises/.github/.github/workflows/devcontainer-image.yml@main
```

Use the repository's actual default branch and complete build inputs (including
setup tasks, other language manifests/lockfiles, and any files they consume).
Source-only changes need not rebuild: the workspace mount hides the image's
source snapshot.

## Native shared config

Native callers add only these named secrets to the image job (never
`secrets: inherit`):

```yaml
    secrets:
      STANDARDS_READER_APP_ID: ${{ secrets.STANDARDS_READER_APP_ID }}
      STANDARDS_READER_APP_PRIVATE_KEY:
        ${{ secrets.STANDARDS_READER_APP_PRIVATE_KEY }}
```

After checkout, `standards-bootstrap@main` resolves standards `legacy/copier` once,
provisions a clean full-history checkout at that SHA in `.standards-config/`,
and exports the validated lowercase 40-hex `STANDARDS_PIN` for later steps.
`bootstrap-parts: repos` skips the caller's toolchain installation and caching;
the Dockerfile installs it. Origin is the credential-free
`https://github.com/Rubio-Enterprises/standards.git`. No standards token route is
installed; legacy standards routes are removed before verification. No standards
credential is passed to Docker as an argument, environment variable or secret.

Use mise **2026.10.19 or later** and baseline **3.0.1 or later**. The build
definition uses the CLI's native argument substitution and named build context:

```json
{
  "build": {
    "dockerfile": "Dockerfile",
    "context": "../..",
    "args": { "STANDARDS_PIN": "${localEnv:STANDARDS_PIN}" },
    "options": [
      "--build-context",
      "standards=${localWorkspaceFolder}/.standards-config"
    ]
  }
}
```

The reusable rejects an absent, empty or malformed pin before building. Its
Docker check also requires the resolved build argument to match that pin and
forwards `build.options` without shell evaluation, matching CLI 0.89.0.
The CLI has no `build --build-arg` flag; `build.args` is the supported interface.

The Dockerfile declares `ARG STANDARDS_PIN` (not `ENV`) and mounts the named
context read-only during toolchain installation and project setup. For example,
with the caller's own manifests/config already staged in `/tmp/mise-pins`:

```dockerfile
ARG STANDARDS_PIN
RUN --mount=type=bind,from=standards,target=/tmp/mise-pins/.standards-config \
    cd /tmp/mise-pins && bash -euc '[[ "$STANDARDS_PIN" =~ ^[0-9a-f]{40}$ ]]' \
    && MISE_TRUSTED_CONFIG_PATHS=/tmp/mise-pins \
       mise bootstrap repos status --missing \
    && MISE_TRUSTED_CONFIG_PATHS=/tmp/mise-pins mise install --system
```

After copying the repository, mount the same context at its workspace's
`.standards-config/`, run `mise bootstrap repos status --missing` before
`CI=true mise run setup`, and retain the ordinary setup tasks. When setup runs
as another user, Git needs that mount's exact path in `safe.directory` because
BuildKit presents the checkout as root-owned. Never remove the mounted
directory during a RUN; it is read-only and disappears when that RUN ends.
The matching pin makes bootstrap/status/repeated setup use local Git state
without standards transport. Empty pins must fail, not silently omit a repo.

Exclude the whole checkout from both the Dockerfile-specific ignore file and
the root fallback used by generated feature Dockerfiles:

```dockerignore
.standards-config
**/.git
```

The named context bypasses those source-context exclusions only for the explicit
bind mounts. Never COPY the checkout, its `config/` subtree or its Git database
into an image layer. Keep both architecture secret scans enabled; scanning
alone does not prove absence of the private bundle. The full checkout and pin
must be provisioned again for runtime setup; the image does not supply them at runtime.

Standards changes do not push a consumer commit or fan out image rebuilds.
The caller's weekly schedule or manual dispatch refreshes the image. Runtime
workspace setup reconciles dependencies independently; trusted runtime
provisioning must supply its own checkout and matching pin.

## Build and publication

- Pull requests check the Dockerfile, build and secret-scan both native
  architectures without pushing, merging, or pruning. Only `push`, `schedule`,
  and `workflow_dispatch` on the default branch publish. Dispatches on other
  branches and all other events do nothing.
- amd64 uses `RUNNERS.docker` (hosted fallback `ubuntu-latest`); arm64 uses
  `RUNNERS.arm` (hosted fallback `ubuntu-24.04-arm`). Docker's architecture
  is checked before building, so a misrouted daemon fails instead of emulating.
  The ARM pool's Tart Ubuntu runner template supports Docker; its existing image
  maintenance pulls images inside that guest. Public consumers cannot see the
  private route map and use the hosted fallbacks. No QEMU is installed.
- The official CLI is installed as `@devcontainers/cli@0.89.0` under
  `RUNNER_TEMP`, with npm lifecycle scripts disabled. The direct CLI allows
  an exact version pin and the OCI source label; `devcontainers/ci` installs
  a floating `@devcontainers/cli@0` and has no label input. Image setup belongs
  in the caller's Dockerfile; no lifecycle commands run.
- Each architecture imports cache from `:main-amd64` or `:main-arm64`,
  builds into the runner's local Docker with `--output type=docker`, scans that
  local image, then pushes `:sha-<commit>-amd64` or `:sha-<commit>-arm64` only
  after its scan succeeds. The build command never receives `--push`.
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

## Secret checks

Both architecture jobs run the same fail-closed steps on PRs and publications:

1. The pinned Dev Container CLI's `read-configuration` parses JSONC and resolves
   configuration substitutions; no handwritten JSONC parser is needed. Dockerfile
   and context come from `build.dockerfile` and `build.context`, both resolved
   relative to the config file, not the workspace. Context defaults to that
   directory. Build arguments and target are forwarded without shell evaluation.
   Definitions without `build.dockerfile` skip only this check, not the image scan.
2. `docker build --check --platform linux/<arch> --file <dockerfile> <context>`
   validates without executing the Dockerfile's build steps. It includes
   `SecretsUsedInArgOrEnv`. Docker documents that **`--check` exits nonzero for
   violations**, unlike ordinary builds' exit-0 warnings. The Node subprocess
   propagates that exit status (including launch errors), and Bash fails the
   step. No warning-text parsing, ignored status, or separate build is involved.
   Build checks require Buildx >= 0.15 and a supporting Dockerfile frontend
   (introduced in Dockerfile 1.8); unsupported tooling fails rather than skipping.
3. Trivy scans the built local tag with `--image-src docker --scanners secret
   --image-config-scanners secret --exit-code 1`; it cannot silently fall back
   to a remote image. Both filesystem contents and image configuration (including
   environment variables and build history) are checked. Scanner errors also fail
   the step. The 15-minute timeout fails closed. The log records scan seconds and
   status per architecture. No vulnerability database is needed for this
   secret-only scan; telemetry and version-update checks are disabled.
4. The report uses Trivy's native Go template output and prints **only file/image
   config location, line range, rule ID and severity**. It never renders matched
   content or code snippets, even partially redacted ones. No JSON/SARIF report
   containing secret values is written or uploaded. Do not enable debug logging.
5. Only a subsequent successful-step-only `docker push` publishes the scanned
   tag, and only on the allowed default-branch events. A failed architecture
   blocks the multi-architecture merge and pruning. The other architecture may
   already have pushed its independently scanned tag; it cannot advance `:main`.

### Trivy installation and exclusions

Trivy **0.76.1** is downloaded directly from its official GitHub release, with
separate Linux amd64/arm64 SHA-256 hashes committed in the workflow. Both hashes
were compared with the release asset API digests and published checksum file.
`sha256sum --check --strict` must succeed **before** extracting the `trivy` binary;
no downloaded installer script or action runs. A rewritten tag/replaced archive
cannot change the accepted bytes. Updating the version requires reviewing and
updating both hashes together; a floating checksum download is not a trust pin.
Review the copied default skip list and its contract-test snapshot against each
new Trivy release too.
This deliberately avoids the mutable Trivy action/setup tags implicated in the
[March 2026 incident](https://github.com/aquasecurity/trivy/discussions/10425).
Checksums establish byte integrity, not immunity to a malicious upstream release.

Caller `trivy.yaml`, `.trivyignore` and `trivy-secret.yaml` are not loaded. Setting
`skip-patterns` **replaces**, rather than extends, Trivy's defaults. The workflow
preserves all 23 patterns from 0.76.1's
[`defaultSkipPatterns`](https://github.com/aquasecurity/trivy/blob/e713f67f7d54decdcd9b3132c9751833dcced698/pkg/fanal/secret/scanner.go#L33-L58):
`.git`, `node_modules`, the listed dependency manifests/lockfiles, and the listed
image, document, binary and archive extensions. Its additional cache scopes are:

| Pattern | Contents |
| --- | --- |
| `**/pnpm-store/**` | pnpm's content-addressed package files |
| `**/_cacache/**` | npm's content-addressed cache |
| `**/.cache/uv/**` | uv's package cache |
| `**/.cache/pip/**` | pip's package cache |
| `**/.cache/mise/npm/aube/store/**` | mise's npm backend package store |
| `**/.cache/mise/downloads-cas/**` | mise's content-addressed tool downloads |

Additional **scan-root-anchored** scopes cover the builder's public install and
package-cache layouts (Trivy paths have no leading slash):

- System mise: `usr/local/share/mise/installs/`.
- User mise: `home/*/.local/share/mise/installs/` and
  `root/.local/share/mise/installs/`.
- Legacy mise: `mnt/devcontainer-cache/mise/installs/`.
- Go modules/toolchains:
  `{var/cache/devcontainer,mnt/devcontainer-cache}/gomod/`,
  `home/*/.cache/gomod/`, `home/*/go/pkg/mod/` and `root/go/pkg/mod/`.
- Bun package cache: `home/*/.bun/install/cache/`, `root/.bun/install/cache/`
  and `{var/cache/devcontainer,mnt/devcontainer-cache}/bun/`.
- Default pnpm store: `home/*/.local/share/pnpm/store/` and
  `root/.local/share/pnpm/store/`.
- Builder uv cache: `{var/cache/devcontainer,mnt/devcontainer-cache}/uv/`.
- Cargo packages: `home/*/.cache/cargo/registry/`; the Cargo home itself is not
  excluded. Both generic builders declare `CARGO_HOME` there.
- Rustup SDKs: only `home/*/.local/share/rustup/toolchains/`, beneath the builders'
  declared `RUSTUP_HOME`.
- uv-managed Python SDK directories: `home/*/.local/share/uv/python/*-*-*/`,
  matching native implementation-version-platform directory names. Both builders
  declare `UV_PYTHON_INSTALL_DIR` at that home-writable data location.
- RubyGems package subtrees (`gems/`) and downloaded archives (`cache/*.gem`)
  under `usr/local/bundle/`, `{home/*,root}/.gem/` (flat or engine/API-version
  scoped), and `{home/*,root}/.local/share/gem/<engine>/<api>/`. Bundler's
  engine/API subdirectories under `usr/local/bundle/` are covered too.
- App-scoped Bundler installs: only `gems/` and `cache/*.gem` beneath
  `{home/*,root}/.cache/*/bundle/<engine>/<api>/`. This covers relocated public
  gems without excluding the app's entire cache.
- Project Bundler installs: only `gems/` and `cache/*.gem` beneath
  `workspaces/*/.bundle/<engine>/<api>/`.

Braces abbreviate separate patterns, not a glob syntax dependency.
There are no version, package, finding-ID or fixture-filename exceptions. The
mise install roots are public tool/package trees; Go distributes public test keys
there and in the module/toolchain cache. Bun's package cache likewise carries
public documentation with sample credentials. The relocations must not turn
those upstream fixtures into credential findings. RubyGems/Bundler likewise
install public gem source containing sample JWTs. Engine and API-version
components are single-directory wildcards, not unrestricted descendants.
RubyGems' [user-directory implementation](https://github.com/rubygems/rubygems/blob/v3.6.2/lib/rubygems/defaults.rb)
and [Bundler's configuration reference](https://bundler.io/man/bundle-config.1.html)
describe these layouts. The preserved upstream `vendor` allow rule already covers
`vendor/bundle` and `vendor/cache`; no new vendor exclusion is added.

Whole Ruby/Bundler cache directories are deliberately **not** excluded.
[Bundler](https://github.com/rubygems/rubygems/blob/v3.6.2/bundler/lib/bundler/source/git.rb)
can put bare Git repositories in `cache/bundler/git/` or its user cache; their
`config` files can retain authenticated remote URLs. Only downloaded `.gem`
archives are excluded there. Git-cache configs remain scanned in filesystem
and saved-image regressions.

Only these named subtrees are added, not the whole mise data directory,
`/var/cache/devcontainer`, home configuration or logs. `.npmrc`, `.ssh`, mise and
Bun configuration, npm logs, first-party build caches and repository source stay
in scope outside preserved dependency exclusions. Cargo `config.toml`,
`credentials` and `credentials.toml`, Rustup `settings.toml`, and configuration
outside uv's managed SDK directories remain scanned. Ruby credentials/configuration
such as `.gem/credentials`, `.gemrc`, `.config/gem/credentials`, `.bundle/config`
and `usr/local/bundle/config` remain scanned. So do Bundler install logs outside
the named package/cache subtrees. A repository path merely containing one of the
new root paths does not match. Image environment/history scanning, detectors,
severities, redacted reporting and scan-error publication gates remain unchanged
in both reusables.
Credentials must not be placed in excluded public install/cache trees: path
exclusions do not authenticate contents or prove that an excluded file is safe.

**Third-party package contents are out of scope**, consistent with Trivy's
default dependency exclusions. Package installs can be hardlinks into these
caches; their hashed cache names also hide the original test/example paths from
Trivy's built-in allow rules. The policy does not maintain individual package
hash exceptions. No detector is disabled. First-party files outside the preserved
default/cache scopes remain scanned, as does image configuration regardless of
filesystem path exclusions. The temporary analysis cache is deleted on success
or failure.

There is no blanket `.venv` or `site-packages` skip, but that does **not** guarantee
installed Python coverage. Trivy's
[layer walker](https://github.com/aquasecurity/trivy/blob/e713f67f7d54decdcd9b3132c9751833dcced698/pkg/fanal/walker/tar.go#L64-L76)
skips hardlink entries. In the bookmark-scraper image, Anthropic SDK source files
are regular entries in uv's excluded cache and hardlinks in the CodeWiki tool's
installed `site-packages`; neither copy is scanned. Skipped cache contents do not
move to installed-path findings. Regular files in uncached venv layouts can still
be scanned, subject to the other built-in exclusions below.

Trivy's built-in global allow rules remain enabled. In 0.76.1 they exclude these
paths (scan-relative, without a leading slash):

| Built-in ID | Path scope |
| --- | --- |
| `dist-info` | Python `.dist-info/` metadata |
| `tests` | Start `test`, or contain `/test`, `-test`, `_test`, `.test` |
| `examples` | Paths or matches containing `example` |
| `vendor` | `/vendor/` |
| `usr-dirs` | `usr/share/`, `usr/include/`, `usr/lib/` |
| `locale-dir` | `/locale/` or `/locales/` |
| `markdown` | Files ending `.md` |
| `node.js` | `opt/yarn-v<version>/` |
| `golang` | `usr/local/go/` |
| `python` | `usr/local/lib/python<version>/` |
| `rubygems` | `usr/lib/gems/` |
| `wordpress` | `usr/src/wordpress/` |
| `anaconda-log` | `var/log/anaconda/` |

The `tests` start-of-path match and `examples` content match ignore case.
Built-in detector-specific allow rules also remain as shipped; see the pinned
[global rules](https://github.com/aquasecurity/trivy/blob/e713f67f7d54decdcd9b3132c9751833dcced698/pkg/fanal/secret/builtin-allow-rules.go)
and [detectors](https://github.com/aquasecurity/trivy/blob/e713f67f7d54decdcd9b3132c9751833dcced698/pkg/fanal/secret/builtin-rules.go).
These defaults reduce fixture/example false positives but are detection blind
spots, not proof that excluded files are safe. Trivy also skips most binary files
(except supported `.pyc` inspection), very short files, and detected base-image
layers. **Skipping detected base layers is accepted for the Microsoft base**;
this is not a full scan of the upstream base or a substitute for secret rotation.

### SDK prose guard

The workflow retains three narrow `exclude-block.regexes` for Anthropic SDK
execution-identity documentation. They suppress only these literal example
substrings, not whole files or real service-account JSON:

```text
sending `\{"type": "service_account"\}` stores the
send null or `\{"type": "service_account"\}`
Always present; `\{"type": "service_account"\}` when runs carry no customer-named
```

The guard matters for layouts that materialize SDK files outside excluded caches
as regular files. Scanning the six installed Anthropic 2.3.0 source files directly
produces nine `gcp-service-account` matches without it and none with it. The
bookmark-scraper image scan itself has no such findings even without the guard,
because of the uv-cache/hardlink layout described above; that zero is not proof
that these files were inspected.

The affected SDK files are `resources/beta/agents/agents.py` (four matches)
and `types/beta/{agent_create_params,agent_update_params,`
`beta_managed_agents_agent,beta_managed_agents_session_agent,`
`beta_managed_agents_session_thread_agent}.py` (one each), under `anthropic/`.
Real-scanner regressions check that unrelated credentials in first-party source
and uncached SDK/venv files still fail without exposing matched content, while
default dependency paths and the package/toolchain-cache scopes remain excluded.
Rooted-cache regressions cover private-key, Stripe and GitHub token fixtures,
protected home/source/lookalike paths, and real image environment/history scans.

References: [Docker build checks](https://docs.docker.com/build/checks/),
[`SecretsUsedInArgOrEnv`](https://docs.docker.com/reference/build-checks/secrets-used-in-arg-or-env/),
[Trivy secret scanning](https://trivy.dev/latest/docs/scanner/secret/).

## Ownership and validation boundary

The consumer owns its build definition, Dockerfile, Docker ignore file, and
runtime `.devcontainer/devcontainer.json` referencing `:main`. Renovate's
shared preset must discover the build definition's features and exclude these
floating first-party images from pinning and updates. The runtime config's
existing lockfile-refresh rule does not cover build-definition lockfiles.

Local contract tests execute the embedded shell with stub CLIs and check
permissions, routing, tags, local-build/scan/push ordering, checksum rejection,
Docker check exit propagation/path resolution, scan failure/report fields, cache
arguments, and input rejection. actionlint
checks workflow syntax; zizmor checks security. YAML anchors share the two build
jobs' matrix and steps while preserving separate token scopes. Also audit an
alias-expanded temporary copy because zizmor warns about anchor support.
Set `TRIVY_TEST_BINARY` to a checksum-verified Trivy 0.76.1 binary when running
`tests/test_devcontainer_image_workflow.py` to include the real-scanner
regressions. Set `PUBLIC_RUBY_GEM_CLIENT` to the checksum-verified
`aws-sdk-core` 3.261.0 `lib/aws-sdk-ssooidc/client.rb` to also
exercise the actual public JWT documentation: excluded in the gem install, still
MEDIUM findings when copied to first-party source. The hash is a test-provenance
assertion, not a production scan exception. Unavailable integration inputs skip
only their optional tests.

Only a real caller can prove native builds, GHCR permissions/private visibility,
first-build cache misses, cache reuse, manifest platforms, pruning, and runtime
feature metadata. The pilot must verify those, plus dependency-volume seeding,
build-safe setup and ARM memory/disk headroom. Scheduled rebuilds can change the
image for the same source commit; `:sha-<commit>` is not a reproducible-build
or immutable-content guarantee.
