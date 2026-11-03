#!/usr/bin/env bash
# Shared by the repository-image and app-image Build Service clients. Sourced, never run.
#
# Runs a command and retries it once when the daemon closed the connection. A restarting daemon fails its in-flight clients
# with `code = Unavailable` and its cache survives the restart, so one retry is enough; a daemon that stays down fails the
# second attempt. Any other failure is returned at once. Needs RUNNER_TEMP; BUILD_SERVICE_RETRY_DELAY (seconds, default 20)
# is the pause before the retry.
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
