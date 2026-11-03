#!/usr/bin/env bash
# Copies the image build.sh staged to GHCR under its final tags, or under the temporary candidate tag, and checks that
# every tag resolves to the staged digest. imagetools copies by digest, so attestations and every child manifest come
# across unchanged. crane copy would also push a sha256-<digest> referrers tag for each attestation (GHCR has no referrers
# API), cluttering the package's tag list and version feeds. This is the only step that sees the GHCR token, and it reaches
# it through its environment.
#
# Environment:
#   IMAGE           untagged GHCR image, ghcr.io/<owner>/<package>
#   MODE            candidate or publish
#   TAGS            publish only: full image:tag references, one per line
#   VERIFIED_IMAGE  publish only, optional: the candidate image@digest a job tested. The copy fails unless the staged image has
#                   the same amd64 layers, so a cache eviction between the test and this build cannot ship an untested image.
#                   Unset or `none` skips the check; set but empty fails, because it means a caller wired the input to an output
#                   that did not resolve
#   APP_IMAGE_POLICY  true (the pipeline) refuses latest, an image outside <repo>, and an existing tag that would move
#   MOVING_TAGS     publish only, optional: tags allowed to move to another image, one per line; every other existing tag is refused
#   REGISTRY_TOKEN  the job's GITHUB_TOKEN
set -euo pipefail
: "${IMAGE:?}" "${MODE:?}" "${RUNNER_TEMP:?}" "${GITHUB_OUTPUT:?}" "${GITHUB_ACTOR:?}" "${REGISTRY_TOKEN:?}" "${GITHUB_REPOSITORY_OWNER:?}"
: "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

resolve_package "$IMAGE"
state="$(state_dir)"
digest="$(< "$state/digest")"
staging="$(< "$state/staging.ref")"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'no staged image: the build step did not run'

case "$MODE" in
candidate) TARGETS=("$IMAGE:$(candidate_tag "$digest")") ;;
publish) collect_tags ;;
*) fail 'MODE must be candidate or publish' ;;
esac
verified="${VERIFIED_IMAGE-none}"
if [[ -z "$verified" ]]; then fail 'verified-image was passed but is empty: the job output it reads did not resolve. Omit the input to publish without verification'; fi
if [[ "$verified" != none && "$MODE" != publish ]]; then fail 'verified-image only applies to a publish'; fi

# connect.sh's publish config holds the staging credential; the GHCR login joins it here. The remote builder is not needed.
export DOCKER_CONFIG="$RUNNER_TEMP/build-service/docker-publish"
unset BUILDX_BUILDER
printf '%s' "$REGISTRY_TOKEN" | crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin

# A publish after a tested candidate rebuilds from the builder's cache. If a cache entry was evicted or pruned in between (any
# client may prune, ADR 0058), the layers could differ from the ones that were tested, so compare them. The config (labels,
# created) legitimately differs, and a bare image manifest and an index's amd64 manifest both resolve through --platform.
if [[ "$verified" != none ]]; then
  [[ "$verified" == "$IMAGE@sha256:"* ]] || fail "verified-image is not a digest of $IMAGE"
  layers() { crane manifest --platform linux/amd64 "$1" | jq --compact-output --exit-status '[.layers[].digest]'; }
  tested="$(layers "$verified")" || fail "Could not read the tested image $verified"
  built="$(layers "$staging@$digest")" || fail 'Could not read the staged image'
  [[ "$built" == "$tested" ]] ||
    fail "The image built for publishing has different layers from the tested image $verified: the builder's cache changed in between. Nothing was published; re-run the workflow"
  echo "The staged image has the tested image's layers"
fi

if [[ "$MODE" == publish ]]; then refuse_moved_tags "$digest"; fi
copy_tags "$staging@$digest" "$digest"

{
  printf 'digest=%s\n' "$digest"
  printf 'image=%s@%s\n' "$IMAGE" "$digest"
} >> "$GITHUB_OUTPUT"
summarize_tags "$digest"
