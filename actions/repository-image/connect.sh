#!/usr/bin/env bash
# Connects an image client job to the Build Service (mac-dev-playbook ADR 0057 and 0058).
#
# The image runner provides the coordinates, so this public workflow names no internal host:
#   BUILD_SERVICE_BUILDER             tcp://host:port of the rootless BuildKit daemon
#   BUILD_SERVICE_REGISTRY            host:port of the staging registry (TLS, password)
#   BUILD_SERVICE_REGISTRY_AUTH_FILE  file holding one line, user:password, for the staging registry
#   BUILD_SERVICE_CA_URL              the Private CA's API
#   BUILD_SERVICE_CA_ROOT_SHA256      lowercase hex SHA-256 fingerprint of the Private CA root, without colons
#   BUILD_SERVICE_SERVERNAME          optional TLS server name of the daemon (default: the endpoint's host)
#   BUILD_SERVICE_AUDIENCE            optional OIDC audience (default: homelab-build-service)
# The job needs curl, openssl and jq. The certificate exchange is the client step of mac-dev-playbook
# docs/homelab/references/operations/build-service-client-certificates.md.
#
# The job keeps two Docker configs. buildx forwards its configured credentials to the daemon during a build, so
# docker-build holds only the staging registry's; docker-publish adds GHCR and is used only after the checks pass.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${GITHUB_ENV:?}" "${GITHUB_REPOSITORY:?}" "${GITHUB_RUN_ID:?}" "${GITHUB_RUN_ATTEMPT:?}"

fail() {
  echo "::error::$*"
  exit 1
}
for name in BUILD_SERVICE_BUILDER BUILD_SERVICE_REGISTRY BUILD_SERVICE_REGISTRY_AUTH_FILE BUILD_SERVICE_CA_URL BUILD_SERVICE_CA_ROOT_SHA256; do
  [[ -n "${!name:-}" ]] || fail "$name is not set: the image runner provides the Build Service coordinates"
done
[[ -r "$BUILD_SERVICE_REGISTRY_AUTH_FILE" ]] || fail 'The Build Service registry credential file is unreadable'
[[ "$BUILD_SERVICE_CA_ROOT_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail 'BUILD_SERVICE_CA_ROOT_SHA256 must be 64 lowercase hex digits without colons'
for tool in curl openssl jq; do
  command -v "$tool" > /dev/null || fail "$tool is required on the image runner"
done

audience="${BUILD_SERVICE_AUDIENCE:-homelab-build-service}"
dir="$RUNNER_TEMP/build-service"
umask 077
mkdir -p "$dir"
chmod 700 "$dir"

# Trades the job's GitHub OIDC token for a client certificate that names the repository (POST /1.0/sign). The OIDC token
# and the request token never appear in a command line: curl reads the request token's header from stdin, and jq reads the
# OIDC token from stdin and writes the request body to curl's stdin. The CA takes the identity from the token, so the
# CSR's subject is irrelevant. Everything specific to the Private CA's provisioner lives in this function.
request_certificate() {
  local fingerprint token status
  [[ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" && -n "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}" ]] ||
    fail 'No OIDC token endpoint: grant id-token: write to the repository-image job'

  # Trust the Private CA only if its root matches the pinned fingerprint
  curl --fail --silent --show-error --insecure --max-time 20 "$BUILD_SERVICE_CA_URL/roots.pem" --output "$dir/roots.pem"
  fingerprint="$(openssl x509 -in "$dir/roots.pem" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')" ||
    fail 'The Private CA did not return a readable root certificate'
  if [[ "$fingerprint" != "$BUILD_SERVICE_CA_ROOT_SHA256" ]]; then
    rm -f "$dir/roots.pem"
    fail 'Private CA root fingerprint does not match the pinned one'
  fi
  mv "$dir/roots.pem" "$dir/ca.crt"

  token="$(printf 'header = "Authorization: Bearer %s"\n' "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" |
    curl --fail --silent --show-error --retry 3 --max-time 20 --config - --get --data-urlencode "audience=$audience" \
      "$ACTIONS_ID_TOKEN_REQUEST_URL" | jq --exit-status --raw-output .value)"
  echo "::add-mask::$token"

  openssl ecparam -genkey -name prime256v1 -noout -out "$dir/client.key"
  openssl req -new -key "$dir/client.key" -subj "/CN=build-service-client" -out "$dir/client.csr"
  status="$(jq --null-input --raw-input --rawfile csr "$dir/client.csr" '{csr: $csr, ott: input}' <<< "$token" |
    curl --silent --show-error --max-time 30 --cacert "$dir/ca.crt" --header 'Content-Type: application/json' \
      --data-binary @- --output "$dir/response.json" --write-out '%{http_code}' "$BUILD_SERVICE_CA_URL/1.0/sign")"
  rm -f "$dir/client.csr"
  if [[ "$status" != 201 ]]; then
    echo "::error::Build Service certificate refused (HTTP $status): $(jq -r '.message // .' "$dir/response.json" 2> /dev/null || true)"
    rm -f "$dir/client.key" "$dir/response.json"
    exit 1
  fi
  # The leaf, then the intermediate
  jq -r '.certChain[]' "$dir/response.json" > "$dir/client.crt"
  rm -f "$dir/response.json"
  chmod 600 "$dir/client.key" "$dir/client.crt"
}
request_certificate

# Go tools (crane, Trivy) trust the system roots plus the Private CA that signs the registry
system_roots="${BUILD_SERVICE_SYSTEM_ROOTS:-/etc/ssl/certs/ca-certificates.crt}"
[[ -r "$system_roots" ]] || fail "$system_roots is missing: cannot build the trust bundle"
cat "$system_roots" "$dir/ca.crt" > "$dir/ca-bundle.pem"
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
driver_options="cacert=$dir/ca.crt,cert=$dir/client.crt,key=$dir/client.key"
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
