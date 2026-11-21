# syntax=docker/dockerfile:1
FROM ghcr.io/rubio-enterprises/base:main@sha256:9ba4566d5c98d78085f85600b2257a8db3fe588eb46a32fe7485e7a5b4554849 AS dev
# mise 2026.11.1 reads mise.lock beside a custom config filename
COPY --chmod=0644 image/mise.toml /etc/mise/config.toml
COPY --chmod=0644 image/mise.lock /etc/mise/mise.lock
# Global Check pins load before this absolute repository config
ENV MISE_OVERRIDE_CONFIG_FILENAMES=/etc/mise/config.toml
RUN chmod 0755 /etc/mise && mise install --locked && mise reshim
ENV UV_PYTHON=3.13.9

FROM dev AS ci
COPY --link --from=ghcr.io/rubio-enterprises/runner:main@sha256:e265976a02d2904b4b00caa3c9c55da5b45120a7ebf43f740279e573bf281f5b --chown=1000:1000 /home/runner /opt/actions-runner
WORKDIR /opt/actions-runner
RUN --mount=type=bind,source=.,target=/src,ro \
    mkdir -p /workspaces/bootstrap && cp -a /src/. /workspaces/bootstrap/ && \
    cd /workspaces/bootstrap && bin/setup.sh && rm -rf /workspaces/bootstrap
