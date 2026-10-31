#!/usr/bin/env bash
set -euo pipefail
: "${GITHUB_ACTION_PATH:?}" "${GITHUB_WORKSPACE:?}" "${RUNNER_TEMP:?}"
case "$PROJECT_TASK" in lint-hooks|typecheck|build) ;; *) echo '::error::unknown project task'; exit 1 ;; esac
export CI=true MISE_ENV=ci TURBO_CONCURRENCY=2 GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never
export IS_FORK="${IS_FORK:-false}" HAS_KNOWLEDGEBASE="${HAS_KNOWLEDGEBASE:-false}"
cd -- "$GITHUB_WORKSPACE"
bash "$GITHUB_ACTION_PATH/preflight.sh"
export STANDARDS_SHAPE
STANDARDS_SHAPE="$(node "$GITHUB_ACTION_PATH/../repository-image/image.mjs" shape)"
package_token=
if [[ "$STANDARDS_SHAPE" = v2 ]]; then package_token="${NODE_AUTH_TOKEN:-${IMAGE_PULL_TOKEN:-}}"; fi
if [ "$IMAGE_MODE" = direct ]; then
  test "$RUNNER_ENVIRONMENT" = self-hosted
  test "$RUNNER_OS" = Linux
  test "$RUNNER_ARCH" = X64
  test "$(id -u)" != 0
  test -x /opt/actions-runner/bin/Runner.Listener
  test -w /usr/local/share/mise
  test -w /var/cache/devcontainer
  test "$UV_LINK_MODE" = copy
  unset IMAGE_PULL_TOKEN
  if [[ "$STANDARDS_SHAPE" = v2 ]]; then
    NODE_AUTH_TOKEN="$package_token" mise run setup
    unset package_token NODE_AUTH_TOKEN
    bash "$GITHUB_ACTION_PATH/preflight.sh" ready
  else
    mise install
    mise run setup
  fi
  exec bash "$GITHUB_ACTION_PATH/$PROJECT_TASK.sh"
fi
[ "$IMAGE_MODE" = hosted ] || { echo '::error::invalid image execution mode'; exit 1; }
[ "$RUNNER_ENVIRONMENT" = github-hosted ] || { echo '::error::hosted image mode refuses a self-hosted runner'; exit 1; }
[ "$RUNNER_OS" = Linux ] && [ "$RUNNER_ARCH" = X64 ]
[[ "$GITHUB_REPOSITORY" =~ ^Rubio-Enterprises/[A-Za-z0-9_.-]+$ ]]
[ -n "${IMAGE_PULL_TOKEN:-}" ] || { echo '::error::hosted image pull requires the repository GITHUB_TOKEN'; exit 1; }
state="$(mktemp -d "$RUNNER_TEMP/project-image.XXXXXX")"
label="project-image-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-$GITHUB_JOB"
cleanup() {
  status=$?
  trap - EXIT INT TERM
  ids="$(docker ps -aq --filter "label=rubio.project-image=$label")" || status=1
  if [ -n "$ids" ]; then
    while IFS= read -r cid; do docker rm -f "$cid" >/dev/null || status=1; done <<< "$ids"
  fi
  rm -rf "$state"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$state/auth"
printf '%s' "$IMAGE_PULL_TOKEN" | docker --config "$state/auth" login ghcr.io --username "$GITHUB_ACTOR" --password-stdin >/dev/null
image="ghcr.io/${GITHUB_REPOSITORY,,}/devcontainer:main"
docker --config "$state/auth" pull "$image"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]]
rm -rf "$state/auth"
unset IMAGE_PULL_TOKEN
if [[ "$STANDARDS_SHAPE" = v2 ]]; then unset NODE_AUTH_TOKEN; fi
cp "$GITHUB_ACTION_PATH/../repository-image/image.mjs" "$state/"
cp "$GITHUB_ACTION_PATH/preflight.sh" "$GITHUB_ACTION_PATH/$PROJECT_TASK.sh" "$state/"
# Do not inherit host Git credentials
: > "$state/gitconfig"
export PROJECT_IMAGE_STATE="$state" PROJECT_IMAGE_ID="$image_id"
node "$GITHUB_ACTION_PATH/runtime.mjs"
cli="$RUNNER_TEMP/project-devcontainer-cli/node_modules/.bin/devcontainer"
args=(--workspace-folder "$GITHUB_WORKSPACE" --config "$state/devcontainer.json" --id-label "rubio.project-image=$label")
"$cli" read-configuration "${args[@]}" --include-merged-configuration > "$state/read.json"
node "$GITHUB_ACTION_PATH/runtime.mjs" verify
"$cli" up "${args[@]}" --skip-post-create --default-user-env-probe none > "$state/up.json"
cid="$(jq -er '.containerId | strings | select(test("^[a-f0-9]{12,64}$"))' "$state/up.json")"
test "$(docker inspect --format '{{index .Config.Labels "rubio.project-image"}}' "$cid")" = "$label"
uid="$(id -u)"
gid="$(id -g)"
# CLI remaps the home UID but not the image's writable tool/cache directories
docker exec --user root "$cid" chown -R "$uid:$gid" /usr/local/share/mise /usr/local/share/mise-shims /var/cache/devcontainer
"$cli" exec "${args[@]}" bash /run/project-image/preflight.sh
if [[ "$STANDARDS_SHAPE" = v2 ]]; then export NODE_AUTH_TOKEN="$package_token"; fi
"$cli" run-user-commands "${args[@]}" --default-user-env-probe none --skip-post-attach
if [[ "$STANDARDS_SHAPE" = v2 ]]; then
  unset NODE_AUTH_TOKEN package_token
  "$cli" exec "${args[@]}" bash /run/project-image/preflight.sh ready
fi
"$cli" exec "${args[@]}" bash "/run/project-image/$PROJECT_TASK.sh"
