# syntax=docker/dockerfile:1
FROM ghcr.io/rubio-enterprises/base:main@sha256:139727bb5b2e770840e839aff0e82e8507e87f590c166cfd005b12bac7a9e9eb AS dev
# mise 2026.11.1 reads mise.lock beside a custom config filename
COPY --chmod=0644 image/mise.toml /etc/mise/config.toml
COPY --chmod=0644 image/mise.lock /etc/mise/mise.lock
# Global Check pins load before this absolute repository config
ENV MISE_OVERRIDE_CONFIG_FILENAMES=/etc/mise/config.toml
RUN chmod 0755 /etc/mise && mise install --locked && mise reshim
ENV UV_PYTHON=3.13.9

FROM dev AS ci
COPY --link --from=ghcr.io/rubio-enterprises/runner:main@sha256:5527b979fbdaa2a55808615febf03d1e42344254ed6bbd0cdb87003723df4ee1 --chown=1000:1000 /home/runner /opt/actions-runner
WORKDIR /opt/actions-runner
RUN --mount=type=bind,source=.,target=/src,ro \
    mkdir -p /workspaces/bootstrap && cp -a /src/. /workspaces/bootstrap/ && \
    cd /workspaces/bootstrap && bin/setup.sh && rm -rf /workspaces/bootstrap
