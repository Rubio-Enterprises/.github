# syntax=docker/dockerfile:1
# check=skip=InvalidDefaultArgInFrom
ARG BASE
FROM ${BASE}
ARG REPO
USER root
# Dev Container UID remapping re-owns only the remote user's home
ENV MISE_PYTHON_COMPILE=0 \
    MISE_CACHE_DIR=/home/vscode/.cache/mise \
    UV_CACHE_DIR=/home/vscode/.cache/uv \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/home/vscode/.local/share/uv/python \
    CARGO_HOME=/home/vscode/.cache/cargo \
    RUSTUP_HOME=/home/vscode/.local/share/rustup \
    pnpm_config_store_dir=/home/vscode/.cache/pnpm-store \
    npm_config_cache=/home/vscode/.cache/npm \
    GOMODCACHE=/home/vscode/.cache/gomod \
    GOFLAGS=-modcacherw
RUN install -d -o vscode -g vscode /usr/local/share/mise \
 && runuser -u vscode -- mkdir -p \
    "$MISE_CACHE_DIR" "$UV_CACHE_DIR" "$pnpm_config_store_dir" "$npm_config_cache" "$GOMODCACHE" \
    "$CARGO_HOME" "$RUSTUP_HOME" "$UV_PYTHON_INSTALL_DIR"
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
