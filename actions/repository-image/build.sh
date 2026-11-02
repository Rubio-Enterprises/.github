#!/usr/bin/env bash
set -euo pipefail
export BUILDX_BUILDER=default
: "${ASSETS:?}" "${CONTEXT:?}" "${BASE:?}" "${BASE_CI:?}" "${IMAGE:?}" "${CI_IMAGE:?}" "${ARCH:?}"
registry="repository-image-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$ARCH"
crane_image=gcr.io/go-containerregistry/crane@sha256:1f968817b95790bed063f71175aa6b8ff879fa17064020415f3e18bb6e6a36e1
registry_image=registry@sha256:d4032d4cc323c1731c707931ecada07a53076eac38e3b61ae57533f170f2c6c3
local_ref=
cleanup() {
  docker rm -f "$registry" >/dev/null 2>&1 || true
  if [[ -n "$local_ref" ]]; then
    docker image rm "$local_ref/features:build" "$local_ref/dev:build" "$local_ref/ci:build" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
# Probe-only instrumentation: wall time and runner-cgroup CPU per phase.
probe_mode=native
probe_cache="${PROBE_CACHE:-warm}"
cpu_usec() { awk '/^usage_usec/ {print $2; found=1} END {if (!found) print 0}' /sys/fs/cgroup/cpu.stat 2>/dev/null || echo 0; }
phase_wall="$(date +%s.%N)"
phase_cpu="$(cpu_usec)"
phase() {
  local now cpu
  now="$(date +%s.%N)"
  cpu="$(cpu_usec)"
  awk -v n="$1" -v a="$ARCH" -v m="$probe_mode" -v c="$probe_cache" \
    -v w0="$phase_wall" -v w1="$now" -v c0="$phase_cpu" -v c1="$cpu" \
    'BEGIN {printf "PROBE_PHASE phase=%s arch=%s mode=%s cache=%s wall_s=%.1f cpu_s=%.1f\n", n, a, m, c, w1-w0, (c1-c0)/1e6}' \
    | tee -a "${RUNNER_TEMP:-/tmp}/probe-metrics.txt"
  phase_wall="$now"
  phase_cpu="$cpu"
}
actual="$(docker info --format '{{.Architecture}}')"
case "$ARCH:$actual" in
  amd64:x86_64|amd64:amd64|arm64:aarch64|arm64:arm64) ;;
  arm64:x86_64)
    if [[ "${ALLOW_EMULATION:-}" != true ]]; then
      echo '::error::Docker daemon is not the requested native architecture'; exit 1
    fi
    probe_mode=emulated
    echo '::notice::Probe: building linux/arm64 under QEMU on an x86_64 daemon' ;;
  *) echo '::error::Docker daemon is not the requested native architecture'; exit 1 ;;
esac

docker run -d --name "$registry" -p 127.0.0.1::5000 "$registry_image" >/dev/null
port="$(docker port "$registry" 5000/tcp)"
local_ref="${port}/repository"
for ((attempt=0; attempt<30; attempt++)); do
  if curl --fail --silent "http://$port/v2/" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent "http://$port/v2/" >/dev/null
crane() { docker run --rm --network host "$crane_image" "$@"; }
phase setup

"$DEVCONTAINER" build --workspace-folder "$CONTEXT/src" \
  --config "$CONTEXT/features/devcontainer.json" --platform "linux/$ARCH" \
  --image-name "$local_ref/features:build" --output type=docker \
  --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
docker push "$local_ref/features:build"
features_digest="$(crane digest --insecure "$local_ref/features:build")"
phase features
shape="$(< "$CONTEXT/expected/shape")"
dockerfile="$ASSETS/Dockerfile"
standards="$GITHUB_WORKSPACE/.standards-config"
pin="${STANDARDS_PIN:-}"
case "$shape" in
  v2) dockerfile="$ASSETS/Dockerfile.v2"; standards="$CONTEXT/package/standards.tgz"; pin= ;;
  legacy|none) ;;
  *) echo '::error::invalid prepared standards shape'; exit 1 ;;
esac
build_context="$CONTEXT/empty"
args=(--platform "linux/$ARCH" --file "$dockerfile"
  --build-context "pins=$CONTEXT/pins"
  --build-context "expected=$CONTEXT/expected"
  --build-arg "BASE=$local_ref/features@$features_digest"
  --build-arg "REPO=${GITHUB_REPOSITORY#*/}"
  --label "org.opencontainers.image.source=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY")
if [[ "$shape" = v2 ]]; then
  build_context="$CONTEXT/build"
  args+=(--secret "id=standards_package,src=$standards")
