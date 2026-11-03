# check=skip=InvalidDefaultArgInFrom
# Cache smoke checks of the dev image, run as build stages on the Build Service. A stage's RUN has no network and no
# job-local container runtime exists, so these replace the old `docker run` checks. build-service.sh builds this with
# --no-cache-filter on every stage so each check executes on every build, and outputs only the final stage's markers.
# BuildKit RUN steps cannot carry the old `--memory 1g --pids-limit 512`; the builder's own cgroup bounds them.
# The CI image's runtime and cache checks are not here: `verify` proves its repository layers and config identical to the
# dev image's, so they run once per base pair release in Rubio-Enterprises/devcontainers.
ARG DEV_IMAGE

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
COPY --from=dev-1000 /tmp/smoke-ok /dev-1000
COPY --from=dev-1001 /tmp/smoke-ok /dev-1001
