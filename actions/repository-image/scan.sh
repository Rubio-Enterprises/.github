#!/usr/bin/env bash
set -euo pipefail
: "${REF:?}" "${RUNNER_TEMP:?}"
version=0.76.1
checksum=c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0
work="$RUNNER_TEMP/ci-scan"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL --retry 3 -o "$work/trivy.tar.gz" \
  "https://github.com/aquasecurity/trivy/releases/download/v$version/trivy_${version}_Linux-64bit.tar.gz"
printf '%s  %s\n' "$checksum" "$work/trivy.tar.gz" | sha256sum --check --strict
tar -xzf "$work/trivy.tar.gz" -C "$work" trivy
# Findings remain in transient JSON so secret matches cannot reach the public log
if ! "$work/trivy" image --image-src remote --scanners secret --image-config-scanners secret \
  --exit-code 1 --config '' --ignorefile '' --secret-config "$(dirname "$0")/../build-service/trivy-secret.yaml" \
  --cache-dir "$work/cache" --disable-telemetry --skip-version-check --timeout 15m \
  --format json --output "$work/results.json" "$REF" >"$work/scan.log" 2>&1; then
  echo '::error::CI image secret scan failed (findings are not printed)'
  exit 1
fi
