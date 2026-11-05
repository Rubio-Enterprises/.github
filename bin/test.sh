#!/bin/sh
set -eu
exec uv run --frozen pytest tests/ --junitxml=reports/test/junit.xml "$@"
