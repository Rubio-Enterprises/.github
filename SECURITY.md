# Security policy

## Reporting a vulnerability

For security issues in any Rubio-Enterprises repo, please open a private vulnerability report via [GitHub Security Advisories](https://github.com/Rubio-Enterprises/.github/security/advisories/new) rather than a public issue. Include: affected repo + version, reproduction steps, and impact.

## Scope

This policy covers code in any `Rubio-Enterprises/*` repo. Forks of upstream OSS (e.g. `whisper.cpp`, `khoj` upstream) are out of scope; report to the upstream project.

## Secrets

Encrypted secrets follow the sops + age model documented in `standards-design.md` §6.10. If you find a leaked secret in any repo's git history, please report via the advisory flow above so we can rotate.
