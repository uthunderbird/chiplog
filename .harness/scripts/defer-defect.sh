#!/bin/sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[ "$#" -eq 1 ] || {
    echo "  → команда: ./.harness/scripts/defer-defect.sh <admission-file>" >&2
    exit 2
}
exec python3 "$ROOT/.harness/scripts/deferred.py" "$ROOT" defer "$1"
