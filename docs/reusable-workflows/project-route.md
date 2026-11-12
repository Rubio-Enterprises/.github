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

The `linux` input selects `ci-size` regardless of `ci-platform`, with the same
admission and fallback logic as a Linux repository. It still reads and validates
both declarations and returns `platform` so a caller can schedule a separate native
leg. The injected Standards Check uses this selection; default `auto` callers
retain their native macOS route.

The `test` input follows auto selection unless the optional `ci-test-route` property
is set to `small`, `medium`, `docker`, `arm` or `macos`. A missing override in event
metadata triggers a base-repository properties API read. Missing/null/empty values
mean no override; other values fail closed. The effective route determines the
returned platform. `auto` and `linux` ignore this test-only override, so Standards
routing remains independent. Public `test` routes always use hosted fallback labels.

The `test` route also reads `ci-test-macos`, an organization multi-select of
`test`, `typecheck`, `e2e` and `package`, managed through OpenTofu. Missing, null,
empty-string and empty-array values mean no extra leg. Missing event keys trigger
the properties API. Non-array declarations or unknown values fail; so does a
non-empty declaration when the effective test platform is already macOS.
Duplicates are removed and concerns use the canonical order above. A macOS shape
with a Linux `ci-test-route` override may declare a leg. Other routes ignore this
property. The Linux concern list does not change.

The separate `macos-runner` always uses hosted `["macos-26"]` for public
repositories. Private repositories use `RUNNERS.macos` or the same hosted fallback.
Invalid macOS label arrays fail before any outputs.

A capability job can explicitly select `small`, `medium`, `docker`, `kvm`, `arm` or `macos`.
The explicit choice overrides auto selection. Keep existing Docker/KVM/ARM/macOS jobs on their
capability routes; they never select a CI Image. Existing bespoke labels and service topologies
remain repository-owned, not replaced by the router.

Outputs:

- `platform`: declared `linux` or `macos` for `auto`/`linux`, effective platform for `test`; for explicit capability
  routes, `macos` for the macOS capability and `linux` otherwise.
- `runner`: JSON label array for `runs-on: fromJSON(...)`.
- `ci-image`: the string `true` only for a repository-specific CI Image runner; otherwise `false`.
- `image-inputs`: `changed`, `unchanged`, `unknown` or `not-checked`. Only eligible Linux
  `linux`/`test` pull-request and merge-group calls inspect image inputs.
- `macos-concerns`: space-separated declared concerns in canonical order; empty on non-test routes or without a declaration.
- `macos-runner`: JSON label array for the Test Gate's macOS job, independent of the Linux runner.

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

## Pull-request image inputs

For `linux` and `test` calls that would otherwise select a CI Image, a Linux-shape
pull request that changes image inputs selects `RUNNERS.docker` (fallback
`["ubuntu-latest"]`) and `ci-image=false`. Both injected workflows consume this one
decision. Their existing Compose branches build the checked-out pull request's Dev
Image with `just build`, then run their normal Checks and Lifecycle Tasks in it.
No image is published and no tools are overlaid onto the admitted main image.

The router uses `actions/github-script`'s automatic job token with only `contents: read`
for `GET /repos/{owner}/{repo}/compare/{base}...{head}`. The base repository is always
`context.repo`. Pull requests supply `pull_request.base.sha` and `.head.sha`;
merge groups supply `merge_group.base_sha` and `.head_sha`, spanning the whole group,
not a constituent pull request. Only full commit SHAs are accepted. The router
performs no checkout and executes no project code.

The [compare API](https://docs.github.com/en/rest/commits/commits#compare-two-commits)
returns at most 300 changed files, only on its first page. Commit pagination cannot
recover additional file names. One unpaginated request therefore suffices when
fewer than 300 files are returned, even when the commit list exceeds its 250-commit
limit. At 300 files or more the list is ambiguous: use Docker. Missing SHAs, API
errors, missing/malformed comparison data or rename metadata also use Docker with
`image-inputs=unknown`, never the stale CI Image. There is no retry. Property lookup
failures and invalid declarations still fail routing rather than inventing a platform.

Changed paths include:

- Root `image/**`, including locks and hidden sidecars. The starter copies these
  toolchain inputs into the Dev Image. `src/image/**` and `images/**` do not match.
- Files named `Dockerfile`, `Dockerfile.*` or `*.Dockerfile` at any depth. The starter's
  `compose.yaml` uses `dev.build.context: .` and `target: dev`, with the default root
  `Dockerfile`. Matching basenames also covers a dev build that selects, for example,
  `.devcontainer/Dockerfile` or `docker/Dockerfile.dev`. It conservatively includes
  documentation named `Dockerfile.md`; it does not match `Dockerfile-guide.md`.
- Compose files named `compose.yaml`, `compose.yml`, `docker-compose.yaml` or
  `docker-compose.yml`, at any depth, because they can redirect the dev build.
- `.dockerignore` and `*.dockerignore`, at any depth, because they change the build
  context, including Dockerfile-specific ignore files.

Both `filename` and `previous_filename` count for renames; added, modified and removed
inputs all count. This is a convention-based path list, not a Dockerfile dependency
parser: arbitrary source files copied by an image and unconventionally named build
files are not inferred. Such configurations need an explicit routing policy extension,
not a claim that the main image tests them.

Public and unadmitted repositories already build through Compose and do no extra
lookup. Owner-selected hosted and custom-label routes stay unchanged. Explicit
capability and `auto` callers are unchanged. `ci-test-route=arm` already builds the
pull request's Dev Image on ARM and stays there; its separate Standards `linux` call
switches from an admitted CI Image to Docker on an image change. Existing Docker
Test Gates stay Docker. `ci-platform=macos` is exempt from this new detection: its
native checks have no Dev Image, and its existing Linux Standards leg, macOS shape
Test Gate and declared macOS concerns retain their routing.

The router logs the effective route, image-input decision, CI Image admission and
runner labels. It stays a Docker-free small job with no inherited secrets. Invalid
declarations fail before outputs. The temporary Renovate human-merge guard remains
until a post-merge canary proves both required workflows build the changed image;
removing it is separate work.

### Post-merge canary

Use the admitted private `Rubio-Enterprises/org-ops` repository (ID `1277378935`,
small). From the same fresh main, open two separate draft pull requests:

1. Append exactly `# Image-routing canary: build this pull request's Dev Image.` to
   its root `Dockerfile`. Both `standards-linux` and `test-gate-linux` must log
   `Project route: docker; CI Image: false; image inputs: changed`, run on the Docker
   route's runner (`ubuntu-latest` during the hosted initiative), and run `just build`
   against the pull request checkout before their Checks or Lifecycle Scripts.
2. Append exactly `<!-- Non-image routing canary. -->` to `README.md`, without any
   image-input change. Both route calls must report `image inputs: unchanged` and
   use `ci-1277378935-org-ops-small`, with no Dev Image build.

Record the run URLs, head SHAs, runner names and build/Check logs. The first pair
proves routing and building, not that a new toolchain rejects code. Before lifting
the Renovate guard, also use an image-only toolchain change known to fail a Check
and observe that failure inside the newly built image. These are bounded canaries,
not a fleet replay. A standards pull request changing only `release-check.yml`
exercises qualification runners, not this router's consumer pull-request path.
