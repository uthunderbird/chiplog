#!/bin/sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[ "$#" -eq 3 ] || {
    echo "  → команда: ./.harness/scripts/take-defect.sh <id> <actor> <full-revision>" >&2
    exit 2
}
exec python3 "$ROOT/.harness/scripts/deferred.py" "$ROOT" take "$1" "$2" "$3"
