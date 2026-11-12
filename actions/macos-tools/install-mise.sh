#!/bin/bash
set -euo pipefail
if command -v mise >/dev/null 2>&1; then exit 0; fi
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) ;;
  *) echo '::error::unsupported mise platform'; exit 1 ;;
esac
version=2026.11.1
# jdx/mise v2026.11.1 SHASUMS256.txt, macos-arm64.tar.gz
checksum=e1eaf20469e586f65849256a0be6a58dd7a3e93ae8db68bc74dd7af5ca0fe7f6
work=$(mktemp -d "$RUNNER_TEMP/mise.XXXXXX")
trap 'rm -rf "$work"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
  "https://github.com/jdx/mise/releases/download/v$version/mise-v$version-macos-arm64.tar.gz" -o "$work/mise.tar.gz"
printf '%s  %s\n' "$checksum" "$work/mise.tar.gz" | shasum -a 256 -c -
mkdir -p "$RUNNER_TEMP/mise/bin"
tar -xzf "$work/mise.tar.gz" -C "$RUNNER_TEMP" mise/bin/mise
printf '%s\n' "$RUNNER_TEMP/mise/bin" >> "$GITHUB_PATH"
