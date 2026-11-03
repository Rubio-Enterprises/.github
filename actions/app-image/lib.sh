#!/usr/bin/env bash
# Shared by the app image operations. Sourced, never run.

fail() {
  echo "::error::$*"
  exit 1
}

# The daemon's restart fails its in-flight clients with Unavailable and its cache survives, so one retry is enough;
# a daemon that stays down fails the second attempt. repository-image/build-service.sh holds the same function.
retry_unavailable() {
  local log attempt status
  log="$(mktemp "$RUNNER_TEMP/build-service-attempt.XXXXXX")"
  for attempt in 1 2; do
    set +e
    "$@" 2>&1 | tee "$log"
    status="${PIPESTATUS[0]}"
    set -e
    if [[ "$status" -eq 0 ]]; then
      rm -f "$log"
      return 0
    fi
    if [[ "$attempt" -eq 1 ]] && grep -q 'code = Unavailable' "$log"; then
      echo '::warning::The Build Service closed the connection; retrying once'
      sleep "${BUILD_SERVICE_RETRY_DELAY:-20}"
      continue
    fi
    break
  done
  rm -f "$log"
  return "$status"
}

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
