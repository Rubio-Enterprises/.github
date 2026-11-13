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
| `app-repositories` | string | Empty | Optional comma/newline-separated repository basenames under the caller's owner |
| `cache-path` | string | Empty | Optional repository-relative directory to persist between builds |

Optional secrets `app-client-id` and `app-private-key` are required together when
`app-repositories` is set. There are no workflow outputs. With both optional inputs
empty, the build still receives only the automatic job token and no cache is
restored or saved. Recipe names follow the shared Lifecycle dispatcher's
`[a-z][a-z0-9-]*` contract. An empty name fails rather than publishing an old
committed directory. A custom name such as `site-build` is supported without
adding it to the Test Gate concern list.

There are no arbitrary hook, enablement or timeout inputs. App access and cache
persistence are generic optional capabilities, not repository-specific settings.

### Read-only source access

A caller can opt into cross-repository reads without changing package access:

```yaml
    with:
      path: site
      recipe: site-build
      app-repositories: |
        source-one
        source-two
      cache-path: .cache/site-reads
    secrets:
      app-client-id: ${{ secrets.SOURCE_APP_CLIENT_ID }}
      app-private-key: ${{ secrets.SOURCE_APP_PRIVATE_KEY }}
```

The existing GitHub App installation must allow the listed repositories. The
workflow validates the nonempty repository list and both secrets, then mints a
short-lived installation token with only `permission-contents: read`. Owner is
fixed to the caller's repository owner. A whitespace/comma-only list fails rather
than silently requesting access to the whole installation. The token action
revokes its token when the job finishes.

Only the requested build recipe receives that App token as `GH_TOKEN` and
`GITHUB_TOKEN`. Dependency setup still uses the job token. `NODE_AUTH_TOKEN`
always stays the automatic job token because GitHub's npm registry rejects App
tokens. Without App inputs, both source-token variables also stay the job token.
The deploy job receives neither App secrets nor the source token.

### Persistent build cache

`cache-path` is a literal directory, not a file or glob. The shared directory
validator rejects absolute paths, parent segments, newlines, the repository root,
symlink components and symlink contents. Cache paths additionally reject glob
characters because the cache action and `hashFiles` interpret patterns. A first
run may start with no directory; validation creates it empty.

After setup, the workflow validates the directory, restores the newest matching
cache by prefix and validates the restored tree before the recipe runs. The
namespace includes runner OS, architecture and a stable hash of the normalized
relative directory. Restore/save use that relative path so workspace location is
not a per-run key component. A `./` prefix and trailing slash keep literal names
from becoming home-directory patterns, comments or trimmed whitespace in the
underlying Actions globber; the content-hash pattern uses the same prefix.

After any attempted build, including a failed build, the workflow revalidates the
directory and computes its content digest with GitHub's `hashFiles`. A nonempty
cache is saved under `<namespace>-<content digest>` only when that differs from the
restored key. An unchanged or empty cache is not saved. All post-build cache steps
use `!cancelled()`, not `success()`: failures still retain useful reads, while
cancelled jobs do not save. A cache save cannot make a failed build deploy.

The recipe must use this directory through its own configuration or arguments.
The reusable does not invent a cache variable or change `XDG_CACHE_HOME`. For an
XDG-style tool, its repository can configure `XDG_CACHE_HOME` to a repository-local
root; a tool with a cache-path argument can instead name the file beneath this
directory directly. The same relative directory is visible in CI Image, Compose
and macOS checkouts. Never put tokens or secrets in a persisted cache.

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
5. The recipe receives `GH_TOKEN`/`GITHUB_TOKEN` from the optional read-only App
   token or the automatic job token, `NODE_AUTH_TOKEN` from the job token, and
   `SITE_URL`/`BASE_PATH` from Pages configuration. CI Image scripts and macOS just
   recipes inherit these values directly. On the Compose path, the repository
   must forward each value its build reads in `compose.yaml`'s dev-service
   environment, including `GITHUB_TOKEN` if the tool reads that name rather than
   `GH_TOKEN`. Declaring only one token name does not forward the other. Cache
   configuration also remains repository-owned; the workspace bind makes the
   validated repository-local directory available inside the Dev Image.
6. The directory must exist, contain `index.html`, sit below the repository root
   and use neither parent segments nor newline characters. Its path components
   and contents cannot be symlinks because the Pages archiver dereferences them.
   The upload action receives only the verified absolute directory. Its default
   hidden-file exclusion remains enabled. Linux needs GNU tar; native macOS
   needs `gtar`, as required by the official Pages artifact action.

First-party `.github` workflows and actions follow central `main`, matching the
organization's ref policy. Third-party actions are pinned to reviewed SHAs.
The build has a 30-minute limit and
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
publisher guard and shared directory validator, and assert permissions, ordering,
App scope, cache-save policy and the build-to-deploy dependency.
A real deployment and deliberate failing build must also be verified when
migrating the first consumer; unit tests alone do not prove a remote site stayed
unchanged.

Pages enablement, visibility, custom domains and environment branch policy remain
in the GitHub Settings Plane, through `.github-private` OpenTofu where managed.
The reusable neither creates a site nor changes those settings. Reviewed merges
to central `main` deliver the reusable to thin callers without a release.
