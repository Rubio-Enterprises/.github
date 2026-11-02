# check=skip=InvalidDefaultArgInFrom
# Runtime and cache smoke checks, run as build stages on the Build Service. A stage's RUN has no network and no job-local
# container runtime exists, so these replace the old `docker run` checks. build-service.sh builds this with
# --no-cache-filter on every stage so each check executes on every build, and outputs only the final stage's markers.
# BuildKit RUN steps cannot carry the old `--memory 1g --pids-limit 512`; the builder's own cgroup bounds them.
ARG DEV_IMAGE
ARG CI_IMAGE

# The runner payload and environment of the CI image, as the fixed runner identity
FROM ${CI_IMAGE} AS runtime
USER 1000
RUN --network=none <<'SMOKE'
#!/usr/bin/env bash
set -euo pipefail
workspace="$PWD"
cd /
/opt/actions-runner/bin/Runner.Listener --version
mise --version
/usr/bin/gh --version
/usr/bin/jq --version
test "$UV_LINK_MODE" = copy
test -w /usr/local/share/mise
test -w /var/cache/devcontainer
test -w /opt/actions-runner
test "$(id -u)" = 1000
if sudo -n true 2>/dev/null; then exit 1; fi
test -z "${STANDARDS_PIN:-}"
test -z "${NODE_AUTH_TOKEN:-}"
test -z "${STANDARDS_VERSION:-}"
test ! -e "$workspace/.standards/node_modules"
echo ok > /tmp/smoke-ok
SMOKE

# Dev Container identity with the original vscode ownership, without UID migration
FROM ${DEV_IMAGE} AS dev-1000
USER root
RUN --network=none --mount=type=bind,source=cache-smoke.sh,target=/run/cache-smoke.sh \
    bash /run/cache-smoke.sh 1000 && echo ok > /tmp/smoke-ok

# The Dev Container CLI's home-only UID/GID migration
FROM ${DEV_IMAGE} AS dev-1001
USER root
RUN --network=none --mount=type=bind,source=cache-smoke.sh,target=/run/cache-smoke.sh \
    bash /run/cache-smoke.sh 1001 && echo ok > /tmp/smoke-ok

FROM scratch
COPY --from=runtime /tmp/smoke-ok /runtime
COPY --from=dev-1000 /tmp/smoke-ok /dev-1000
COPY --from=dev-1001 /tmp/smoke-ok /dev-1001
