#!/usr/bin/env bash
set -euo pipefail
# renovate: datasource=docker depName=ghcr.io/aquasecurity/trivy
image=ghcr.io/aquasecurity/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c
tagged=${image%@*}
version=${tagged##*:}
destination=${1:?destination}
existing=${2:-$(command -v trivy || true)}
if [[ -n "$existing" ]]; then
  observed=$("$existing" --version 2>/dev/null) || observed=''
  if [[ "${observed%%$'\n'*}" == "Version: $version" ]]; then printf '%s\n' "$existing"; exit 0; fi
fi
case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64) platform=linux/amd64 ;;
  Linux:aarch64|Linux:arm64) platform=linux/arm64 ;;
  *) echo 'unsupported Trivy platform' >&2; exit 1 ;;
esac
work=$(mktemp -d "${RUNNER_TEMP:?}/trivy-image.XXXXXX")
trap 'rm -rf "$work"' EXIT
: "${GH_TOKEN:?GH_TOKEN is required for the Trivy image}" "${GITHUB_ACTOR:?}"
# Isolate the read-only vendor pull from the job's other registry credentials.
mkdir "$work/auth"
printf '%s' "$GH_TOKEN" | DOCKER_CONFIG="$work/auth" crane auth login ghcr.io --username "$GITHUB_ACTOR" --password-stdin >&2
DOCKER_CONFIG="$work/auth" crane export --platform "$platform" "$image" "$work/rootfs.tar"
tar -xOf "$work/rootfs.tar" usr/local/bin/trivy > "$work/trivy"
chmod 0755 "$work/trivy"
observed=$("$work/trivy" --version)
[[ "${observed%%$'\n'*}" == "Version: $version" ]] || { echo 'Trivy image version mismatch' >&2; exit 1; }
mkdir -p "$(dirname "$destination")"
mv "$work/trivy" "$destination"
printf '%s\n' "$destination"
