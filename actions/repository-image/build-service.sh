#!/usr/bin/env bash
# Builds, rebases, verifies and audits one repository's image pair on the Build Service (ADR 0057). The job is a thin
# client: no Docker daemon, no root. connect.sh has set DOCKER_CONFIG, BUILDX_BUILDER, SSL_CERT_FILE, STAGING and
# STAGING_TAG. Builds push to the staging registry beside the daemon; the workflow copies the verified result to GHCR.
set -euo pipefail
: "${ASSETS:?}" "${CONTEXT:?}" "${BASE:?}" "${BASE_CI:?}" "${ARCH:?}" "${DEVCONTAINER:?}" "${RUNNER_TEMP:?}"
: "${STAGING:?}" "${STAGING_TAG:?}" "${BUILDX_BUILDER:?}"
if [[ "$ARCH" != amd64 ]]; then
  echo '::error::The Build Service builds linux/amd64 only'
  exit 1
fi

# A restarting daemon fails its in-flight clients with Unavailable, and its cache survives the restart, so one retry
# is enough. A daemon that stays down fails the second attempt.
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

# Both settings keep the Features image deterministic, so a repository with extra Features keeps its cache. Without
# attestations the push is one manifest whose digest is the image's. Without a reachable Docker engine the CLI writes a
# syntax directive that makes the daemon fetch the docker/dockerfile:1.4 frontend from Docker Hub on every build.
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
features="$STAGING/features:$STAGING_TAG"
retry_unavailable "$DEVCONTAINER" build --workspace-folder "$CONTEXT/src" \
  --config "$CONTEXT/features/devcontainer.json" --platform "linux/$ARCH" \
  --image-name "$features" --push --omit-syntax-directive \
  --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
features_digest="$(crane digest "$features")"

package="$CONTEXT/package/standards.tgz"
labels=()
if [[ -n "${INPUT_KEY:-}" ]]; then
  if ! [[ "$INPUT_KEY" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    echo '::error::invalid image input key'
    exit 1
  fi
  labels=(--label "org.rubio-enterprises.image.input-key=$INPUT_KEY"
    --label "org.rubio-enterprises.image.built-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)")
fi
args=(--platform "linux/$ARCH" --file "$CONTEXT/package/Dockerfile" --target ci
  --build-context "pins=$CONTEXT/pins"
  --build-context "expected=$CONTEXT/expected"
  --build-arg "BASE=$STAGING/features@$features_digest"
  --build-arg "REPO=${GITHUB_REPOSITORY#*/}"
  --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
  --secret "id=standards_package,src=$package"
  "${labels[@]}")
build_context="$CONTEXT/build"
retry_unavailable docker buildx build --check "${args[@]}" "$build_context"
# The daemon's own cache replaces --cache-from and inline cache, which would also forward a GHCR credential to it
retry_unavailable docker buildx build --provenance=false --sbom=false "${args[@]}" \
  --output "type=image,name=$STAGING/dev:$STAGING_TAG,push=true" "$build_context"

crane mutate "$STAGING/dev:$STAGING_TAG" \
  --annotation "org.opencontainers.image.base.digest=${BASE##*@}" -t "$STAGING/dev:$STAGING_TAG"
crane rebase --platform "linux/$ARCH" "$STAGING/dev:$STAGING_TAG" \
  --old_base "$BASE" --new_base "$BASE_CI" -t "$STAGING/ci:$STAGING_TAG"
mkdir -p "$CONTEXT/verify"
for name in base base-ci dev ci; do
  case "$name" in
  base) ref="$BASE" ;;
  base-ci) ref="$BASE_CI" ;;
  *)
    digest="$(crane digest "$STAGING/$name:$STAGING_TAG")"
    ref="$STAGING/$name@$digest"
    printf '%s\n' "$ref" > "$CONTEXT/$name.ref"
    ;;
  esac
  crane manifest --platform "linux/$ARCH" "$ref" > "$CONTEXT/verify/$name.json"
  crane config --platform "linux/$ARCH" "$ref" > "$CONTEXT/verify/$name-config.json"
done
node "$ASSETS/image.mjs" verify "$CONTEXT/verify"

# Audit only the layers above the public base. verify proved the base layers byte-identical to the published base, so
# they cannot hold standards content, and skipping them saves 40 percent to 70 percent of the download.
dev_ref="$(< "$CONTEXT/dev.ref")"
audit="$CONTEXT/audit"
mkdir -p "$audit"
layers="$(node "$ASSETS/image.mjs" above-base "$CONTEXT/verify")"
crane config --platform "linux/$ARCH" "$dev_ref" > "$audit/config.json"
index=0
while read -r layer; do
  crane blob "${dev_ref%@*}@$layer" > "$audit/layer-$(printf '%04d' "$index")" < /dev/null
  index=$((index + 1))
done <<< "$layers"
python3 "$ASSETS/audit.py" "$audit" "$package" "$CONTEXT/src"
rm -rf "$audit"
printf 'Audited registry artifact: %s\n' "$dev_ref"

# The dev image's cache checks run as build stages without network. The CI image's runtime and cache checks run once per
# base pair release in Rubio-Enterprises/devcontainers: verify proved its repository layers and config identical to dev's.
smoke="$CONTEXT/smoke"
mkdir -p "$smoke"
cp "$ASSETS/smoke.Dockerfile" "$smoke/Dockerfile"
cp "$ASSETS/cache-smoke.sh" "$smoke/cache-smoke.sh"
retry_unavailable docker buildx build --provenance=false --sbom=false --platform "linux/$ARCH" \
  --no-cache-filter dev-1000,dev-1001 --build-arg "DEV_IMAGE=$dev_ref" \
  --output type=cacheonly "$smoke"
