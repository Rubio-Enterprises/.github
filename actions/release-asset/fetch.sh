#!/usr/bin/env bash
set -euo pipefail
: "${GH_TOKEN:?GH_TOKEN is required for release downloads}"
repo=${1:?owner/repo} tag=${2:?tag} asset=${3:?asset} checksum=${4:?sha256} output=${5:?output}
[[ "$checksum" =~ ^[a-f0-9]{64}$ ]] || { echo 'invalid release checksum' >&2; exit 1; }
work=$(mktemp -d "${RUNNER_TEMP:?}/release-asset.XXXXXX")
trap 'rm -rf "$work"' EXIT
# curl strips Authorization on cross-host redirects. Never use --location-trusted.
request() {
  local status
  if ! status=$(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN" |
    curl --disable --config - --proto '=https' --proto-redir '=https' --tlsv1.2 --silent --show-error \
      --location --header "Accept: $1" --output "$3" --write-out '%{http_code}' "$2"); then
    echo "Release API request failed for organization ${repo%%/*}" >&2
    return 1
  fi
  if [[ "$status" != 200 ]]; then
    if jq -e '(.message // "") | ascii_downcase | test("ip allow ?list|ip address.*not permitted")' "$3" >/dev/null 2>&1; then
      echo "Release API refused for organization ${repo%%/*} (HTTP $status): IP allow list restriction" >&2
    else
      echo "Release API refused for organization ${repo%%/*} (HTTP $status)" >&2
    fi
    return 1
  fi
}
request application/vnd.github+json "https://api.github.com/repos/$repo/releases/tags/$tag" "$work/release.json"
id=$(jq -er --arg name "$asset" '[.assets[] | select(.name == $name)] | if length == 1 then .[0].id else error("release asset missing or ambiguous") end' "$work/release.json")
[[ "$id" =~ ^[0-9]+$ ]] || { echo 'invalid release asset id' >&2; exit 1; }
request application/octet-stream "https://api.github.com/repos/$repo/releases/assets/$id" "$work/asset"
printf '%s  %s\n' "$checksum" "$work/asset" | shasum -a 256 -c - >&2
mv "$work/asset" "$output"
