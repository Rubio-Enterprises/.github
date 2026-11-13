# Built-site Pages workflow

`pages.yml` builds a repository's site and deploys it to that repository's single
GitHub Pages site. It is separate from
[`codewiki-pages.yml`](codewiki-pages.md), which publishes committed CodeWiki
output without building an application.

## Caller

The repository owns a thin `.github/workflows/pages.yml` with its triggers:

```yaml
name: pages
"on":
  push:
    branches: [main]
  workflow_dispatch:
permissions:
  contents: read
  packages: read
jobs:
  pages:
    permissions:
      contents: read
      packages: read
      pages: write
      id-token: write
    uses: Rubio-Enterprises/.github/.github/workflows/pages.yml@main
    with:
      path: dist
```

A caller may add a schedule. Deployment is not a pull-request check; the Test
Gate's package concern still builds on pull requests. The repository's
`github-pages` environment controls which branches may deploy. The reusable does
not enforce a default-branch guard, so a deliberate build-failure proof on a
throwaway branch can reach the build without weakening the environment policy.
A successful branch build still cannot deploy unless that policy permits it.

Do not hold a caller-level concurrency group named `pages`: the deploy job uses
that literal group and would wait for the caller that is waiting for it. It shares
the lock with CodeWiki and never cancels an in-progress deployment.

## Interface

| Input | Type | Default | Meaning |
| --- | --- | --- | --- |
| `recipe` | string | `package` | Repository recipe that builds the site |
| `path` | string | Required | Repository-relative output directory containing `index.html` |

There are no workflow secrets or outputs. The build receives only the automatic
job token. Recipe names follow the shared Lifecycle dispatcher's
`[a-z][a-z0-9-]*` contract. An empty name fails rather than publishing an old
committed directory. A custom name such as `site-build` is supported without
adding it to the Test Gate concern list.

There are no App-token, cache, hook, enablement or timeout inputs. Repository
builds that need cross-repository credentials or cache handoff need a separately
reviewed generic integration, not repository-specific knobs in this workflow.

## Build and environment

1. [`project-route.yml`](project-route.md) selects the runner with the same
   `route: test` and `inspect-image-inputs: true` settings as the Test Gate. It
   honors declared test-route overrides and uses the changed-image Docker route
   for eligible pull-request or merge-group calls. Default-branch pushes and
   dispatches use ordinary CI Image admission. This does not add a second macOS
   test leg or duplicate any routing logic.
2. Checkout does not persist credentials. Before setup, the build rejects
   `docs/codewiki/metadata.json`, including a dangling symlink at that path. That
   file selects the existing CodeWiki publisher in the repository's standards
   caller. A repository chooses one publisher; two workflows cannot own one site.
3. `actions/configure-pages` reads the existing Pages configuration without
   enabling or modifying it. A repository without Pages enabled fails before
   dependency setup. The build gets `SITE_URL` from its `origin` output and
   `BASE_PATH` from `base_path`, preserving the private-site root path and public
   project-site prefix without hard-coding a host name.
4. The shared [`actions/lifecycle`](../../actions/lifecycle/README.md) runs setup,
   then the requested recipe. A CI Image runs `bin/setup.sh` and
   `bin/<recipe>.sh` directly. Linux runners outside a CI Image run literal
   `just build`, `just setup`, then `just <recipe>` in the repository's Dev Image.
   A native macOS route bootstraps just/mise, uses the existing setup spelling
   selector, then runs the literal recipe. No project toolchain is installed on a
   Linux host. Hosted Docker jobs use the existing disk-preparation action and
   Compose services and dependency volumes are cleaned even on failure.
5. The recipe receives `GH_TOKEN`, `GITHUB_TOKEN` and `NODE_AUTH_TOKEN` from the
   automatic job token, plus `SITE_URL` and `BASE_PATH`. On the Compose path, the
   repository must declare the variables its build needs in `compose.yaml`'s dev
   service environment. CI Image scripts inherit them directly.
6. The directory must exist, contain `index.html`, sit below the repository root
   and use neither parent segments nor newline characters. Its path components
   and contents cannot be symlinks because the Pages archiver dereferences them.
   The upload action receives only the verified absolute directory. Its default
   hidden-file exclusion remains enabled. Linux needs GNU tar; native macOS
   needs `gtar`, as required by the official Pages artifact action.

All action references, including central helpers, are pinned to reviewed SHAs.
The router workflow follows central `main`. The build has a 30-minute limit and
requests only `contents: read`, `packages: read` and `pages: read`.

## Deployment and ownership

The separate `deploy` job needs a successful `build`, which includes artifact
upload. It has no checkout and executes no repository code. Only this job receives
`pages: write` and `id-token: write`. It deploys the uploaded `github-pages`
artifact in environment `github-pages`, exposes the deployed URL on that
environment, and has a 15-minute limit.

A failed setup, build, directory verification or upload prevents deployment. The
published site remains the previous deployment. There is no retry or
continue-on-error path. Contract tests execute the real Lifecycle dispatcher and
inline guards, and pin the permissions, ordering and build-to-deploy dependency.
A real deployment and deliberate failing build must also be verified when
migrating the first consumer; unit tests alone do not prove a remote site stayed
unchanged.

Pages enablement, visibility, custom domains and environment branch policy remain
in the GitHub Settings Plane, through `.github-private` OpenTofu where managed.
The reusable neither creates a site nor changes those settings. Reviewed merges
to central `main` deliver the reusable to thin callers without a release.
