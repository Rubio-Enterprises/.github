#!/bin/bash
set -euo pipefail

# Temporary sweep compatibility. Delete this script when every repository uses -macos.
name="${1:?setup or check is required}"
case "$name" in setup|check) ;; *) echo "::error::unsupported macOS recipe: $name"; exit 1 ;; esac
recipes=$(just --summary)
recipe="$name-native"
for candidate in $recipes; do
  if [ "$candidate" = "$name-macos" ]; then recipe="$candidate"; break; fi
done
just "$recipe"
