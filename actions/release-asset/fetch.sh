#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?GH_TOKEN is required for release downloads}"
repo=${1:?owner/repo} tag=${2:?tag} asset=${3:?asset} checksum=${4:?sha256} output=${5:?output}
[[ "$checksum" =~ ^[a-f0-9]{64}$ ]] || { echo 'invalid release checksum' >&2; exit 1; }
work=$(mktemp -d "${RUNNER_TEMP:?}/release-asset.XXXXXX")
trap 'rm -rf "$work"' EXIT
if command -v gh >/dev/null 2>&1; then
  GH_HOST=github.com gh release download --repo "$repo" "$tag" --pattern "$asset" --output "$work/asset"
else
  # curl strips Authorization on cross-host redirects. Never use --location-trusted.
  request() {
    printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN" |
      curl --config - --proto '=https' --proto-redir '=https' --tlsv1.2 --fail --silent --show-error \
        --location --header "Accept: $1" "$2"
  }
  id=$(request application/vnd.github+json "https://api.github.com/repos/$repo/releases/tags/$tag" |
    jq -er --arg name "$asset" '[.assets[] | select(.name == $name)] | if length == 1 then .[0].id else error("release asset missing or ambiguous") end')
  [[ "$id" =~ ^[0-9]+$ ]] || { echo 'invalid release asset id' >&2; exit 1; }
  request application/octet-stream "https://api.github.com/repos/$repo/releases/assets/$id" > "$work/asset"
fi
printf '%s  %s\n' "$checksum" "$work/asset" | shasum -a 256 -c - >&2
mv "$work/asset" "$output"
