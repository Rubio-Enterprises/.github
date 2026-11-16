#!/bin/bash
set -euo pipefail

name="${1:?setup or check is required}"
case "$name" in setup|check) ;; *) echo "::error::unsupported macOS recipe: $name"; exit 1 ;; esac
exec just "$name-macos"
