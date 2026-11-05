#!/usr/bin/env bash
# Pulls a candidate image by digest onto a runner with a Docker daemon, with a Docker config that exists only for the pull.
# The temporary pull config is deleted before returning to the caller
#
# Environment:
#   IMAGE           the candidate, ghcr.io/<owner>/<package>@sha256:<digest>
#   REGISTRY_TOKEN  the job's GITHUB_TOKEN with packages: read
set -euo pipefail
: "${IMAGE:?}" "${REGISTRY_TOKEN:?}" "${RUNNER_TEMP:?}" "${GITHUB_ACTOR:?}"
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

pull_candidate "$IMAGE"
