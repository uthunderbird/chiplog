#!/bin/sh
# Verifies the rule: corrupt durable bytes must fail loudly without a partial show.
set -eu

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cd "$ROOT"
export CHIPLOG_R6_OPERATOR_SECRET=reproducer-operator-secret
uv run chiplog --database "$TMP/store.sqlite3" bootstrap \
  --tenant tenant-1 --principal principal-1 --credential credential --session session \
  --database-instance reproducer-store --token reproducer-token >/dev/null
# Historical fixture setup seeds the retained durable format. The corruption
# observation below still uses the canonical R8 CLI, including startup integrity.
uv run python -c 'from chiplog import cli; from chiplog.composition.r7_planning import open_r7_runtime; cli.open_r8_runtime = open_r7_runtime; cli.main()' --database "$TMP/store.sqlite3" create purpose \
  --tenant tenant-1 --principal principal-1 --command-id command-1 \
  --intention-id intention-1 --revision-id revision-1 --authority-act act-1 \
  --credential credential --session session >/dev/null
uv run python -c 'import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); db.execute("UPDATE records SET canonical_bytes = ? WHERE record_id = ?", (b"not-json", "revision-1")); db.commit(); db.close()' "$TMP/store.sqlite3"

OUT="$TMP/out"
if uv run chiplog --database "$TMP/store.sqlite3" show --tenant tenant-1 \
  --principal principal-1 --credential credential --session session >"$OUT" 2>&1; then
  echo "corrupt authoritative record produced successful output" >&2
  exit 1
fi
grep -F 'operation=render tenant=tenant-1 record_id=revision-1' "$OUT" >/dev/null
! grep -F 'tenant=tenant-1 records=5' "$OUT" >/dev/null
