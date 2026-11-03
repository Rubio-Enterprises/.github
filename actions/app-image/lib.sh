#!/usr/bin/env bash
# Shared by the app image operations. Sourced, never run.

fail() {
  echo "::error::$*"
  exit 1
}

# retry_unavailable, shared with repository-image/build-service.sh
# shellcheck source=../build-service/retry.sh
source "$(dirname "${BASH_SOURCE[0]}")/../build-service/retry.sh"

# Sets PACKAGE to the GHCR package name behind an image reference, ghcr.io/<owner>/<package>, or fails. It sets a variable
# instead of printing because a failure inside a command substitution would lose its annotation.
resolve_package() {
  local image="$1" owner="${GITHUB_REPOSITORY_OWNER,,}"
  [[ "$image" == "ghcr.io/$owner/"* ]] || fail "image must be a GHCR image in the $owner namespace"
  PACKAGE="${image#"ghcr.io/$owner/"}"
  [[ "$PACKAGE" =~ ^[a-z0-9]+(([._]|__|-+)[a-z0-9]+)*(/[a-z0-9]+(([._]|__|-+)[a-z0-9]+)*)*$ ]] ||
    fail 'image must be an untagged GHCR image with a valid package name'
  # The pipeline's policy (APP_IMAGE_POLICY=true): a repository's main image is <repo>; its others are <repo>/<name>. The
  # workflow's own check says the same before any step runs; this one keeps every script safe when it is called on its own.
  if [[ "${APP_IMAGE_POLICY:-}" == true ]]; then
    local repo="${GITHUB_REPOSITORY#*/}"
    repo="${repo,,}"
    [[ "$PACKAGE" == "$repo" || "$PACKAGE" == "$repo/"* ]] ||
      fail "image $image is outside this repository's namespace: name it ghcr.io/$owner/$repo or ghcr.io/$owner/$repo/<name>"
  fi
}

# The per-job working directory shared by the operations; connect.sh owns the credentials beside it
state_dir() {
  local dir="$RUNNER_TEMP/app-image"
  mkdir -p "$dir"
  chmod 700 "$dir"
  printf '%s' "$dir"
}

# Candidate tags are the only ones prune deletes, so nothing else may look like one (publish.sh refuses a final tag that does).
# The digest discriminates two candidates of one image in a run, which would otherwise overwrite each other's tag and leave the
# first untagged, where prune never looks.
candidate_tag() {
  local digest="${1#sha256:}"
  printf 'candidate-%s-%s-%s' "$GITHUB_RUN_ID" "$GITHUB_RUN_ATTEMPT" "${digest:0:12}"
}

# A Docker config under the per-job state directory that logs in to GHCR with the job's token and finds the buildx plugin that
# tools installed. It sets DOCKER_CONFIG for the caller. The token reaches jq through a pipe, never a command line.
ghcr_docker_config() {
  local token="$1" config plugins='[]'
  config="$(state_dir)/docker-ghcr"
  mkdir -p "$config"
  chmod 700 "$config"
  if [[ -d "$RUNNER_TEMP/build-service/cli-plugins" ]]; then plugins="$(jq --null-input --arg dir "$RUNNER_TEMP/build-service/cli-plugins" '[$dir]')"; fi
  printf '%s:%s' "${GITHUB_ACTOR:?}" "$token" | base64 | tr -d '\n' |
    jq --null-input --rawfile auth /dev/stdin --argjson plugins "$plugins" \
      '{auths: {"ghcr.io": {auth: $auth}}, cliPluginsExtraDirs: $plugins}' > "$config/config.json"
  chmod 600 "$config/config.json"
  export DOCKER_CONFIG="$config"
  unset BUILDX_BUILDER
}

