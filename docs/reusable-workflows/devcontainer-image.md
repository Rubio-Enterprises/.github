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
   --exit-code 1`; it cannot silently fall back to a remote image. Scanner errors
   also fail the step. The 15-minute timeout fails closed. The log records scan
   seconds and status per architecture. No vulnerability database is needed for
   this secret-only scan; telemetry and version-update checks are disabled.
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
This deliberately avoids the mutable Trivy action/setup tags implicated in the
[March 2026 incident](https://github.com/aquasecurity/trivy/discussions/10425).
Checksums establish byte integrity, not immunity to a malicious upstream release.

Caller `trivy.yaml`, `.trivyignore` and `trivy-secret.yaml` are not loaded. The
workflow supplies an explicit **`skip-patterns` allowlist** because 0.76.1
would otherwise skip all `node_modules` and several lockfiles. Neither
`node_modules` nor `.venv` is excluded wholesale. The only custom exclusions
are the evidence-backed fixtures and prose blocks below. The temporary Trivy
analysis cache is deleted on success or failure.

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

### Pilot false-positive exclusions

The first [pilot run][first-scan] failed both architectures with 50 findings
each: 41 private-key matches in pnpm's content-addressed store and nine
`gcp-service-account` matches in Anthropic SDK 2.3.0 docstrings. No image was
pushed. The two corrections use Trivy's native configuration:

- **41 exact pnpm object paths**, not the store or dependency tree, are skipped.
  Every object's SHA-512 was verified against the published `ssh2@1.17.0`
  archive and mapped to its public `test/fixtures/` file. Hashing removes the
  original test path, so Trivy's built-in test-path allow rule cannot recognize
  these copies. The full paths/hashes are embedded in the workflow; the table
  below maps their unique 12-character digest prefixes back to the fixtures.
  New or changed objects are not excluded. This trusts pnpm's content-addressed
  naming, not an independent scan-time assertion that those bytes still match.
- **Three exact prose patterns** in `exclude-block.regexes` suppress the
  SDK's execution-identity examples. They cover only the literal example
  substring, not whole files, the SDK, uv cache, or real service-account JSON.
  The remaining contents of all six affected SDK files stay scanned. No
  detector is disabled.

The excluded prose patterns are:

```text
sending `\{"type": "service_account"\}` stores the
send null or `\{"type": "service_account"\}`
Always present; `\{"type": "service_account"\}` when runs carry no customer-named
```

The affected SDK files are `resources/beta/agents/agents.py` (four matches)
and `types/beta/{agent_create_params,agent_update_params,`
`beta_managed_agents_agent,beta_managed_agents_session_agent,`
`beta_managed_agents_session_thread_agent}.py` (one each), under `anthropic/`.
The real-scanner regression checks also verify that unrelated credentials in
dependency files and SDK files still fail without exposing matched content.

All fixture paths below are relative to `ssh2@1.17.0/test/fixtures/`:

| SHA-512 prefix | Fixture |
| --- | --- |
| `03e8ac3ad639` | `keyParser/ppk_rsa_enc.result` |
| `081a4d030797` | `keyParser/openssh_new_dsa` |
| `0e8eec4450db` | `openssh_new_rsa` |
| `18a3c7d1407f` | `keyParser/openssh_old_rsa.result` |
| `2e495fdf2a0f` | `keyParser/openssh_new_rsa_enc_gcm.result` |
| `38453eb81616` | `keyParser/openssh_new_rsa_enc.result` |
| `41de26bc294e` | `keyParser/ppk_rsa.result` |
| `436f02f5b307` | `keyParser/openssh_new_ecdsa_enc_gcm` |
| `4802052c23fe` | `ssh_host_ecdsa_key` |
| `4f087fd189a7` | `keyParser/openssh_old_dsa_enc.result` |
| `58fe74411e34` | `keyParser/openssh_old_dsa.result` |
| `63099a924155` | `keyParser/openssh_new_ecdsa_enc` |
| `638247405ad2` | `keyParser/openssh_new_ecdsa.result` |
| `6a9cdd0b5dde` | `keyParser/openssh_old_rsa_enc.result` |
| `6f07af000350` | `keyParser/openssh_new_ed25519.result` |
| `7293a11ba467` | `https_key.pem` |
| `7d3ffc59dede` | `keyParser/openssh_old_ecdsa_enc.result` |
| `8050a694218d` | `keyParser/openssh_old_rsa_enc_aes256.result` |
| `9001cc5a0efc` | `ssh_host_dsa_key` |
| `903f8d54f948` | `ssh_host_rsa_key` |
| `924b631bc0e6` | `keyParser/openssh_new_dsa_enc_gcm.result` |
| `9333a54ba03e` | `keyParser/openssh_old_ecdsa.result` |
| `9bdae572f20e` | `id_rsa` |
| `a0318c9394f1` | `keyParser/openssh_new_ecdsa_enc.result` |
| `aa85c50453de` | `keyParser/openssh_old_ecdsa` |
| `b1baa4c5e1b7` | `keyParser/openssh_new_dsa_enc_gcm` |
| `b2c9d877ff80` | `keyParser/openssh_new_dsa.result` |
| `b5c0383718c8` | `id_dsa` |
| `bba839681077` | `id_ecdsa` |
| `c297acc40255` | `keyParser/openssh_new_rsa_enc` |
| `c5839760d2d8` | `keyParser/openssh_new_ecdsa` |
| `c7b8750ecac6` | `keyParser/openssh_new_rsa.result` |
| `c7bbceefb0ac` | `keyParser/ppk_dsa_enc.result` |
| `d04e09fc99b6` | `keyParser/openssh_new_dsa_enc.result` |
| `db4ea37d542b` | `keyParser/openssh_old_rsa` |
| `dd6e1eeafe4d` | `bad_rsa_private_key` |
| `e840d75f553e` | `keyParser/openssh_new_dsa_enc` |
| `f16a44e61491` | `keyParser/openssh_new_ed25519` |
| `f1955c52f8d0` | `keyParser/openssh_new_ecdsa_enc_gcm.result` |
| `f55ed42caac5` | `keyParser/openssh_old_dsa` |
| `f56750d0c92d` | `keyParser/openssh_new_rsa_enc_gcm` |

[first-scan]: https://github.com/Rubio-Enterprises/bookmark-scraper/actions/runs/40151668405

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
`tests/test_devcontainer_image_workflow.py` to include the three real-scanner
regressions; without it, only those optional integration tests are skipped.

Only a real caller can prove native builds, GHCR permissions/private visibility,
first-build cache misses, cache reuse, manifest platforms, pruning, and runtime
feature metadata. The pilot must verify those, plus dependency-volume seeding,
build-safe setup and ARM memory/disk headroom. Scheduled rebuilds can change the
image for the same source commit; `:sha-<commit>` is not a reproducible-build
or immutable-content guarantee.
