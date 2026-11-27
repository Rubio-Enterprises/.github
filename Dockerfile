# syntax=docker/dockerfile:1@sha256:572cebbc2b15e6f84c077742952674889cb63cd5e9b80abdffcbe587b6f19183
FROM ghcr.io/rubio-enterprises/base:main@sha256:273f3fcaeaca36f6b60aaddaa96a710fb8de122533a13e6c49b153075809c1f7 AS dev
# mise 2026.11.1 reads mise.lock beside a custom config filename
COPY --chmod=0644 image/mise.toml /etc/mise/config.toml
COPY --chmod=0644 image/mise.lock /etc/mise/mise.lock
# Global Check pins load before this absolute repository config
ENV MISE_OVERRIDE_CONFIG_FILENAMES=/etc/mise/config.toml
RUN chmod 0755 /etc/mise && mise install --locked && mise reshim
ENV UV_PYTHON=3.15.2

FROM dev AS ci
COPY --link --from=ghcr.io/rubio-enterprises/runner:main@sha256:44b3f58d3ad97a08b550c96042ce2a6505dec684ec119c62362a2beb6b9766ca --chown=1000:1000 /home/runner /opt/actions-runner
WORKDIR /opt/actions-runner
RUN --mount=type=bind,source=.,target=/src,ro \
    mkdir -p /workspaces/bootstrap && cp -a /src/. /workspaces/bootstrap/ && \
    cd /workspaces/bootstrap && bin/setup.sh && rm -rf /workspaces/bootstrap