else
  args+=(--build-context "standards=$standards" --build-context "src=$CONTEXT/src")
fi
check_args=()
# The docker driver lints against the host platform, so an emulated arm64 base trips this check.
if [[ "$probe_mode" == emulated ]]; then
  check_args=(--build-arg "BUILDKIT_DOCKERFILE_CHECK=skip=InvalidDefaultArgInFrom,InvalidBaseImagePlatform")
fi
docker build --check "${args[@]}" "${check_args[@]}" "$build_context"
cache_args=(--cache-from "$IMAGE:main-$ARCH")
if [[ "$probe_cache" == cold ]]; then cache_args=(--no-cache); fi
docker build --provenance=false --sbom=false "${args[@]}" \
  "${cache_args[@]}" --build-arg BUILDKIT_INLINE_CACHE=1 \
  -t "$local_ref/dev:build" "$build_context"
phase dockerfile
docker push "$local_ref/dev:build"
crane rebase --insecure --platform "linux/$ARCH" "$local_ref/dev:build" \
  --old_base "$BASE" --new_base "$BASE_CI" -t "$local_ref/ci:build"
mkdir -p "$CONTEXT/verify"
for name in base base-ci dev ci; do
  case "$name" in
    base) ref="$BASE" ;;
    base-ci) ref="$BASE_CI" ;;
    *) ref="$local_ref/$name:build" ;;
  esac
  crane manifest --insecure --platform "linux/$ARCH" "$ref" > "$CONTEXT/verify/$name.json"
  crane config --insecure --platform "linux/$ARCH" "$ref" > "$CONTEXT/verify/$name-config.json"
done
node "$ASSETS/image.mjs" verify "$CONTEXT/verify"
docker pull --platform "linux/$ARCH" "$local_ref/ci:build"
phase rebase_verify
docker tag "$local_ref/dev:build" "$IMAGE:sha-$GITHUB_SHA-$ARCH"
docker tag "$local_ref/ci:build" "$CI_IMAGE:sha-$GITHUB_SHA-$ARCH"
for artifact in "$IMAGE" "$CI_IMAGE"; do
  docker image save "$artifact:sha-$GITHUB_SHA-$ARCH" -o "$CONTEXT/artifact.tar"
  python3 "$ASSETS/audit.py" "$CONTEXT/artifact.tar" "$standards" "$pin" "$CONTEXT/src"
  rm "$CONTEXT/artifact.tar"
done
phase audit
# Probe: run the smoke checks in their own shell so an emulation failure is recorded, not fatal.
cat > "$CONTEXT/probe-smoke.sh" <<'SMOKE'
docker run --rm --security-opt no-new-privileges --user 1000 --memory 1g --pids-limit 512 \
  --entrypoint bash "$CI_IMAGE:sha-$GITHUB_SHA-$ARCH" -euc '
    workspace="$PWD"
    cd /
    /opt/actions-runner/bin/Runner.Listener --version
    mise --version
    /usr/bin/gh --version
    /usr/bin/jq --version
    test "$UV_LINK_MODE" = copy
    test -w /usr/local/share/mise
    test -w /var/cache/devcontainer
    test -w /opt/actions-runner
    test "$(id -u)" = 1000
    if sudo -n true 2>/dev/null; then exit 1; fi
    test -z "${STANDARDS_PIN:-}"
    test -z "${NODE_AUTH_TOKEN:-}"
    test -z "${STANDARDS_VERSION:-}"
    test ! -e "$workspace/.standards/node_modules"
  '
for artifact in "$IMAGE" "$CI_IMAGE"; do
  for uid in 1000 1001; do
    docker run --rm -i --network none --security-opt no-new-privileges --user root \
      --memory 1g --pids-limit 512 --entrypoint bash "$artifact:sha-$GITHUB_SHA-$ARCH" \
      -s -- "$uid" < "$ASSETS/cache-smoke.sh"
  done
done
SMOKE
smoke_status=0
bash -euo pipefail "$CONTEXT/probe-smoke.sh" || smoke_status=$?
phase smoke
echo "PROBE_SMOKE arch=$ARCH mode=$probe_mode cache=$probe_cache status=$smoke_status" \
  | tee -a "${RUNNER_TEMP:-/tmp}/probe-metrics.txt"
if [[ "$smoke_status" != 0 ]]; then
  if [[ "$probe_mode" != emulated ]]; then exit "$smoke_status"; fi
  echo "::warning::Probe: smoke checks failed under emulation (exit $smoke_status)"
fi
