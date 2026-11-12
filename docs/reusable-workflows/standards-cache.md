# Standards cache and public verification

`standards-cache.yml` is workflow_call-only. The existing repository-owned publisher
calls it with `contents: read` and `packages: read`, without inherited secrets.

Its `route` and `warm` jobs use the Linux Standards route. They fill the weekly
workspace-local trivy database cache only on a miss. The injected Standards job
and default-branch verification restore that same path/key. Non-image hosted
builds use the shared Docker disk preparation before `just build`. Warming does
not install project toolchains on the host.

The independent `verify` job calls `default-branch-check.yml` only for public
repositories, on push or workflow_dispatch on their actual default branch. It
excludes `Rubio-Enterprises/.github`. It does not depend on a cache hit, a successful
warm job or a publication job. Private repositories verify through the image
publisher instead, after publication. Neither path changes repository-owned files.

Verification always executes both check families. It needs only the existing
read-only grants and the automatic job token. It adds no required context, secret,
retry, issue, label or notification. The cache reusable's concurrency group and
its publisher's group stay occupied until verification finishes. See
[default-branch verification](../standards-check.md#default-branch-verification)
for routing, names, cache visibility and latest-head behavior.
