#!/bin/sh
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
[ "$#" -eq 5 ] || {
    echo "  → команда: ./.harness/scripts/close-defect.sh <id> resolved|retired|moved <actor> <full-revision> <evidence>" >&2
    exit 2
}
exec python3 "$ROOT/.harness/scripts/deferred.py" "$ROOT" close "$1" "$2" "$3" "$4" "$5"
