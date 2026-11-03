#!/usr/bin/env bash
# Deletes stale candidate images from a GHCR package: versions whose every tag is a candidate tag and that are older than
# the time to live. A candidate is a single manifest (build.sh builds it without attestations), so deleting its version
# removes the whole image. Versions that also carry a real tag are never touched. Failure only warns: a leftover
# candidate costs a little storage, and a failed prune must not fail a build that already succeeded.
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
for page in 1 2 3; do
  status="$(github --output "$state/versions.json" --write-out '%{http_code}' \
    "$api/orgs/$owner/packages/container/$encoded/versions?per_page=100&page=$page")" || status=000
  if [[ "$status" != 200 ]]; then
    echo "::warning::Could not list $package versions (HTTP $status); skipping the candidate prune"
    exit 0
  fi
  while IFS= read -r id; do
    if [[ -n "$id" ]]; then stale+=("$id"); fi
  done < <(jq --raw-output --argjson cutoff "$cutoff" '.[] |
    select((.metadata.container.tags | length) > 0 and
           all(.metadata.container.tags[]; test("^candidate-[0-9]+-[0-9]+$")) and
           ((.created_at | fromdateiso8601) < $cutoff)) | .id' "$state/versions.json")
  if [[ "$(jq length "$state/versions.json")" -lt 100 ]]; then break; fi
done

for id in ${stale[@]+"${stale[@]}"}; do
  status="$(github --request DELETE --output /dev/null --write-out '%{http_code}' \
    "$api/orgs/$owner/packages/container/$encoded/versions/$id")" || status=000
  if [[ "$status" == 204 ]]; then
    echo "Deleted stale candidate version $id of $package"
  else
    echo "::warning::Could not delete candidate version $id of $package (HTTP $status)"
  fi
done
echo "Pruned ${#stale[@]} stale candidate version(s) of $package"
rm -f "$state/versions.json"
