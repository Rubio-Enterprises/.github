#!/bin/bash
set -euo pipefail

fail() { echo "::error::pages: $*" >&2; exit 1; }
src="${DIRECTORY_PATH%/}"
case "$src" in
  ''|/*|..|../*|*/..|*/../*|*$'\n'*|*$'\r'*)
    fail 'path must be a repository-relative directory without parent segments or newlines' ;;
esac
case "$PURPOSE" in
  site) ;;
  cache)
    # Cache actions and hashFiles interpret globs rather than literal directory names
    case "$src" in *[\*\?\[\]\{\}\\\!]*) fail 'cache directory must not contain glob characters' ;; esac
    ;;
  *) fail 'unknown directory purpose' ;;
esac
parent="$src"
while :; do
  [ ! -L "$parent" ] || fail 'directory paths must not traverse symlinks'
  case "$parent" in */*) parent="${parent%/*}" ;; *) break ;; esac
done
if [ "$PURPOSE" = cache ] && [ ! -e "$src" ]; then mkdir -p -- "$src"; fi
if [ "$PURPOSE" = site ]; then
  [ -d "$src" ] && [ -f "$src/index.html" ] || fail 'the site directory must exist and contain index.html'
else
  [ -d "$src" ] || fail 'cache directory must exist'
fi
src="$(cd -- "$src" && pwd -P)"
root="$(pwd -P)"
[ "$src" != "$root" ] || fail 'the repository root is not a site or cache directory'
# The Pages artifact archiver dereferences links outside the declared directory
links="$(find "$src" -type l -print)"
[ -z "$links" ] || fail 'symlinks are not publishable or cacheable'
relative="${src#"$root"/}"
printf 'path=%s\nrelative-path=%s\n' "$src" "$relative" >> "$GITHUB_OUTPUT"
if [ "$PURPOSE" = cache ]; then
  namespace="$(printf '%s' "$relative" | git hash-object --stdin)"
  printf 'cache-prefix=pages-cache-v1-%s-%s-%s\n' "$RUNNER_OS" "$RUNNER_ARCH" "$namespace" >> "$GITHUB_OUTPUT"
fi
