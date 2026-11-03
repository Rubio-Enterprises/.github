#!/usr/bin/env bash
# Deletes stale candidate images from a GHCR package: versions whose every tag is a candidate tag and that are older than
# the time to live. The token can delete a version only when the repository holds the Admin role on the package (Manage
# Actions access); with Write it can push but every delete is refused, which only warns. A candidate with attestations is an
# index whose two children (the image and its attestations) are untagged versions of their own, so each stale candidate takes
# its untagged children with it; a child is found by the digest its index lists. A candidate that was promoted also carries its
# final tags and is never touched, nor are its children. Failure only warns: a leftover candidate costs a little storage, and a
# failed prune must not fail a build that already succeeded.
#
# Environment:
#   IMAGE      untagged GHCR image, ghcr.io/<owner>/<package>
#   GH_TOKEN   the job's GITHUB_TOKEN with packages: write
#   TTL_HOURS  age at which a candidate is stale (default 24: longer than any job that uses one)
set -euo pipefail
: "${IMAGE:?}" "${GH_TOKEN:?}" "${RUNNER_TEMP:?}" "${GITHUB_REPOSITORY_OWNER:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

api="${GITHUB_API_URL:-https://api.github.com}"
owner="${GITHUB_REPOSITORY_OWNER,,}"
resolve_package "$IMAGE"
package="$PACKAGE"
encoded="${package//\//%2F}"
ttl="${TTL_HOURS:-24}"
[[ "$ttl" =~ ^[1-9][0-9]*$ ]] || fail 'TTL_HOURS must be a positive whole number'
cutoff="$(($(date -u +%s) - ttl * 3600))"
state="$(state_dir)"

# The token reaches curl through its config on stdin, never through a command line
github() {
  printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN" |
    curl --silent --show-error --max-time 30 --config - \
      --header 'Accept: application/vnd.github+json' --header 'X-GitHub-Api-Version: 2022-11-28' "$@"
}

stale=()
stale_digests=()
declare -A untagged=()
# Newest versions come first, but every page is read until a short one so a busy package cannot hide stale candidates
max_pages=100
for ((page = 1; page <= max_pages; page++)); do
  status="$(github --output "$state/versions.json" --write-out '%{http_code}' \
    "$api/orgs/$owner/packages/container/$encoded/versions?per_page=100&page=$page")" || status=000
  if [[ "$status" != 200 ]]; then
    echo "::warning::Could not list $package versions (HTTP $status); skipping the candidate prune"
    exit 0
  fi
  while read -r id name; do
    if [[ -n "$id" ]]; then
      stale+=("$id")
      stale_digests+=("$name")
    fi
  done < <(jq --raw-output --argjson cutoff "$cutoff" '.[] |
    select((.metadata.container.tags | length) > 0 and
           all(.metadata.container.tags[]; test("^candidate-[0-9]+-[0-9]+-[a-f0-9]{12}$")) and
           ((.created_at | fromdateiso8601) < $cutoff)) | "\(.id) \(.name)"' "$state/versions.json")
  while read -r id name; do
    if [[ -n "$id" ]]; then untagged["$name"]="$id"; fi
  done < <(jq --raw-output '.[] | select((.metadata.container.tags | length) == 0) | "\(.id) \(.name)"' "$state/versions.json")
  if [[ "$(jq length "$state/versions.json")" -lt 100 ]]; then break; fi
  if [[ "$page" -eq "$max_pages" ]]; then echo "::warning::Stopped after $max_pages pages of $package versions; older candidates stay until the next run"; fi
done

delete_version() {
  local id="$1" kind="$2" status
  status="$(github --request DELETE --output /dev/null --write-out '%{http_code}' \
    "$api/orgs/$owner/packages/container/$encoded/versions/$id")" || status=000
  if [[ "$status" == 204 ]]; then
    echo "Deleted stale candidate $kind version $id of $package"
    return 0
  fi
  echo "::warning::Could not delete candidate $kind version $id of $package (HTTP $status). Deleting needs the Admin role for this repository in the package's Manage Actions access"
  return 1
}

# The children of a stale index, read from its manifest with the job's own token. An unreadable or unlisted child only leaves
# an orphan behind.
children_of() {
  local digest="$1"
  DOCKER_CONFIG="$docker_config" docker buildx imagetools inspect --raw "$IMAGE@$digest" 2> /dev/null |
    jq --raw-output '.manifests[]?.digest' 2> /dev/null || true
}

if [[ "${#stale[@]}" -gt 0 ]]; then
  ghcr_docker_config "$GH_TOKEN" > /dev/null 2>&1 || echo '::warning::Could not log in to GHCR; the children of stale candidates stay'
  docker_config="${DOCKER_CONFIG:-$state/docker-ghcr}"
fi
removed=0
for index in "${!stale[@]}"; do
  # The index goes first: a half-deleted candidate must never be a tagged index with missing children
  children="$(children_of "${stale_digests[index]}")"
  if delete_version "${stale[index]}" index; then removed=$((removed + 1)); else continue; fi
  while read -r child; do
    if [[ -n "$child" && -n "${untagged[$child]:-}" ]]; then delete_version "${untagged[$child]}" child || true; fi
  done <<< "$children"
done
echo "Pruned $removed stale candidate version(s) of $package"
rm -f "$state/versions.json"
