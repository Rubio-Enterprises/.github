# Contributing to Rubio-Enterprises

Thanks for considering a contribution.

## Quickstart

1. Open an issue first if the change is non-trivial (more than a docs typo or one-line fix).
2. Fork + branch (`feat/<name>`, `fix/<name>`, `docs/<name>`, `refactor/<name>`).
3. Run `mise install`, install lefthook (`lefthook install`), and run `mise run lint` + `mise run test` locally.
4. Commit using Conventional Commits (`feat: …`, `fix: …`, etc. — see commitlint.config.js in any consumer repo).
5. Open a PR. The PR title is what release-please reads at squash-merge time, so it MUST be a valid Conventional Commit message.

## Standards

Every Rubio-Enterprises repo follows the standards in [`Rubio-Enterprises/standards`](https://github.com/Rubio-Enterprises/standards). Before opening a PR that touches CI, lint configs, or secrets shape, check `standards/spec/` to confirm your change is consistent.

## Reporting bugs / vulnerabilities

For non-security bugs: open an issue using the "Bug" template. For security issues, see `SECURITY.md`.
