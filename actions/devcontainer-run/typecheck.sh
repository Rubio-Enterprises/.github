#!/usr/bin/env bash
set -euo pipefail
if ! mise tasks info typecheck >/dev/null 2>&1; then
  echo "::notice::no 'typecheck' mise task; typecheck gate passes (defensive)"
  exit 0
fi
set -euo pipefail
pnpm_flags=(--frozen-lockfile --network-concurrency=8 --child-concurrency=2)
if [ -f package.json ]; then
  if   [ -f pnpm-lock.yaml ];               then pnpm install "${pnpm_flags[@]}"
  elif [ -f yarn.lock ];                    then corepack yarn install --frozen-lockfile
  elif [ -f bun.lock ] || [ -f bun.lockb ]; then bun install --frozen-lockfile
  elif [ -f package-lock.json ];            then npm ci
  else echo "::error::package.json without a lockfile — commit one"; exit 1
  fi
fi


mise run typecheck
