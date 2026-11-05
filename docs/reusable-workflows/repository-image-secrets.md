# CI Image secret checks

The immutable staged CI image is scanned before it can reach GHCR. Scan failures
block copy and tag movement. `actions/repository-image/scan.sh` uses the pinned
Trivy binary, central policy and no caller-supplied ignore/config file. Findings
remain in temporary JSON and are never printed or uploaded.

## Trivy installation and exclusions

Trivy **0.76.1** is downloaded from its official GitHub release with the Linux
amd64 SHA-256 committed in the scan script. `sha256sum --check --strict` must succeed
before extraction; no installer script runs. Version updates require reviewing
both the archive hash and the default skip-list snapshot. A floating checksum
download is not a trust pin. This avoids the mutable action/setup tags implicated
in the [March 2026 incident](https://github.com/aquasecurity/trivy/discussions/10425).
Checksums establish byte integrity, not immunity to a malicious upstream release.

Caller `trivy.yaml`, `.trivyignore` and `trivy-secret.yaml` are not loaded. The policy is
`actions/build-service/trivy-secret.yaml`, which every scan step copies from the central action.
Setting `skip-patterns` **replaces**, rather than extends, Trivy's defaults. The workflow
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
in this workflow.
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
layers. **Skipping detected base layers is accepted for the public bases**;
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

## Local verification

`tests/test_host_first.py` covers publication failure paths;
`tests/test_app_image_secret_policy.py` covers the App Image policy and real-scanner fixtures. Set `TRIVY_TEST_BINARY` to a checksum-verified Trivy
0.76.1 binary to run the latter. Optional verified public sources are
`PUBLIC_OPENSSL_SOURCE` (openssl 0.10.81 `src/pkey_ctx.rs`) and
`PUBLIC_RUBY_GEM_CLIENT` (aws-sdk-core 3.261.0
`lib/aws-sdk-ssooidc/client.rb`); their test hashes are provenance assertions,
not production scan exceptions. Missing inputs skip only those optional tests.
