#!/usr/bin/env bash
# Tier 1 of the App Image pipeline: what can be said about an image without running it. It reads the image from a registry,
# never from the repository, so it holds no repository code. It checks:
#   - a non-root USER;
#   - a HEALTHCHECK, when the image is a server (SERVER=true);
#   - the source and revision labels;
#   - a secret scan of the layers and the image configuration (a finding fails the step);
#   - a vulnerability report of the fixable HIGH and CRITICAL findings (it fails the step only when BLOCK_VULNERABILITIES=true,
#     and then only for a fixable CRITICAL).
# In the Build Service's build job it reads the staged image before anything is copied to GHCR, so an image that fails never
# reaches GHCR. For an image built elsewhere, REF names the registry image and REGISTRY_TOKEN logs in to GHCR.
#
# Environment:
#   REF                    optional: image@digest to read. Default: the image build.sh staged
#   PLATFORM               platform to read (default linux/amd64)
#   SERVER                 true when the image is a long-running server: it must declare a HEALTHCHECK (default false)
#   BLOCK_VULNERABILITIES  true fails the step on a fixable CRITICAL vulnerability (default false: report only)
#   REGISTRY_TOKEN         optional: the job's GITHUB_TOKEN with packages: read, for a GHCR REF
#   TRIVY_BIN              optional: a Trivy binary to use instead of the pinned release
set -euo pipefail
: "${RUNNER_TEMP:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

state="$(state_dir)"
platform="${PLATFORM:-linux/amd64}"
server="${SERVER:-false}"
block="${BLOCK_VULNERABILITIES:-false}"
ref="${REF:-}"
if [[ -z "$ref" ]]; then
  digest="$(< "$state/digest")"
  [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fail 'no staged image: the build step did not run'
  ref="$(< "$state/staging.ref")@$digest"
fi
[[ "$ref" =~ ^[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}$ ]] || fail 'REF must be image@sha256:<digest>'
[[ "$platform" =~ ^linux/(amd64|arm64)$ ]] || fail 'PLATFORM must be linux/amd64 or linux/arm64'
[[ "$server" == true || "$server" == false ]] || fail 'SERVER must be true or false'
[[ "$block" == true || "$block" == false ]] || fail 'BLOCK_VULNERABILITIES must be true or false'
if [[ -n "${REGISTRY_TOKEN:-}" ]]; then ghcr_docker_config "$REGISTRY_TOKEN"; fi

# The pinned Trivy release, the one repository-image.yml scans with
install_trivy() {
  local version=0.76.1 checksum=c7cbe3137fb8a48a17574bede3dd6beb16742d1e9772929940fc90082c39d9c0 archive="$state/trivy.tar.gz"
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 3 --output "$archive" \
    "https://github.com/aquasecurity/trivy/releases/download/v$version/trivy_${version}_Linux-64bit.tar.gz"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --strict
  mkdir -p "$state/trivy"
  tar -xzf "$archive" -C "$state/trivy" trivy
  rm -f "$archive"
  TRIVY_BIN="$state/trivy/trivy"
}
if [[ -z "${TRIVY_BIN:-}" ]]; then install_trivy; fi

failures=0
problem() {
  echo "::error title=Image structure::$*"
  failures=$((failures + 1))
}

config="$state/image-config.json"
crane config --platform "$platform" "$ref" > "$config" || fail "Could not read the configuration of $ref"

# Docker accepts a user name or a numeric id, each with an optional group
user="$(jq --raw-output '.config.User // ""' "$config")"
case "${user%%:*}" in
'' | root | 0) problem 'The image runs as root: set USER to a non-root user in the Dockerfile' ;;
esac
if [[ "$server" == true ]] && ! jq --exit-status '(.config.Healthcheck.Test // []) | length > 0 and .[0] != "NONE"' "$config" > /dev/null; then
  problem 'The image is a server but declares no HEALTHCHECK: add one to the Dockerfile'
fi
for label in org.opencontainers.image.source org.opencontainers.image.revision; do
  jq --exit-status --arg label "$label" '(.config.Labels[$label] // "") != ""' "$config" > /dev/null ||
    problem "The image has no $label label"
done

# Trivy runs outside the checkout, so a trivy.yaml, .trivyignore or trivy-secret.yaml the repository carries cannot change the
# scan. The matches stay out of the log: only the rule, the file and the lines are printed.
cache="$state/trivy-cache"
scan() {
  local output="$1"
  shift
  (cd "$state" && "$TRIVY_BIN" image --image-src remote --platform "$platform" --exit-code 0 --config '' --ignorefile '' \
    --cache-dir "$cache" --disable-telemetry --skip-version-check --timeout 15m --format json --output "$output" "$@" "$ref")
}
secrets="$state/secrets.json"
scan "$secrets" --scanners secret --image-config-scanners secret > /dev/null || fail 'The secret scan did not complete'
found="$(jq --raw-output '[.Results[]? | .Target as $t | .Secrets[]? | "\($t):\(.StartLine)-\(.EndLine): \(.RuleID) (\(.Severity))"] | .[]' "$secrets")"
if [[ -n "$found" ]]; then
  while IFS= read -r line; do problem "Secret in the image: $line"; done <<< "$found"
fi

vulnerabilities="$state/vulnerabilities.json"
if scan "$vulnerabilities" --scanners vuln --severity HIGH,CRITICAL > /dev/null; then
  counts="$(jq --raw-output '[.Results[]? | .Vulnerabilities[]? | select((.FixedVersion // "") != "")] |
    "\(map(select(.Severity == "CRITICAL")) | length) \(map(select(.Severity == "HIGH")) | length)"' "$vulnerabilities")"
  critical="${counts% *}"
  high="${counts#* }"
  echo "Vulnerabilities with a fix: $critical CRITICAL, $high HIGH"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "## Vulnerability report"
      echo
      echo "$critical CRITICAL and $high HIGH vulnerabilities have a fixed version."
      if [[ "$critical" != 0 ]]; then
        echo
        echo '| Vulnerability | Package | Installed | Fixed in |'
        echo '| --- | --- | --- | --- |'
        jq --raw-output '[.Results[]? | .Vulnerabilities[]? | select(.Severity == "CRITICAL" and (.FixedVersion // "") != "")] | .[:25][] |
          "| \(.VulnerabilityID) | \(.PkgName) | \(.InstalledVersion) | \(.FixedVersion) |"' "$vulnerabilities"
      fi
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  if [[ "$critical" != 0 ]]; then
    if [[ "$block" == true ]]; then
      problem "$critical fixable CRITICAL vulnerabilities: update the base image or the packages"
    else
      echo "::warning title=Vulnerabilities::$critical CRITICAL vulnerabilities have a fix. The report does not fail the build yet"
    fi
  fi
elif [[ "$block" == true ]]; then
  problem 'The vulnerability scan did not complete'
else
  echo '::warning title=Vulnerabilities::The vulnerability scan did not complete; the report is empty'
fi

rm -f "$secrets" "$vulnerabilities" "$config"
if [[ "$failures" != 0 ]]; then
  fail "$failures image structure problem(s) in $ref"
fi
echo "Image structure checks passed for $ref"
