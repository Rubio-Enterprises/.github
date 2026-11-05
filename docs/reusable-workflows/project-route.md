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
stripped, then truncated to 40>-<size>`. Both image classes must already be admitted and online
before the central ID switch is enabled. No generic-runner fallback is attempted when an admitted
image label is unavailable.

Missing route keys retain the normal capability fallbacks. Linux project execution
outside a CI Image needs Docker: a selected generic `linux-x64-small` or
`linux-x64-medium` becomes the `RUNNERS.docker` route (fallback `ubuntu-latest`),
and `ubuntu-slim` becomes `ubuntu-latest`. Explicit capability routes and custom
multi-label constraints are preserved. An owner-selected hosted route stays hosted.

The router itself stays a Docker-free small job. It uses the job's read-only token,
no checkout and no inherited secrets. Invalid declarations fail before outputs.
