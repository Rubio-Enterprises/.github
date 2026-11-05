set positional-arguments
export REPO_DIR := justfile_directory()
export GIT_COMMON_DIR := `git rev-parse --path-format=absolute --git-common-dir`

_default:
    @just --list

build:
    @docker volume create rubio-uv-cache >/dev/null
    @docker volume create rubio-npm-cache >/dev/null
    docker compose build --pull dev

setup: build
    docker compose run --rm -T dev bin/setup.sh "$@" </dev/null

check:
    docker compose run --rm -T dev bin/check.sh "$@" </dev/null

fmt:
    docker compose run --rm -T dev standards fmt "$@" </dev/null

check-pr:
    docker compose run --rm -T dev standards check-pr "$@" </dev/null

test *args:
    docker compose run --rm -T dev bin/test.sh "$@" </dev/null

shell:
    docker compose run --rm dev bash

exec *args:
    docker compose run --rm -T dev "$@" </dev/null
