#!/usr/bin/env bash
set -euo pipefail
version=1.59.0
case "$(uname -s):$(uname -m)" in
  Linux:x86_64) triple=x86_64-unknown-linux-musl; checksum=409044617b6f06517c1035e55ca38c8c856ff8b650e41bc2aa1ef82d5903a23e ;;
  Linux:aarch64|Linux:arm64) triple=aarch64-unknown-linux-musl; checksum=23871b1702bfd550fb5f901778fd4290ecf2ee034a45a889fbe96d89b188086d ;;
  Darwin:arm64) triple=aarch64-apple-darwin; checksum=6574cea774ed4cae12e24b6aa11857de33e80dab4667cf628fd5840df96a2a9d ;;
  Darwin:x86_64) triple=x86_64-apple-darwin; checksum=036ead9df9bc31dbae9934ae3b4236ed124d0bafcffd33439460ae084d5c9cb7 ;;
  *) echo '::error::unsupported just platform'; exit 1 ;;
esac
work=$(mktemp -d "$RUNNER_TEMP/just.XXXXXX")
trap 'rm -rf "$work"' EXIT
if [ -n "${GH_TOKEN:-}" ] && command -v gh >/dev/null 2>&1; then
  gh release download --repo casey/just "$version" \
    --pattern "just-$version-$triple.tar.gz" --output "$work/just.tar.gz"
else
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
    "https://github.com/casey/just/releases/download/$version/just-$version-$triple.tar.gz" -o "$work/just.tar.gz"
fi
printf '%s  %s\n' "$checksum" "$work/just.tar.gz" | shasum -a 256 -c -
mkdir -p "$RUNNER_TEMP/bin"
tar -xzf "$work/just.tar.gz" -C "$RUNNER_TEMP/bin" just
printf '%s\n' "$RUNNER_TEMP/bin" >> "$GITHUB_PATH"