# Reads TAGS (full image:tag references, one per line) into TARGETS, refusing a reference that names another repository or
# digest, an invalid tag and a tag reserved for candidates, which prune deletes. Under the pipeline's policy
# (APP_IMAGE_POLICY=true) it also refuses `latest`: App Images publish none.
# shellcheck disable=SC2153 # IMAGE is the calling script's input
collect_tags() {
  local line tag
  TARGETS=()
  while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ -z "$line" ]]; then continue; fi
    [[ "$line" == "$IMAGE":* ]] || fail "tag $line is not a tag of $IMAGE"
    tag="${line#"$IMAGE":}"
    [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]] || fail "tag $line is not a valid tag"
    [[ "${APP_IMAGE_POLICY:-}" != true || "$tag" != latest ]] || fail "tag $line: App Images publish no latest tag; pin a version"
    [[ "$tag" != candidate-* ]] || fail "tag $line is reserved for candidate images"
    TARGETS+=("$line")
  done <<< "${TAGS:-}"
  [[ "${#TARGETS[@]}" -gt 0 ]] || fail 'publishing needs at least one tag: pass the tags input'
}

# Under the pipeline's policy an existing tag of the package is never moved to another image, except the ones MOVING_TAGS (one
# exact tag per line) names; without it the check does nothing. Run it after the GHCR login: it reads each target tag. A tag that does not exist yet passes; any other failure to read it
# stops the publish rather than guess.
# shellcheck disable=SC2153
refuse_moved_tags() {
  local digest="$1" target tag current error moving
  [[ "${APP_IMAGE_POLICY:-}" == true ]] || return 0
  moving=$'\n'"${MOVING_TAGS:-}"$'\n'
  moving="${moving//$'\r'/}"
  error="$(state_dir)/crane-error"
  for target in "${TARGETS[@]}"; do
    tag="${target#"$IMAGE":}"
    if current="$(crane digest "$target" 2> "$error")"; then
      [[ "$current" == "$digest" ]] && continue
      [[ "$moving" == *$'\n'"$tag"$'\n'* ]] && continue
      fail "tag $target already points at $current: an existing version tag is never overwritten. Publish a new version, or list the tag in moving-tags if it is meant to move"
    fi
    if ! grep -Eqi 'MANIFEST_UNKNOWN|NAME_UNKNOWN|404|not found' "$error"; then
      cat "$error" >&2
      fail "could not read $target to check that it is unused"
    fi
  done
  rm -f "$error"
}

# Copies one image to every target tag by digest and checks each tag. --prefer-index=false copies a bare image manifest as
# itself: by default buildx wraps one in a new index with a different digest, which the check could never match, while an index
# (attestations) is copied byte for byte either way. crane copy is not used: it also writes a sha256-<digest> referrers tag for
# each attestation, which GHCR cannot hide.
copy_tags() {
  local source="$1" digest="$2" target
  local flags=()
  for target in "${TARGETS[@]}"; do flags+=(--tag "$target"); done
  docker buildx imagetools create --prefer-index=false "${flags[@]}" "$source"
  for target in "${TARGETS[@]}"; do
    [[ "$(crane digest "$target")" == "$digest" ]] || fail "Published manifest of $target differs from the copied image"
    printf 'Published verified digest: %s -> %s\n' "$digest" "$target"
  done
}

# Prints the GitHub step summary block of a published image
summarize_tags() {
  local digest="$1" target
  if [[ -z "${GITHUB_STEP_SUMMARY:-}" ]]; then return 0; fi
  {
    echo '## Published image'
    echo
    echo "Digest: \`$digest\`"
    echo
    echo '```text'
    for target in "${TARGETS[@]}"; do echo "$target@$digest"; done
    echo '```'
  } >> "$GITHUB_STEP_SUMMARY"
}

# Pulls an image by digest with a Docker config that lives only for the pull
pull_candidate() {
  local image="$1" config status=0
  [[ "$image" =~ ^ghcr\.io/[A-Za-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || fail 'the image must be a GHCR image@sha256:<digest>'
  config="$(mktemp -d "$RUNNER_TEMP/docker-pull.XXXXXX")"
  printf '%s' "${REGISTRY_TOKEN:?}" | DOCKER_CONFIG="$config" docker login ghcr.io --username "${GITHUB_ACTOR:?}" --password-stdin > /dev/null || status=$?
  if [[ "$status" == 0 ]]; then DOCKER_CONFIG="$config" docker pull --quiet "$image" > /dev/null || status=$?; fi
  rm -rf "$config"
  [[ "$status" == 0 ]] || fail "Could not pull $image"
  echo "Pulled $image"
}
