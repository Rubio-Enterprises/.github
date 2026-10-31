#!/usr/bin/env bash
set -euo pipefail
if [[ "${STANDARDS_SHAPE:-}" = v2 ]]; then exec mise run check; fi
run_fork() {
set -euo pipefail
files=/tmp/lefthook-changed-files.txt
: > "$files"

if git rev-parse --verify --quiet HEAD^1 >/dev/null && git rev-parse --verify --quiet HEAD^2 >/dev/null; then
  git diff -z --name-only --diff-filter=ACMR HEAD^1 HEAD > "$files"
elif [ -n "${GITHUB_BASE_REF:-}" ]; then
  git fetch --no-tags --depth=50 origin "refs/heads/${GITHUB_BASE_REF}:refs/remotes/origin/${GITHUB_BASE_REF}"
  if git merge-base "origin/${GITHUB_BASE_REF}" HEAD >/dev/null; then
    git diff -z --name-only --diff-filter=ACMR "origin/${GITHUB_BASE_REF}...HEAD" > "$files"
  elif git rev-parse --verify --quiet HEAD^ >/dev/null; then
    git diff -z --name-only --diff-filter=ACMR HEAD^ HEAD > "$files"
  else
    git ls-files -z > "$files"
  fi
elif git rev-parse --verify --quiet HEAD^ >/dev/null; then
  git diff -z --name-only --diff-filter=ACMR HEAD^ HEAD > "$files"
else
  git ls-files -z > "$files"
fi

if [ ! -s "$files" ]; then
  echo "::notice::fork PR has no added/copied/modified/renamed files for lefthook to check"
  return 0
fi

echo "Fork-mode lefthook file list:"
tr '\0' '\n' < "$files" | sed 's/^/  /'

if ! mise exec -- lefthook run pre-commit --no-stage-fixed --files-from-stdin < "$files"; then
  echo "::error::fork-mode lefthook hooks failed" >&2
  exit 1
fi
echo "fork-mode lefthook hooks passed"
}
if [ "${IS_FORK:-false}" = true ]; then
  run_fork
else
mise exec -- lefthook run pre-commit --all-files --no-stage-fixed
fi
if [ "${HAS_KNOWLEDGEBASE:-false}" = true ]; then
mise exec -- lefthook run kb-check
fi
if git diff --quiet HEAD --; then
  echo "no hook rewrote a tracked file"
  exit 0
fi
if [ "$IS_FORK" = "true" ]; then
  fix="run 'mise exec -- lefthook run pre-commit' on the changed files"
else
  fix="run 'mise exec -- lefthook run pre-commit --all-files'"
fi
fix="${fix} locally and commit the result"
echo "::error title=lint-hooks::hooks rewrote tracked files — ${fix}"
git --no-pager diff --stat HEAD --
git --no-pager diff HEAD -- | head -n 400
exit 1
