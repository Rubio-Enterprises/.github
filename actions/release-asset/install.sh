#!/usr/bin/env bash
set -euo pipefail
tool=${1:?tool} destination=${2:?destination}
here=$(dirname "${BASH_SOURCE[0]}")
case "$(uname -s)" in Linux) os=Linux ;; Darwin) os=macOS ;; *) echo 'unsupported release platform' >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64|amd64) arch=X64 ;; aarch64|arm64) arch=ARM64 ;; *) echo 'unsupported release architecture' >&2; exit 1 ;; esac
pin=$(jq -er --arg key "$tool-$os-$arch" '.[$key] // error("unsupported release platform")' "$here/pins.json")
read -r repo tag checksum asset member < <(jq -r '[.repo, .tag, .sha256, .asset, .member // "-"] | @tsv' <<< "$pin")
version=${tag#v}
asset=${asset/\{version\}/$version}
existing=${3:-$(command -v "$tool" || true)}
if [[ -n "$existing" ]]; then
  observed=$("$existing" --version 2>&1) || observed=''
  case "$tool" in
    just|trufflehog) expected="$tool $version" ;;
    mise) expected="$version"; observed=${observed%% *} ;;
    *) echo 'unsupported release tool' >&2; exit 1 ;;
  esac
  if [[ "$observed" == "$expected" ]]; then printf '%s\n' "$existing"; exit 0; fi
fi
work=$(mktemp -d "${RUNNER_TEMP:?}/install-release.XXXXXX")
trap 'rm -rf "$work"' EXIT
bash "$here/fetch.sh" "$repo" "$tag" "$asset" "$checksum" "$work/archive"
tar -xzf "$work/archive" -C "$work" "$member"
mkdir -p "$(dirname "$destination")"
mv "$work/$member" "$destination"
chmod 0755 "$destination"
printf '%s\n' "$destination"
