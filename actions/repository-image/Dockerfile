# syntax=docker/dockerfile:1
# check=skip=InvalidDefaultArgInFrom
ARG BASE
FROM ${BASE}
ARG REPO
USER root
ENV MISE_PYTHON_COMPILE=0 \
    UV_CACHE_DIR=/var/cache/devcontainer/.cache/uv \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/usr/local/share/mise/python \
    pnpm_config_store_dir=/var/cache/devcontainer/pnpm-store \
    npm_config_cache=/var/cache/devcontainer/_cacache \
    GOMODCACHE=/var/cache/devcontainer/gomod \
    GOFLAGS=-modcacherw
RUN install -d -o vscode -g vscode /usr/local/share/mise /var/cache/devcontainer
USER vscode
RUN --mount=type=bind,from=pins,target=/workspaces/.mise-pins \
    --mount=type=bind,from=standards,target=/workspaces/.mise-pins/.standards-config \
    --mount=type=bind,from=expected,target=/run/expected \
    cd /workspaces/.mise-pins \
 && if [ -f .mise.toml ] || [ -f mise.toml ]; then \
      export STANDARDS_PIN="$(cat /run/expected/pin)" MISE_TRUSTED_CONFIG_PATHS=/workspaces/.mise-pins; \
      git -c safe.directory="$PWD/.standards-config" -C .standards-config diff --quiet HEAD \
      && test "$(git -c safe.directory="$PWD/.standards-config" -C .standards-config rev-parse HEAD)" = "$STANDARDS_PIN" \
      && git config --global --add safe.directory "$PWD/.standards-config" \
      && mise bootstrap repos status --missing \
      && CI=true MISE_YES=1 mise install --system; \
    fi \
 && rm -rf /home/vscode/.cache/mise /home/vscode/.local/state
ENV PATH=/usr/local/share/mise/shims:${PATH}
COPY --chown=vscode:vscode --from=src / /workspaces/${REPO}/
WORKDIR /workspaces/${REPO}
RUN --mount=type=bind,from=standards,target=/workspaces/${REPO}/.standards-config \
    --mount=type=bind,from=expected,target=/run/expected \
    if [ -f .mise.toml ] || [ -f mise.toml ]; then \
      export STANDARDS_PIN="$(cat /run/expected/pin)"; \
      git config --global --add safe.directory "$PWD/.standards-config" \
      && test "$(git -C .standards-config rev-parse HEAD)" = "$STANDARDS_PIN" \
      && mise trust -q && mise bootstrap repos status --missing \
      && CI=true MISE_YES=1 mise run setup; \
    fi \
 && rm -rf /home/vscode/.cache/mise
