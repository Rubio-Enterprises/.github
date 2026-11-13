# Lifecycle dispatch

This composite stages a small dispatcher outside the checkout and can run one
named recipe. It inherits the caller's environment, current directory and output
file. It does not select Test Gate concerns, validate JUnit, clear reports, group
logs, install tools, authenticate, or change permissions.

```yaml
- uses: Rubio-Enterprises/.github/actions/lifecycle@main
  with:
    mode: compose
    recipe: package
```

Modes:

- `image`: run `bin/<recipe>.sh` directly. This also serves declared macOS legs
  whose setup and concerns execute directly, without Docker.
- `compose`: run literal `just <recipe>`. For `setup`, first run `just build`.
- `macos`: run `setup` and `check` through the existing macOS recipe-spelling
  selector; other names run literal `just <recipe>`. There is no Docker build.

Names match `[a-z][a-z0-9-]*`; custom names such as `site-build` are supported.
Failures stop immediately and retain their exit status, without retries.

## Caller-owned loops and clean checkouts

With an empty `recipe` (the default), the action only stages its dispatcher and
the existing macOS spelling selector under `$RUNNER_TEMP/lifecycle`. Its `runner`
output is the staged script path. Call it as `bash <runner> <mode> <recipe>`.
This is the same implementation used by the composite's direct invocation.

The Test Gate checks out the action at its central ref alongside the policy
reader, invokes the staging step, then removes that entire central checkout
before repository setup. Its existing shell loop calls the staged script, keeping
concern selection, script validation, report cleanup, outputs and log groups in
the workflow. This avoids both duplicated dispatch and a central checkout visible
to repository tree-walking tools. The existing macOS helper source path is retained
for workflows created before this extraction.

The separate `e2e.yml` has a different setup contract: a CI Image may omit
`bin/setup.sh`. The Test Gate requires it. E2E remains unchanged rather than
silently making setup mandatory or adding an optional-setup policy to this seam.
