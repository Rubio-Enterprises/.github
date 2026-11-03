#!/usr/bin/env bash
# Tier 2 of the App Image pipeline: the candidate starts and, as a non-root user, does what its kind does. A server must report
# healthy through the HEALTHCHECK its image declares; a command-line tool must run SMOKE_COMMAND and exit 0. The job has a
# Docker daemon and no repository checkout: it runs the image and nothing else.
#
# Environment:
#   IMAGE            the candidate, ghcr.io/<owner>/<package>@sha256:<digest>
#   SMOKE_COMMAND    a command-line tool's arguments for the image's entrypoint, for example --version. Empty means a server
#   SMOKE_ENV        optional NAME=value lines (no secrets) a server needs to start
#   SMOKE_TIMEOUT    seconds a server has to become healthy (default 120)
#   REGISTRY_TOKEN   the job's GITHUB_TOKEN with packages: read
set -euo pipefail
: "${IMAGE:?}" "${REGISTRY_TOKEN:?}" "${RUNNER_TEMP:?}" "${GITHUB_ACTOR:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

timeout="${SMOKE_TIMEOUT:-120}"
[[ "$timeout" =~ ^[1-9][0-9]*$ ]] || fail 'SMOKE_TIMEOUT must be a positive whole number of seconds'
env_flags=()
while IFS= read -r line; do
  line="${line%$'\r'}"
  if [[ -z "$line" || "$line" == '#'* ]]; then continue; fi
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || fail 'smoke-env lines must be NAME=value'
  env_flags+=(--env "$line")
done <<< "${SMOKE_ENV:-}"

pull_candidate "$IMAGE"
container="app-image-smoke-$$"
cleanup() { docker rm --force "$container" > /dev/null 2>&1 || true; }
trap cleanup EXIT

if [[ -n "${SMOKE_COMMAND:-}" ]]; then
  # A command-line tool: no shell, so the words go to the entrypoint exactly as written
  read -r -a words <<< "$SMOKE_COMMAND"
  echo "Running the candidate with: ${words[*]}"
  status=0
  timeout "$timeout" docker run --rm --name "$container" --pull never ${env_flags[@]+"${env_flags[@]}"} "$IMAGE" "${words[@]}" || status=$?
  [[ "$status" == 0 ]] || fail "The image exited with status $status for: ${words[*]}"
  echo 'The command-line image ran and exited 0'
  exit 0
fi

docker run --detach --name "$container" --pull never ${env_flags[@]+"${env_flags[@]}"} "$IMAGE" > /dev/null
deadline=$((SECONDS + timeout))
health=starting
while ((SECONDS < deadline)); do
  state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container")"
  running="${state% *}"
  health="${state#* }"
  if [[ "$health" == none ]]; then fail 'The image declares no HEALTHCHECK, so a server cannot be called healthy: add one, or give the pipeline a smoke-command if this is a command-line tool'; fi
  if [[ "$running" != running ]]; then
    docker logs --tail 50 "$container" >&2 || true
    fail "The container stopped ($running) before it became healthy"
  fi
  if [[ "$health" == healthy ]]; then break; fi
  if [[ "$health" == unhealthy ]]; then
    docker logs --tail 50 "$container" >&2 || true
    fail 'The container reported unhealthy'
  fi
  sleep 2
done
if [[ "$health" != healthy ]]; then
  docker logs --tail 50 "$container" >&2 || true
  fail "The container was not healthy after $timeout seconds"
fi
echo 'The server became healthy'

# The processes of the container as the host sees them: none may run as uid 0, and a distroless image has no `id` to exec
uids="$(docker top "$container" -eo pid,uid | awk 'NR > 1 {print $2}')"
[[ -n "$uids" ]] || fail 'Could not list the container processes'
if grep -qx 0 <<< "$uids"; then fail 'A process in the container runs as root'; fi
echo 'No process in the container runs as root'
