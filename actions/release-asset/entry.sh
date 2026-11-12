#!/usr/bin/env bash
set -euo pipefail
binary=$(bash "$(dirname "${BASH_SOURCE[0]}")/install.sh" "$TOOL" "$RUNNER_TEMP/bin/$TOOL")
dirname "$binary" >> "$GITHUB_PATH"
