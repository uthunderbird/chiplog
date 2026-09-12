#!/bin/sh
# Consumer-side validation of the public deferred-defect boundary.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

for name in defer-defect take-defect close-defect; do
    script="$ROOT/.harness/scripts/$name.sh"
    [ -x "$script" ] || {
        echo "missing executable public command: $script" >&2
        exit 1
    }

    before=$(find "$ROOT/.harness/deferred" -maxdepth 1 -type f -print | sort)
    if "$script" contract-probe >"$TMP/$name.out" 2>"$TMP/$name.err"; then
        echo "$name command reported false success for a contract probe" >&2
        exit 1
    fi
    after=$(find "$ROOT/.harness/deferred" -maxdepth 1 -type f -print | sort)
    [ "$before" = "$after" ] || {
        echo "$name command changed the ledger for a rejected contract probe" >&2
        exit 1
    }
done

README="$ROOT/.harness/deferred/README.md"
for token in \
    'ratified_disposition: non-blocking' \
    'ratification_locator:' \
    'ratification_sha256:' \
    'observed_revision:' \
    'finding_sha256:' \
    'target_sha256:' \
    'evidence_sha256:' \
    'expected_verdict:' \
    'admission_sha256:' \
    'unknown: `stop`' \
    'open`, `active`, `resolved`, `retired`, `moved'; do
    grep -Fq "$token" "$README" || {
        echo "deferred contract is missing: $token" >&2
        exit 1
    }
done

echo "deferred contract shape: ok"
