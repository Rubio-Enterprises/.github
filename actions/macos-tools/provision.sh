#!/bin/bash
set -euo pipefail

: "${RUNNERS:?runner tool names are required}"
cd "$GITHUB_WORKSPACE"
test -f image/mise.toml
test -f image/mise.lock
home="$RUNNER_TEMP/macos-tools"
base="$home/base"
mkdir -p "$base/mise"

export MISE_DATA_DIR="$home/data"
# Workflow download-cache restore/save steps must use this path outside the checkout
export MISE_CACHE_DIR="$home/cache"
export MISE_STATE_DIR="$home/state"
export MISE_CONFIG_DIR="$home/config"
export MISE_SYSTEM_CONFIG_DIR="$home/system"
export MISE_SHIMS_DIR="$MISE_DATA_DIR/shims"
export MISE_GLOBAL_CONFIG_FILE="$MISE_CONFIG_DIR/empty.toml"
export MISE_TRUSTED_CONFIG_PATHS="$GITHUB_WORKSPACE"
export MISE_YES=1 MISE_AUTO_INSTALL=0
# Mise delegates these stores to runtimes which otherwise share the runner user's home
export RUSTUP_HOME="$MISE_DATA_DIR/rustup"
export CARGO_HOME="$MISE_DATA_DIR/cargo"
export UV_PYTHON_INSTALL_DIR="$MISE_DATA_DIR/uv-python"
export UV_CACHE_DIR="$MISE_CACHE_DIR/uv"
export GOPATH="$MISE_DATA_DIR/go"
export GOMODCACHE="$MISE_CACHE_DIR/go-mod"
export GOCACHE="$MISE_CACHE_DIR/go-build"
export npm_config_cache="$MISE_CACHE_DIR/npm"
export PNPM_HOME="$MISE_DATA_DIR/pnpm"
export npm_config_store_dir="$MISE_CACHE_DIR/pnpm/store"
export npm_config_cache_dir="$MISE_CACHE_DIR/pnpm"
export npm_config_state_dir="$MISE_STATE_DIR/pnpm"
mkdir -p "$MISE_CONFIG_DIR" "$MISE_SYSTEM_CONFIG_DIR"
: > "$MISE_GLOBAL_CONFIG_FILE"

if [ -n "$STANDARDS_SOURCE" ]; then
  cp "$STANDARDS_SOURCE/mise/standards.toml" "$base/mise/standards.toml"
  cp "$STANDARDS_SOURCE/mise/standards.lock" "$base/mise/mise.lock"
else
  image="$(awk '/^FROM ghcr.io\/rubio-enterprises\/base:/ { print $2; exit }' Dockerfile)"
  test -n "$image"
  version="${image##*:}"
  case "$version" in main) version=latest ;; v*) version="${version#v}" ;; esac
  work=$(mktemp -d "$RUNNER_TEMP/macos-tools-package.XXXXXX")
  trap 'rm -rf "$work"' EXIT
  # npm expands the token placeholder from its environment
  NODE_AUTH_TOKEN="$GITHUB_TOKEN" env \
    'npm_config_@rubio-enterprises:registry=https://npm.pkg.github.com' \
    "npm_config_//npm.pkg.github.com/:_authToken=\${NODE_AUTH_TOKEN}" \
    npm pack "@rubio-enterprises/standards@$version" --ignore-scripts --pack-destination "$work" >/dev/null
  tar -xzf "$work/"*.tgz -C "$work" package/mise
  cp "$work/package/mise/standards.toml" "$base/mise/standards.toml"
  cp "$work/package/mise/standards.lock" "$base/mise/mise.lock"
fi

export MISE_OVERRIDE_CONFIG_FILENAMES="$GITHUB_WORKSPACE/image/mise.toml"
mise install --locked
export MISE_OVERRIDE_CONFIG_FILENAMES="$base/mise/standards.toml"
# Split tool names without glob expansion
read -r -a runners <<< "$RUNNERS"
test "${#runners[@]}" -gt 0
mise install --locked "${runners[@]}"
# Later configs win so repository pins override the base's Check runtimes
export MISE_OVERRIDE_CONFIG_FILENAMES="$base/mise/standards.toml:$GITHUB_WORKSPACE/image/mise.toml"
mise env --json > "$home/env.json"
python3 "$GITHUB_ACTION_PATH/export-env.py" "$home/env.json"
