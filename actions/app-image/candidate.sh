#!/usr/bin/env bash
# Copies the image build.sh staged to GHCR under its temporary candidate tag and checks that the tag resolves to the staged digest.
# imagetools copies by digest, so attestations and every child manifest come across unchanged. crane copy would also push a
# sha256-<digest> referrers tag for each attestation (GHCR has no referrers API), cluttering the package's tag list and
# version feeds. This is the only step that sees the GHCR token, and it reaches it through its environment.
#
# Environment:
#   IMAGE           untagged GHCR image, ghcr.io/<owner>/<package>
#   REGISTRY_TOKEN  the job's GITHUB_TOKEN
set -euo pipefail
: "${IMAGE:?}" "${RUNNER_TEMP:?}" "${GITHUB_OUTPUT:?}" "${GITHUB_ACTOR:?}" "${REGISTRY_TOKEN:?}" "${GITHUB_REPOSITORY_OWNER:?}"
: "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

resolve_package "$IMAGE"
state="$(state_dir)"
digest="$(< "$state/digest")"
staging="$(< "$state/staging.ref")"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'no staged image: the build step did not run'

TARGETS=("$IMAGE:$(candidate_tag "$digest")")

# connect.sh's publish config holds the staging credential; the GHCR login joins it here. The remote builder is not needed.
export DOCKER_CONFIG="$RUNNER_TEMP/build-service/docker-publish"
unset BUILDX_BUILDER
printf '%s' "$REGISTRY_TOKEN" | crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin

copy_tags "$staging@$digest" "$digest"

{
  printf 'digest=%s\n' "$digest"
  printf 'image=%s@%s\n' "$IMAGE" "$digest"
} >> "$GITHUB_OUTPUT"
summarize_tags "$digest"
