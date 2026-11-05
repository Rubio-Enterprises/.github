# Rubio-Enterprises

Polyglot homelab + experimentation org. Repos are TS/JS, Python, Go, and Rust services, CLIs, and libraries — governed by a single set of standards (lint, format, test, CI, secrets, observability, docs floor).

## Standards

The source of truth lives in [`Rubio-Enterprises/standards`](https://github.com/Rubio-Enterprises/standards). Repositories use the internal `ghcr.io/rubio-enterprises/base:main` image: `npm init @rubio-enterprises/standards` onboards one, and `just check` runs every check locally exactly as CI does.

## Reusable workflows

The reusable workflows in `.github/.github/workflows/` are pinned by SHA via Renovate (`helpers:pinGitHubActionDigests`). Per-repo CI is the starter `standards.yml` caller that invokes them, plus the injected Standards Check and the repository's own `test-gate`.
