# Pinned public release assets

`fetch.sh` is the single release transport for the central installers. It requires
an explicit `GH_TOKEN` and resolves a release tag and asset ID with curl and jq.
The binary request uses `Accept: application/octet-stream`. It never tries a
browser download URL, retries a refusal, or falls back after an error. No gh
CLI or GraphQL repository preflight is involved. Allow-list refusals name the
upstream organization, without printing the response body. A SHA-256 must match before
the downloaded file is handed to an installer.

Bash is already required by every calling composite action. The Node Build Service
installer invokes this same helper, rather than maintaining a second HTTP client.
The Docker CLI archive is from download.docker.com, not a GitHub release.

Curl's `--location` strips Authorization on a different host;
`--location-trusted` must never be used. The tests run real curl through a TLS
fixture proxy. It refuses browser release URLs and checks that the CDN receives
no token. The same tests run with gh absent or a gh executable that always fails,
proving it is not a dependency.

Callers pass the job token, including public callers. Public release assets do not
need a new credential or permission on the upstream repository. A fork pull request
normally gets the base repository's read-only `GITHUB_TOKEN`, not repository secrets.
That is enough for public assets; this does not grant private asset access.
[GitHub documents](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
an ordinary budget of 1,000 API requests per hour per repository for
`GITHUB_TOKEN`, or 15,000 for GitHub Enterprise Cloud. This enterprise's private
canary measured 15,000. The budget is shared per repository across its jobs, not
per minted token. Secondary limits and service failures still fail the job.

Each release platform has a literal tag and SHA-256 in `pins.json`. The repository's
Renovate regex manager uses the built-in `github-release-attachments` datasource
with `currentDigest` and `pinDigests`. That datasource identifies the current asset
by checksum and updates its digest together with its release tag. Asset templates
substitute the version without a leading `v`. No post-upgrade command or controller
allowlist change is needed. Checksums were retained from the existing installers;
TruffleHog's per-platform pins come from the v3.95.5 release asset digests.

The shared shell installer and the Node client installer reuse only an exact
version match. Trivy is not a release-helper consumer: both scanners extract its
binary from the digest-pinned vendor image through the shared `actions/trivy`
installer, because aquasecurity refuses the job token's release API requests.
Mise's later tool installations, indirect downloads inside third-party actions,
and image-build secret delivery are separate from this bootstrap transport.
