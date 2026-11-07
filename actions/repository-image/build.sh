#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../build-service/retry.sh"
: "${GITHUB_WORKSPACE:?}" "${STAGING:?}" "${STAGING_TAG:?}" "${RUNNER_TEMP:?}"
file="${BUILD_FILE:-Dockerfile}"
context="${BUILD_CONTEXT:-.}"
[[ "${BUILD_TARGET:-ci}" = ci ]] || { echo '::error::target must be ci'; exit 1; }
workspace=$(realpath "$GITHUB_WORKSPACE")
for path in "$file" "$context"; do
  case "$path" in /*|..|../*|*/../*|*/..) echo '::error::build paths must stay inside the repository'; exit 1 ;; esac
  resolved=$(realpath "$workspace/$path")
  [[ "$resolved" = "$workspace" || "$resolved" = "$workspace/"* ]] || { echo '::error::build path escapes repository'; exit 1; }
done
test -f "$workspace/$file"
test -d "$workspace/$context"
# Only literal image refs are supported so a Release Pin cannot fall back to main
declare -A upstreams digests
upstreams[base]=$(awk 'tolower($1) == "from" { i = 2; if ($i ~ /^--platform=/) i++; print $i; exit }' "$workspace/$file")
upstreams[runner]=$(awk 'tolower($1) == "copy" { for (i = 2; i <= NF; i++) if ($i ~ /^--from=ghcr.io\/rubio-enterprises\/runner[:@]/) print substr($i, 8) }' "$workspace/$file" | sort -u)
for name in base runner; do
  upstream="${upstreams[$name]}"
  if [[ ! "$upstream" =~ ^ghcr\.io/rubio-enterprises/$name(:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127})?(@sha256:[a-f0-9]{64})?$ || "$upstream" = "ghcr.io/rubio-enterprises/$name" ]]; then
    echo "::error::Dockerfile must declare a literal $name image reference with a tag or digest"
    exit 1
  fi
done

# Buildx forwards its Docker auth to BuildKit, so GHCR access stays in the client-only config
export DOCKER_CONFIG="$RUNNER_TEMP/build-service/docker-publish"
printf '%s' "${REGISTRY_TOKEN:?}" | crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
unset REGISTRY_TOKEN
contexts=()
for name in base runner; do
  upstream="${upstreams[$name]}"
  digest=$(crane digest --platform linux/amd64 "$upstream")
  [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
  digests[$name]="$digest"
  if [[ "$name" = base && -n "${EXPECTED_BASE_DIGEST:-}" && "$EXPECTED_BASE_DIGEST" != "$digest" ]]; then
    echo '::error::dispatched base digest is superseded or differs from the Dockerfile Release Pin'
    exit 1
  fi
  staged="$STAGING/$name:$STAGING_TAG"
  crane copy "${upstream%@*}@$digest" "$staged"
  [[ "$(crane digest "$staged")" = "$digest" ]] || { echo "::error::staged $name digest differs from resolved image"; exit 1; }
  contexts+=(--build-context "$upstream=docker-image://$STAGING/$name@$digest")
done
export DOCKER_CONFIG="$RUNNER_TEMP/build-service/docker-build"
metadata="$RUNNER_TEMP/build-service/ci-metadata.json"
rm -f "$metadata"
staged="$STAGING/ci:$STAGING_TAG"
retry_unavailable docker buildx build --platform linux/amd64 --file "$workspace/$file" --target ci --pull \
  "${contexts[@]}" \
  --provenance=false --sbom=false --metadata-file "$metadata" \
  --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
  --label "org.opencontainers.image.revision=$GITHUB_SHA" \
  --label "org.opencontainers.image.base.name=${upstreams[base]}" \
  --label "org.opencontainers.image.base.digest=${digests[base]}" \
  --label "org.rubio-enterprises.image.built-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --output "type=image,name=$staged,push=true,compression=zstd,compression-level=9,oci-mediatypes=true" "$workspace/$context"
digest=$(jq -er '."containerimage.digest"' "$metadata")
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
[[ "$(crane digest "$staged")" = "$digest" ]]
printf 'ref=%s@%s\n' "${staged%:*}" "$digest" >> "$GITHUB_OUTPUT"
