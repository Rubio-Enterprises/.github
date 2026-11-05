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
# BuildKit uses registry credentials over its session, not as a Dockerfile secret or build arg
cleanup() {
  jq 'del(.auths["ghcr.io"])' "$DOCKER_CONFIG/config.json" > "$DOCKER_CONFIG/config.json.new"
  mv "$DOCKER_CONFIG/config.json.new" "$DOCKER_CONFIG/config.json"
}
trap cleanup EXIT
printf '%s' "${REGISTRY_TOKEN:?}" | crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
metadata="$RUNNER_TEMP/build-service/ci-metadata.json"
rm -f "$metadata"
staged="$STAGING/ci:$STAGING_TAG"
retry_unavailable docker buildx build --platform linux/amd64 --file "$workspace/$file" --target ci --pull \
  --provenance=false --sbom=false --metadata-file "$metadata" \
  --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
  --label "org.opencontainers.image.revision=$GITHUB_SHA" \
  --label "org.rubio-enterprises.image.built-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --output "type=image,name=$staged,push=true,compression=zstd,compression-level=9,oci-mediatypes=true" "$workspace/$context"
digest=$(jq -er '."containerimage.digest"' "$metadata")
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
[[ "$(crane digest "$staged")" = "$digest" ]]
printf 'ref=%s@%s\n' "${staged%:*}" "$digest" >> "$GITHUB_OUTPUT"
