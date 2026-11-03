#!/usr/bin/env bash
# Copies the image build.sh staged to GHCR under its final tags, or under the temporary candidate tag, and checks that
# every tag resolves to the staged digest. crane copies by digest, so attestations and every child manifest come across
# unchanged. This is the only step that sees the GHCR token, and it reaches it through its environment.
#
# Environment:
#   IMAGE           untagged GHCR image, ghcr.io/<owner>/<package>
#   MODE            candidate or publish
#   TAGS            publish only: full image:tag references, one per line
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

targets=()
case "$MODE" in
candidate) targets+=("$IMAGE:$(candidate_tag)") ;;
publish)
  while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ -z "$line" ]]; then continue; fi
    # A reference that names another repository or a digest would publish somewhere the workflow did not intend
    [[ "$line" == "$IMAGE":* ]] || fail "tag $line is not a tag of $IMAGE"
    [[ "${line#"$IMAGE":}" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]] || fail "tag $line is not a valid tag"
    targets+=("$line")
  done <<< "${TAGS:-}"
  [[ "${#targets[@]}" -gt 0 ]] || fail 'publishing needs at least one tag: pass the tags input'
  ;;
*) fail 'MODE must be candidate or publish' ;;
esac

# connect.sh's publish config holds the staging credential; the GHCR login joins it here
export DOCKER_CONFIG="$RUNNER_TEMP/build-service/docker-publish"
printf '%s' "$REGISTRY_TOKEN" | crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
for target in "${targets[@]}"; do
  crane copy "$staging@$digest" "$target"
  [[ "$(crane digest "$target")" == "$digest" ]] || fail "Published manifest of $target differs from the staged image"
  printf 'Published verified digest: %s -> %s\n' "$digest" "$target"
done

{
  printf 'digest=%s\n' "$digest"
  printf 'image=%s@%s\n' "$IMAGE" "$digest"
} >> "$GITHUB_OUTPUT"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo '## Published image'
    echo
    echo "Digest: \`$digest\`"
    echo
    echo '```text'
    for target in "${targets[@]}"; do echo "$target@$digest"; done
    echo '```'
  } >> "$GITHUB_STEP_SUMMARY"
fi
