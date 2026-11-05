#!/usr/bin/env bash
set -euo pipefail
: "${IMAGE:?}" "${REF:?}" "${GITHUB_SHA:?}"
[[ "$REF" =~ @sha256:[a-f0-9]{64}$ ]]
digest="${REF##*@}"
owner="${GITHUB_REPOSITORY_OWNER,,}"
package="${IMAGE#ghcr.io/"$owner"/}"
encoded="${package//\//%2F}"
# The CI image contains internal standards, so an existing public package is not a valid destination
reply="$RUNNER_TEMP/build-service/package-visibility"
status=0
GH_TOKEN="$REGISTRY_TOKEN" gh api --include --jq .visibility "orgs/$GITHUB_REPOSITORY_OWNER/packages/container/$encoded" > "$reply" 2>"$reply.err" || status=$?
if [[ "$status" = 0 ]]; then
  visibility=$(tail -n 1 "$reply")
  [[ "$visibility" = private || "$visibility" = internal ]] || { echo '::error::refusing to publish internal CI content to a public package'; exit 1; }
else
  [[ "$(head -n 1 "$reply" | awk '{print $2}')" = 404 ]] || { echo '::error::cannot verify CI package visibility'; exit 1; }
fi
crane config "$REF" | jq -e '.os == "linux" and .architecture == "amd64"' >/dev/null
export DOCKER_CONFIG="$RUNNER_TEMP/build-service/docker-publish"
printf '%s' "${REGISTRY_TOKEN:?}" | crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
target="$IMAGE:sha-$GITHUB_SHA-amd64"
crane copy "$REF" "$target"
[[ "$(crane digest "$target")" = "$digest" ]] || { echo '::error::published digest differs from scanned image'; exit 1; }
unset BUILDX_BUILDER
docker buildx imagetools create --prefer-index=true --tag "$IMAGE:main" --tag "$IMAGE:sha-$GITHUB_SHA" "$target"
[[ "$(crane digest --platform linux/amd64 "$IMAGE:main")" = "$digest" ]]
