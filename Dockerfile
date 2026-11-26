# syntax=docker/dockerfile:1
FROM ghcr.io/rubio-enterprises/base:main@sha256:4f33adf26c233309126abf159fe5c89b53a0b2251cd5187d62c2fadbac482b12 AS dev
# mise 2026.11.1 reads mise.lock beside a custom config filename
COPY --chmod=0644 image/mise.toml /etc/mise/config.toml
COPY --chmod=0644 image/mise.lock /etc/mise/mise.lock
# Global Check pins load before this absolute repository config
ENV MISE_OVERRIDE_CONFIG_FILENAMES=/etc/mise/config.toml
RUN chmod 0755 /etc/mise && mise install --locked && mise reshim
ENV UV_PYTHON=3.15.2

FROM dev AS ci
COPY --link --from=ghcr.io/rubio-enterprises/runner:main@sha256:7bd552aee2ad36a14057a958d66e322910c69569d857cdca82a7520ebed7afd7 --chown=1000:1000 /home/runner /opt/actions-runner
WORKDIR /opt/actions-runner
RUN --mount=type=bind,source=.,target=/src,ro \
    mkdir -p /workspaces/bootstrap && cp -a /src/. /workspaces/bootstrap/ && \
    cd /workspaces/bootstrap && bin/setup.sh && rm -rf /workspaces/bootstrap
