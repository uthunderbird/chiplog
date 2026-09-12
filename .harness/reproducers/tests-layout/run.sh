#!/bin/sh
set -eu
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$ROOT"
exec sh .harness/scripts/clean-git-env.sh uv run pytest -q tests/tooling/test_tests_layout.py
