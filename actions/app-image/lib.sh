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
