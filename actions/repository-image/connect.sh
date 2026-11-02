#!/usr/bin/env bash
# Connects an image client job to the Build Service (mac-dev-playbook ADR 0057 and 0058).
#
# The image runner provides the coordinates, so this public workflow names no internal host:
#   BUILD_SERVICE_BUILDER             tcp://host:port of the rootless BuildKit daemon
#   BUILD_SERVICE_REGISTRY            host:port of the staging registry (TLS, password)
#   BUILD_SERVICE_REGISTRY_AUTH_FILE  file holding one line, user:password, for the staging registry
#   BUILD_SERVICE_CA_URL              the Private CA's API
#   BUILD_SERVICE_CA_ROOT             file holding the Private CA root certificate
#   BUILD_SERVICE_SERVERNAME          optional TLS server name of the daemon (default: the endpoint's host)
#   BUILD_SERVICE_AUDIENCE            optional OIDC audience (default: homelab-build-service)
#
# The job keeps two Docker configs. buildx forwards its configured credentials to the daemon during a build, so
# docker-build holds only the staging registry's; docker-publish adds GHCR and is used only after the checks pass.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${GITHUB_ENV:?}" "${GITHUB_REPOSITORY:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"

fail() {
  echo "::error::$*"
  exit 1
}
for name in BUILD_SERVICE_BUILDER BUILD_SERVICE_REGISTRY BUILD_SERVICE_REGISTRY_AUTH_FILE BUILD_SERVICE_CA_URL BUILD_SERVICE_CA_ROOT; do
  [[ -n "${!name:-}" ]] || fail "$name is not set: the image runner provides the Build Service coordinates"
done
[[ -r "$BUILD_SERVICE_CA_ROOT" && -r "$BUILD_SERVICE_REGISTRY_AUTH_FILE" ]] || fail 'The Build Service CA root or registry credential file is unreadable'

audience="${BUILD_SERVICE_AUDIENCE:-homelab-build-service}"
dir="$RUNNER_TEMP/build-service"
umask 077
mkdir -p "$dir"
chmod 700 "$dir"

# Exchanges the job's GitHub OIDC token for a client certificate that names the repository. Everything specific to the
# Private CA's provisioner (audience, subject, template) lives in this one function.
request_certificate() {
  local token
  [[ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" && -n "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]] ||
    fail 'No OIDC token endpoint: grant id-token: write to the repository-image job'
  token="$(printf 'header = "Authorization: Bearer %s"\n' "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" |
    curl --fail --silent --show-error --retry 3 --config - --get --data-urlencode "audience=$audience" \
      "$ACTIONS_ID_TOKEN_REQUEST_URL" | jq --exit-status --raw-output .value)"
  echo "::add-mask::$token"
  step ca certificate "$GITHUB_REPOSITORY" "$dir/client.crt" "$dir/client.key" \
    --ca-url "$BUILD_SERVICE_CA_URL" --root "$BUILD_SERVICE_CA_ROOT" --token "$token" --force
}
request_certificate

# Go tools (crane, Trivy, step) trust the system roots plus the Private CA that signs the registry
system_roots="${BUILD_SERVICE_SYSTEM_ROOTS:-/etc/ssl/certs/ca-certificates.crt}"
[[ -r "$system_roots" ]] || fail "$system_roots is missing: cannot build the trust bundle"
cat "$system_roots" "$BUILD_SERVICE_CA_ROOT" > "$dir/ca-bundle.pem"
export SSL_CERT_FILE="$dir/ca-bundle.pem"

IFS= read -r credential < "$BUILD_SERVICE_REGISTRY_AUTH_FILE"
[[ "$credential" == ?*:?* ]] || fail 'The registry credential file must hold user:password'
echo "::add-mask::${credential#*:}"
plugins='[]'
if [[ -d "$dir/cli-plugins" ]]; then plugins="$(jq --null-input --arg dir "$dir/cli-plugins" '[$dir]')"; fi
for config in docker-build docker-publish; do
  mkdir -p "$dir/$config"
  printf '%s' "${credential#*:}" |
    DOCKER_CONFIG="$dir/$config" crane auth login "$BUILD_SERVICE_REGISTRY" --username "${credential%%:*}" --password-stdin
  jq --argjson plugins "$plugins" '. + {cliPluginsExtraDirs: $plugins}' "$dir/$config/config.json" > "$dir/$config/config.json.new"
  mv "$dir/$config/config.json.new" "$dir/$config/config.json"
done

export DOCKER_CONFIG="$dir/docker-build"
driver_options="cacert=$BUILD_SERVICE_CA_ROOT,cert=$dir/client.crt,key=$dir/client.key"
if [[ -n "${BUILD_SERVICE_SERVERNAME:-}" ]]; then driver_options+=",servername=$BUILD_SERVICE_SERVERNAME"; fi
docker buildx create --name build-service --driver remote --driver-opt "$driver_options" "$BUILD_SERVICE_BUILDER" > /dev/null

# A client the daemon rejects hangs at "waiting for connection", so every contact carries its own timeout
for attempt in 1 2; do
  if timeout 60 docker buildx inspect --bootstrap build-service > "$dir/builder.txt"; then break; fi
  if [[ "$attempt" -eq 2 ]]; then fail 'The Build Service did not accept this job after 60 seconds twice: check the daemon and the certificate'; fi
  sleep 10
done
cat "$dir/builder.txt"
# buildx lists the worker's native platform first
grep -Eq '^Platforms:[[:space:]]+linux/amd64\*?(,|$)' "$dir/builder.txt" || fail 'The Build Service is not a native linux/amd64 builder'

{
  echo "DOCKER_CONFIG=$dir/docker-build"
  echo 'BUILDX_BUILDER=build-service'
  echo "SSL_CERT_FILE=$dir/ca-bundle.pem"
  echo "STAGING=$BUILD_SERVICE_REGISTRY/${GITHUB_REPOSITORY,,}"
  echo "STAGING_TAG=run-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
} >> "$GITHUB_ENV"
