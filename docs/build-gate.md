# Build gate

`.github/workflows/build.yml` is the `gate-build` Gate Family: a Required
Governance Workflow that the `gate-build` org ruleset injects into pull requests
and merge groups. It owns two Check Catalog checks:

- BD-01, *Release artifact builds*: it builds the release package, binary, app
  or extension without publishing it.
- CT-01, *Container smoke test*: it builds the release image without pushing,
  starts it with Docker Compose, and requires a passing healthcheck and a
  non-root process.

## Selection

`.github-private` Terraform sets the `gate-build` repository property from the
Classification Answers:

```text
gate_build = standards_onboarded AND enforce_build
             AND (has_release_artifact OR has_container_image)
```

Inside the run, each check follows its own Facet. `has_release_artifact: true`
runs BD-01 and `has_container_image: true` runs CT-01. The workflow reads the
pull request's answers and the target branch's answers, and runs any check
that either of them declares. So a pull request that removes a Facet must still
pass that check, and the check stops on the next pull request. If neither set of
answers declares a Facet, the run reports a notice and passes. This only happens
while the property is still catching up with a merged change.

## Repository interface

### BD-01: `mise run build`

Add a `build` mise task that builds the release artifact from a clean checkout
without publishing it. The task installs its own workspace dependencies.
The workflow installs only the mise tools, and pnpm when `pnpm-lock.yaml`
exists. If a repository declares `has_release_artifact` but has no `build`
task, BD-01 fails.

- Runner: `RUNNERS.medium`/`heavy` (hosted `ubuntu-latest` fallback).
  Repositories declaring `has_swift` build on `RUNNERS.macos` (hosted
  `macos-26` fallback). The workflow checks for Xcode first.
- A repository declaring `has_ruby` gets its `.ruby-version` and locked
  bundle from `ruby/setup-ruby` before the task runs, on either route, so a
  Fastlane or Bundler build works. The macOS route first installs Homebrew's
  `openssl@3`, `libyaml` and `gmp` and the fixed tool cache path that the
  Darwin prebuilds need. The Linux route's glue-runner image is Ubuntu 24.04
  with a runner-owned `/opt/hostedtoolcache` and the prebuilds' runtime
  libraries; it has no `-dev` headers, so a gem that compiles against a
  system library fails the build.
- Apple builds are unsigned and never publish. The job has no signing, App
  Store Connect or registry credentials, so the task must not need them. For
  example, pass `CODE_SIGNING_ALLOWED=NO` to `xcodebuild`. Signed releases stay
  in `testflight.yml`.
- Repositories using native configuration (standards
  ADR-0033) are set up by the [`standards-bootstrap`](standards-bootstrap.md)
  action before the build. It clones the private Shared Config Checkout
  through a read-only standards-reader token and removes that route before
  any repository code runs.

### CT-01: `compose.ci.yml`

Commit `compose.ci.yml` at the repository root. Its `app` service must build
the release image (the same Dockerfile target you ship) and keep the image's
real healthcheck. Declare real dependencies, such as a database, with normal
Compose `depends_on`. The workflow runs:

```sh
docker compose -f compose.ci.yml up --build --wait --wait-timeout 300
```

It then requires all of these, or CT-01 fails:

- `app` has a running container.
- Its healthcheck status is `healthy`. Compose's `--wait` alone accepts a
  service that has no healthcheck.
- `docker top` lists at least one process and no process runs as UID 0. Do not
  force a user in `compose.ci.yml`. The check tests the image's default user.

CT-01 also fails if `compose.ci.yml` is missing or has no `app` service. It
refuses to run on a rootless or user-namespace-remapped Docker daemon, because
there `docker top` cannot show whether a container process is root. The job
runs on `RUNNERS.docker` (hosted `ubuntu-latest` fallback). It always collects
logs, and it removes its uniquely named Compose project with its volumes and
locally built images. It never logs in to a registry or pushes.

## Not covered

- Reuse of an artifact built by another workflow. Separate injected workflows
  cannot use `needs:` across workflows. Where a repository's required
  `test-gate` already builds the exact release artifact, the Check Catalog
  makes that `test-gate` the BD-01 owner. Record that exception in the
  selector, not here.
- Platform matrices (TS-03) belong to each repository's `test-gate`.
