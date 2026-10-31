#!/usr/bin/env bash
set -euo pipefail
uid="${1:?expected runtime UID}"
[[ "$uid" = 1000 || "$uid" = 1001 ]]
[[ "$(id -u vscode)" = 1000 ]]
probe=".rubio-cache-smoke-$$"
runuser -u vscode -- bash -seu -- "$probe" <<'WARM'
  umask 022
  for cache in "$UV_CACHE_DIR" "$npm_config_cache" "$pnpm_config_store_dir" "$GOMODCACHE" "$MISE_CACHE_DIR"; do
    mkdir -p "$cache/$1/nested"
    printf warm > "$cache/$1/nested/metadata"
  done
WARM
if [[ "$uid" != 1000 ]]; then
  groupmod -g "$uid" vscode
  usermod -u "$uid" -g "$uid" vscode
  # Match the CLI updateUID.Dockerfile home-only ownership migration
  chown -R "$uid:$uid" /home/vscode
fi
runuser -u vscode -- bash -seu -- "$probe" "$uid" <<'VERIFY'
  test "$(id -u)" = "$2"
  test "$(id -g)" = "$2"
  for cache in "$UV_CACHE_DIR" "$npm_config_cache" "$pnpm_config_store_dir" "$GOMODCACHE" "$MISE_CACHE_DIR"; do
    printf updated >> "$cache/$1/nested/metadata"
    test "$(cat "$cache/$1/nested/metadata")" = warmupdated
    mkdir "$cache/$1/created"
    mv "$cache/$1/nested/metadata" "$cache/$1/created/metadata"
    rm -r "$cache/$1"
    printf "cache ownership: uid=%s path=%s writable\n" "$(id -u)" "$cache"
  done
VERIFY
