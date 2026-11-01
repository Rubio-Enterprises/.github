# Project runner routing

`project-route.yml` selects a runner for project commands without running them. The caller keeps
its own checkout, setup, checks, Lifecycle Tasks, services and required job name. Repository-agnostic
jobs and image builders do not use it.

```yaml
jobs:
  route:
    uses: Rubio-Enterprises/.github/.github/workflows/project-route.yml@main
  test-gate:
    name: test-gate
    needs: route
    runs-on: ${{ fromJSON(needs.route.outputs.runner) }}
    # Keep this repository's steps here.
```

## Inputs and outputs

The optional `route` input defaults to `auto`: `ci-platform=macos` selects `macos`; otherwise
`ci-size` selects `small` or `medium`. These declarations are organization custom properties,
not inferred from project files. When either property is missing from the event, the router reads
the base repository's properties API. API errors fail the route rather than silently selecting small.
Missing property values use their organization schema defaults, `linux` and `small`.

A capability job can explicitly select `small`, `medium`, `docker`, `kvm`, `arm`, `macos` or `vrt`.
The explicit choice overrides auto selection. Keep existing Docker/KVM/ARM/macOS/VRT jobs on their
capability routes; they never select a CI Image. Existing bespoke labels and service topologies
remain repository-owned, not replaced by the router.

Outputs:

- `runner`: JSON label array for `runs-on: fromJSON(...)`.
- `ci-image`: the string `true` only for a repository-specific CI Image runner; otherwise `false`.

## Admission and fallback

`CI_IMAGE_REPOS` is a numeric repository-ID array declared in `.github-private` Terraform. Its
visibility is `all`; its value is a reviewed rollout switch, not a consumer setting.
`RUNNERS` remains the private owner-operated map. This workflow writes neither variable.

Image routing requires all of:

1. A private/internal repository in Rubio-Enterprises with its base repository ID admitted.
2. The selected job route is small or medium.
3. The selected RUNNERS value is exactly `["linux-x64-small"]` or `["linux-x64-medium"]`,
   matching the selected class. Extra label constraints are preserved as ordinary routes, never discarded.

The image label matches org-ops's inventory producer:
`ci-<ID>-<basename lowercased, each non-[a-z0-9-] replaced by '-', leading/trailing hyphens
stripped, then truncated to40>-<size>`. Both image classes must already be admitted and online
before the central ID switch is enabled. No generic-runner fallback is attempted when an admitted
image label is unavailable.

Missing RUNNERS keys retain the existing hosted fallbacks: small → `ubuntu-slim`, medium/Docker/KVM
→ `ubuntu-latest`, ARM → `ubuntu-24.04-arm`, macOS → `macos-26`; VRT remains self-only on
`linux-x64-medium`. Public consumers cannot see the private map. An owner-selected hosted route
also remains hosted even if the repository ID is admitted.

The router itself is a repository-agnostic job on the ordinary small route, with its existing
hosted fallback. It uses a SHA-pinned first-party GitHub action, the read-only job token, no
checkout and no inherited secrets. Values enter through environment variables/context, not script
interpolation. Invalid JSON/types, unknown routes and invalid property values fail clearly.

## Image job setup and qualification

A caller using the image output must still run canonical `mise run setup` with step-local
`NODE_AUTH_TOKEN` and `packages: read`, creating current `.standards/` in the actual checkout.
Standards is not baked into the image. Explicitly trust the checkout before mise commands if
skipping mise-action, which normally supplies that trust. Installed tool versions and shared
package caches come from the image; setup installs anything missing and project dependencies.
Do not restore a plain-runner mise data cache over the image environment.

Measure route-job queue/runtime as well as project setup/task/job durations. A warm tool cache
alone does not establish end-to-end speedup. Live image routing requires both-set online/private-pull
and image-digest evidence plus natural project-job results; mocked route tests prove only selection.

This reusable does not implement hosted Dev Container parity or migrate existing callers.
The repository-owned Test Gates require a deliberate caller-update wave; changing the standards
package's starter affects future copies only. Starter/fleet adoption and hosted parity are separate
owner-approved image work. Required check names and test/JUnit proofs are unchanged.
