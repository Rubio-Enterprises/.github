# Rubio-Enterprises

Polyglot homelab + experimentation org. Repos are TS/JS, Python, Go, and Rust services, CLIs, and libraries — governed by a single set of standards (lint, format, test, CI, secrets, observability, docs floor).

## Standards

The source of truth lives in [`Rubio-Enterprises/standards`](https://github.com/Rubio-Enterprises/standards). New repos start with `copier copy --vcs-ref=v1 gh:Rubio-Enterprises/standards .`. From any Claude Code session inside a Rubio-Enterprises repo, `/audit-standards` checks conformance.

## Reusable workflows

The reusable workflows in `.github/.github/workflows/` are pinned by SHA via Renovate (`helpers:pinGitHubActionDigests`). Per-repo CI is one thin caller workflow that invokes them — see the Copier template for shape.
