#!/bin/bash
set -euo pipefail

mode="${1:?runner mode is required}"
recipe="${2:?recipe is required}"
[[ "$recipe" =~ ^[a-z][a-z0-9-]*$ ]] || { echo '::error::invalid recipe name' >&2; exit 1; }
case "$mode" in
  image) exec "bin/$recipe.sh" ;;
  compose)
    if [ "$recipe" = setup ]; then just build; fi
    exec just "$recipe"
    ;;
  macos)
    case "$recipe" in
      setup|check) exec bash "$(dirname "$0")/macos-recipe.sh" "$recipe" ;;
      *) exec just "$recipe" ;;
    esac
    ;;
  *) echo '::error::invalid Lifecycle runner mode' >&2; exit 1 ;;
esac
