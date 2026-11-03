#!/usr/bin/env bash
# Builds one app image on the Build Service and leaves it in the staging registry (mac-dev-playbook ADR 0057). The job is
# a thin client: no Docker daemon, no root. connect.sh has set DOCKER_CONFIG, BUILDX_BUILDER, SSL_CERT_FILE, STAGING and
# STAGING_TAG. publish.sh copies the staged result to GHCR; this step never holds a GHCR credential.
#
# Environment:
#   IMAGE          untagged GHCR image, ghcr.io/<owner>/<package>
#   MODE           check (staging only), candidate (a temporary GHCR tag for a later run job) or publish (final tags)
#   BUILD_CONTEXT  context directory relative to the workspace (default .)
#   BUILD_FILE     Dockerfile relative to the workspace (default <context>/Dockerfile)
#   BUILD_TARGET   optional stage
#   BUILD_ARGS     optional lines of NAME=value
#   BUILD_LABELS   optional lines of key=value
#   ATTESTATIONS   true adds provenance=max and an SBOM to a candidate or publish build; a check never carries attestations
set -euo pipefail
: "${IMAGE:?}" "${MODE:?}" "${RUNNER_TEMP:?}" "${GITHUB_WORKSPACE:?}" "${GITHUB_OUTPUT:?}" "${GITHUB_REPOSITORY_OWNER:?}"
: "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}" "${STAGING:?}" "${STAGING_TAG:?}" "${BUILDX_BUILDER:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

case "$MODE" in check | candidate | publish) ;; *) fail 'MODE must be check, candidate or publish' ;; esac
resolve_package "$IMAGE"
package="$PACKAGE"

# A path from the caller stays inside the workspace: no absolute path, no parent traversal
relative() {
  case "$2" in
  /* | .. | ../* | */../* | */..) fail "$1 must be a path inside the repository" ;;
  esac
}
context="${BUILD_CONTEXT:-.}"
relative context "$context"
context="${context#./}"
context="${context%/}"
[[ -d "$GITHUB_WORKSPACE/$context" ]] || fail "context $context is not a directory of the repository"
file="${BUILD_FILE:-}"
if [[ -z "$file" ]]; then file="$context/Dockerfile"; fi
relative file "$file"
file="${file#./}"
[[ -f "$GITHUB_WORKSPACE/$file" ]] || fail "Dockerfile $file does not exist"
workspace="$(cd "$GITHUB_WORKSPACE" && pwd -P)"
for path in "$context" "$file"; do
  resolved="$(cd "$GITHUB_WORKSPACE" && realpath "$path")"
  [[ "$resolved" == "$workspace" || "$resolved" == "$workspace"/* ]] || fail "$path resolves outside the repository"
done
context_path="$GITHUB_WORKSPACE"
if [[ "$context" != . ]]; then context_path+="/$context"; fi

args=(--platform linux/amd64 --file "$GITHUB_WORKSPACE/$file")
if [[ -n "${BUILD_TARGET:-}" ]]; then args+=(--target "$BUILD_TARGET"); fi
while IFS= read -r line; do
  line="${line%$'\r'}"
  if [[ -z "$line" || "$line" == '#'* ]]; then continue; fi
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || fail 'build-args lines must be NAME=value'
  args+=(--build-arg "$line")
done <<< "${BUILD_ARGS:-}"
while IFS= read -r line; do
  line="${line%$'\r'}"
  if [[ -z "$line" ]]; then continue; fi
  [[ "$line" == ?*=* ]] || fail 'labels lines must be key=value'
  args+=(--label "$line")
done <<< "${BUILD_LABELS:-}"

# A candidate carries the attestations its promotion will publish, so the tested digest is the published digest. It is an index:
# prune.sh deletes its untagged child versions along with it. A check stays one manifest and is never copied to GHCR.
if [[ "$MODE" != check && "${ATTESTATIONS:-true}" == true ]]; then
  args+=(--provenance=mode=max --sbom=true)
else
  args+=(--provenance=false --sbom=false)
fi

state="$(state_dir)"
staging="$STAGING/$package:$STAGING_TAG-$MODE"
metadata="$state/build-metadata.json"
# A failed build must leave nothing for publish.sh to find: it would copy the previous build's image
rm -f "$metadata" "$state/digest" "$state/staging.ref"
# The daemon's own cache replaces --cache-from and --cache-to: the daemon holds no registry or Actions credential
retry_unavailable docker buildx build "${args[@]}" --metadata-file "$metadata" \
  --output "type=image,name=$staging,push=true" "$context_path"

digest="$(jq --exit-status --raw-output '."containerimage.digest"' "$metadata")"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'the build did not report an image digest'
[[ "$(crane digest "$staging")" == "$digest" ]] || fail 'the staged image differs from the build result'
printf '%s\n' "$digest" > "$state/digest"
printf '%s\n' "$staging" > "$state/staging.ref"
printf 'digest=%s\n' "$digest" >> "$GITHUB_OUTPUT"
printf 'Built %s (%s) on the Build Service: %s\n' "$package" "$MODE" "$digest"
