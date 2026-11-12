#!/usr/bin/env bash
set -euo pipefail
binary=$(bash "$(dirname "${BASH_SOURCE[0]}")/../release-asset/install.sh" just "$RUNNER_TEMP/bin/just")
printf '%s\n' "$(dirname "$binary")" >> "$GITHUB_PATH"
