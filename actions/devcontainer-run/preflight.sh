#!/usr/bin/env bash
set -euo pipefail
[[ "${STANDARDS_PIN:-}" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::invalid independent STANDARDS_PIN" >&2; exit 1; }
test -d .standards-config/.git
test "$(git -C .standards-config config --get remote.origin.url)" = https://github.com/Rubio-Enterprises/standards.git
test "$(git -C .standards-config rev-parse HEAD)" = "$STANDARDS_PIN"
test "$(git -C .standards-config rev-parse --is-shallow-repository)" = false
test -z "$(git -C .standards-config status --porcelain --untracked-files=all)"
test -f .standards-config/config/lefthook/base.yml
status="$(mise bootstrap repos status --missing --json)"
jq -e --arg pin "$STANDARDS_PIN" '
  .repos | any(.path_raw == ".standards-config" and
    .url == "https://github.com/Rubio-Enterprises/standards.git" and
    .ref == $pin and .current_sha == $pin and .state == "current")
' <<<"$status" >/dev/null
