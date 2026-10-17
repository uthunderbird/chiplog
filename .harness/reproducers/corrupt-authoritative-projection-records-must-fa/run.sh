#!/bin/sh
# Verifies the rule through the actual PostgreSQL CLI: no partial corrupt output.
set -eu

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
cd "$ROOT"

if [ -z "${CHIPLOG_TEST_POSTGRES_DSN:-}" ]; then
  echo "corrupt-record reproducer requires a dedicated PostgreSQL test instance; production CHIPLOG_POSTGRES_DSN is not used." >&2
  echo "→ команда: CHIPLOG_TEST_POSTGRES_DSN='<dedicated test instance conninfo>' sh .harness/reproducers/corrupt-authoritative-projection-records-must-fa/run.sh" >&2
  exit 2
fi

# The fixture creates UUID-isolated databases and drops only those databases.
uv run --no-sync pytest -q -p no:timeout \
  tests/cli/test_corrupt_store.py::test_canonical_cli_rejects_corrupt_authority_before_output
