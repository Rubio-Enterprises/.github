# Pinned public release assets

`fetch.sh` is the single release transport for the central installers. It requires
an explicit `GH_TOKEN`, uses `gh release download` when available, and otherwise
resolves a release tag and asset ID with curl and jq. Both paths request the API
asset with `Accept: application/octet-stream`. Neither tries a browser download
URL, retries a refusal, or falls back after an error. A SHA-256 must match before
the downloaded file is handed to an installer.

Bash is already required by every calling composite action. The Node Build Service
installer invokes this same helper, rather than maintaining a second HTTP client.
The Docker CLI archive is from download.docker.com, not a GitHub release.

The CLI's [release downloader](https://github.com/cli/cli/blob/trunk/pkg/cmd/release/download/download.go)
uses asset API URLs. Its [authentication transport](https://github.com/cli/cli/blob/trunk/api/http_client.go)
does not add authorization after a cross-host redirect. Curl's `--location` also
strips Authorization on a different host; `--location-trusted` must never be used.
The tests run the real gh and curl through a TLS fixture proxy. It refuses browser
release URLs and checks that the CDN receives no token.

Callers pass the job token, including public callers. Public release assets do not
need a new credential or permission on the upstream repository. A fork pull request
normally gets the base repository's read-only `GITHUB_TOKEN`, not repository secrets.
That is enough for public assets; this does not grant private asset access.
[GitHub documents](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
a budget of 1,000 API requests per hour per repository for `GITHUB_TOKEN`, shared
across its jobs, not per minted token. Secondary limits and service failures still
fail the job.

Each platform has a literal release tag and SHA-256 in `pins.json`. The repository's
Renovate regex manager uses the built-in `github-release-attachments` datasource
with `currentDigest` and `pinDigests`. That datasource identifies the current asset
by checksum and updates its digest together with its release tag. Asset templates
substitute the version without a leading `v`. No post-upgrade command or controller
allowlist change is needed. Checksums were retained from the existing installers;
TruffleHog's per-platform pins come from the v3.95.5 release asset digests.

The shared shell installer and the Node client installer reuse only an exact
version match. The scoped `TRIVY_BIN` override has the same version requirement.
Mise's later tool installations, indirect downloads inside third-party actions,
and image-build secret delivery are separate from this bootstrap transport.
