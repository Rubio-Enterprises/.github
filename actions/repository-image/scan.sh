#!/usr/bin/env bash
set -euo pipefail
: "${REF:?}" "${RUNNER_TEMP:?}"
work="$RUNNER_TEMP/ci-scan"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT
trivy=$(bash "$(dirname "${BASH_SOURCE[0]}")/../release-asset/install.sh" trivy "$work/trivy")
# Findings remain in transient JSON so secret matches cannot reach the public log
if ! "$trivy" image --image-src remote --scanners secret --image-config-scanners secret \
  --exit-code 1 --config '' --ignorefile '' --secret-config "$(dirname "$0")/../build-service/trivy-secret.yaml" \
  --cache-dir "$work/cache" --disable-telemetry --skip-version-check --timeout 15m \
  --format json --output "$work/results.json" "$REF" >"$work/scan.log" 2>&1; then
  echo '::error::CI image secret scan failed (findings are not printed)'
  exit 1
fi
