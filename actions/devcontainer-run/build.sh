#!/usr/bin/env bash
set -euo pipefail
if ! mise tasks info build >/dev/null 2>&1; then
  echo "::error::has_release_artifact is declared but the repository has no 'build' mise task; add a non-publishing build task (Rubio-Enterprises/.github docs/build-gate.md)" >&2
  exit 1
fi

mise run build
