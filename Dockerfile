# syntax=docker/dockerfile:1
FROM ghcr.io/rubio-enterprises/base:main@sha256:88d8310859df133fba54758d500a0bfd7e2631bb78867aa4fb7ea36342cb1495 AS dev
# mise 2026.11.1 reads mise.lock beside a custom config filename
COPY --chmod=0644 image/mise.toml /etc/mise/config.toml
COPY --chmod=0644 image/mise.lock /etc/mise/mise.lock
# Global Check pins load before this absolute repository config
ENV MISE_OVERRIDE_CONFIG_FILENAMES=/etc/mise/config.toml
RUN chmod 0755 /etc/mise && mise install --locked && mise reshim
ENV UV_PYTHON=3.13.9

FROM dev AS ci
COPY --link --from=ghcr.io/rubio-enterprises/runner:main@sha256:254c5dabce23dd9a8e703ee47ef34e17c5d77c087ff1ada862bb0bea794a8d4f --chown=1000:1000 /home/runner /opt/actions-runner
WORKDIR /opt/actions-runner
RUN --mount=type=bind,source=.,target=/src,ro \
    mkdir -p /workspaces/bootstrap && cp -a /src/. /workspaces/bootstrap/ && \
    cd /workspaces/bootstrap && bin/setup.sh && rm -rf /workspaces/bootstrap
