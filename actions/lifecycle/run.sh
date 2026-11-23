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
  exec)
    # A macOS Shape's justfile has no Linux setup recipe; run its script in the built Dev Image
    if [ ! -f "bin/$recipe.sh" ] || [ ! -x "bin/$recipe.sh" ]; then
      echo "::error::bin/$recipe.sh must be an executable file" >&2
      exit 1
    fi
    if [ "$recipe" = setup ]; then just build; fi
    exec just exec "bin/$recipe.sh"
    ;;
  macos)
    case "$recipe" in
      setup|check) exec bash "$(dirname "$0")/macos-recipe.sh" "$recipe" ;;
      *) exec just "$recipe" ;;
    esac
    ;;
  *) echo '::error::invalid Lifecycle runner mode' >&2; exit 1 ;;
esac
