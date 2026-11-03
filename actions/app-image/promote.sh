#!/usr/bin/env bash
# Promotes a tested candidate image: gives the image a job already tested its final tags in the same GHCR package, by digest.
# Nothing is built or rebuilt, so the published bytes are the tested bytes, attestations included. The job has no builder, no
# certificate and no OIDC token: only the GHCR token, which reaches this step through its environment.
#
# Environment:
#   IMAGE            untagged GHCR image, ghcr.io/<owner>/<package>
#   CANDIDATE_IMAGE  the tested candidate, <IMAGE>@sha256:<digest>
#   TAGS             full image:tag references, one per line
#   MOVING_TAGS      optional: tags allowed to move to another image, one per line; every other existing tag is refused
#   REGISTRY_TOKEN   the job's GITHUB_TOKEN
set -euo pipefail
: "${IMAGE:?}" "${CANDIDATE_IMAGE:?}" "${RUNNER_TEMP:?}" "${GITHUB_OUTPUT:?}" "${GITHUB_ACTOR:?}" "${REGISTRY_TOKEN:?}"
: "${GITHUB_REPOSITORY_OWNER:?}" "${GITHUB_REPOSITORY:?}"
# Promotion exists only for the pipeline, so its policy always applies
export APP_IMAGE_POLICY=true
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

resolve_package "$IMAGE"
digest="${CANDIDATE_IMAGE#"$IMAGE@"}"
[[ "$CANDIDATE_IMAGE" == "$IMAGE@sha256:"* && "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] ||
  fail "candidate-image must be a digest of $IMAGE (image@sha256:<digest>)"
collect_tags

ghcr_docker_config "$REGISTRY_TOKEN"
[[ "$(crane digest "$CANDIDATE_IMAGE")" == "$digest" ]] || fail "The candidate $CANDIDATE_IMAGE is not in the registry"
refuse_moved_tags "$digest"
copy_tags "$CANDIDATE_IMAGE" "$digest"

{
  printf 'digest=%s\n' "$digest"
  printf 'image=%s@%s\n' "$IMAGE" "$digest"
} >> "$GITHUB_OUTPUT"
summarize_tags "$digest"
