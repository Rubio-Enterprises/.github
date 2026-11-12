#!/usr/bin/env bash
set -euo pipefail
binary=$(bash "$(dirname "${BASH_SOURCE[0]}")/../release-asset/install.sh" mise "$RUNNER_TEMP/mise/bin/mise")
printf '%s\n' "$(dirname "$binary")" >> "$GITHUB_PATH"
