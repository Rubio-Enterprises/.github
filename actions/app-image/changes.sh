#!/usr/bin/env bash
# Decides whether a pull request touches anything that can change an image. Any other event (a push, a tag, a dispatch)
# always runs: a publish never skips. A pull request whose every changed file matches an ignore pattern skips the image jobs.
#
# Environment:
#   EVENT_NAME     the triggering event
#   IGNORE_PATHS   shell patterns, one per line; * also matches a slash, so *.md matches docs/a/b.md
# Run it in a checkout of the pull request's merge commit with at least two commits of history. Output: run=true|false
set -euo pipefail
: "${EVENT_NAME:?}" "${GITHUB_OUTPUT:?}"

emit() {
  printf 'run=%s\n' "$1" >> "$GITHUB_OUTPUT"
  echo "$2"
  exit 0
}
if [[ "$EVENT_NAME" != pull_request ]]; then emit true "Not a pull request: the image jobs run"; fi
# A merge commit's first parent is the base, so this is exactly what the pull request changes. Any trouble means run.
files="$(git diff --name-only HEAD^1 HEAD 2> /dev/null)" || emit true "Could not read the changed files: the image jobs run"
if [[ -z "$files" ]]; then emit true "No changed files found: the image jobs run"; fi
patterns=()
while IFS= read -r line; do
  line="${line%$'\r'}"
  if [[ -n "$line" ]]; then patterns+=("$line"); fi
done <<< "${IGNORE_PATHS:-}"
while IFS= read -r file; do
  ignored=false
  for pattern in ${patterns[@]+"${patterns[@]}"}; do
    # shellcheck disable=SC2053 # a glob match on purpose
    if [[ "$file" == $pattern ]]; then
      ignored=true
      break
    fi
  done
  if [[ "$ignored" == false ]]; then emit true "$file can change the image: the image jobs run"; fi
done <<< "$files"
emit false "Only ignored files changed: the image jobs are skipped"
