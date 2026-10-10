#!/bin/sh
set -eu

SOURCE_ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fixture() {
    name=$1
    root="$TMP/$name"
    mkdir -p "$root/.harness/retro" "$root/.harness/scripts"
    cp "$SOURCE_ROOT/.harness/scripts/new-retro.sh" "$root/.harness/scripts/new-retro.sh"
    cp "$SOURCE_ROOT/.harness/retro/TEMPLATE.md" "$root/.harness/retro/TEMPLATE.md"
    printf '%s\n' "$root"
}

assert_no_temporary_files() {
    directory=$1
    if find "$directory" \( -name '.new-retro.*' -o -name '.retro-inputs.*' -o -name '.retro-errors.*' -o -name '.new-retro-rendered.*' \) -print | grep -q .; then
        echo "temporary retro-generation files were left behind" >&2
        return 1
    fi
}

DATE=$(date +%Y-%m-%d)
SUCCESS_ROOT=$(fixture success)
cat > "$SUCCESS_ROOT/.harness/scripts/retro-inputs.sh" <<'SH'
#!/bin/sh
set -eu
printf x >> "$RETRO_TEST_CALLS"
if [ -n "${RETRO_TEST_RACE_TARGET:-}" ]; then
    printf 'raced target\n' > "$RETRO_TEST_RACE_TARGET"
fi
printf '1. КОПИЛКА  .harness/observations.md\n'
printf '2. ДИФФЫ  abcdef0..HEAD\n'
printf '3. ХВОСТЫ  .harness/handoff.md\n'
printf '4. КЕЙСЫ  .harness/reproducers/*/case.md\n'
printf '5. ИНВАРИАНТЫ И ДОЛГИ\nline with trailing spaces  \nRETRO_INPUTS_COMPLETE=1\n'
SH
EXPECTED="$TMP/expected.txt"
printf '1. КОПИЛКА  .harness/observations.md\n2. ДИФФЫ  abcdef0..HEAD\n3. ХВОСТЫ  .harness/handoff.md\n4. КЕЙСЫ  .harness/reproducers/*/case.md\n5. ИНВАРИАНТЫ И ДОЛГИ\nline with trailing spaces  \nRETRO_INPUTS_COMPLETE=1\n' > "$EXPECTED"
CALLS="$TMP/success-calls"
RETRO_TEST_CALLS=$CALLS sh "$SUCCESS_ROOT/.harness/scripts/new-retro.sh" --id success > "$TMP/success.stdout"
[ "$(wc -c < "$CALLS" | tr -d ' ')" -eq 1 ]
SUCCESS_FILE="$SUCCESS_ROOT/.harness/retro/$DATE-success.md"
python3 - "$SUCCESS_FILE" "$EXPECTED" <<'PY'
from pathlib import Path
import stat
import sys

record = Path(sys.argv[1]).read_bytes()
expected = Path(sys.argv[2]).read_bytes()
mode = stat.S_IMODE(Path(sys.argv[1]).stat().st_mode)
assert mode == 0o644, oct(mode)
begin = b"```text\n"
end = b"\n```\n"
embedded = record.split(begin, 1)[1].split(end, 1)[0]
assert embedded == expected, (embedded, expected)
PY
assert_no_temporary_files "$SUCCESS_ROOT/.harness/retro"

FIRST_ROOT=$(fixture first-retro)
cat > "$FIRST_ROOT/.harness/scripts/retro-inputs.sh" <<'SH'
#!/bin/sh
printf '1. КОПИЛКА  .harness/observations.md\n2. ДИФФЫ  вся история\n3. ХВОСТЫ  .harness/handoff.md\n4. КЕЙСЫ  .harness/reproducers/*/case.md\n5. ИНВАРИАНТЫ И ДОЛГИ\nRETRO_INPUTS_COMPLETE=1\n'
SH
sh "$FIRST_ROOT/.harness/scripts/new-retro.sh" --id first > "$TMP/first.stdout"
grep -Fq '2. ДИФФЫ  вся история' "$FIRST_ROOT/.harness/retro/$DATE-first.md"
assert_no_temporary_files "$FIRST_ROOT/.harness/retro"

FAIL_ROOT=$(fixture failure)
cat > "$FAIL_ROOT/.harness/scripts/retro-inputs.sh" <<'SH'
#!/bin/sh
printf 'partial input\n'
printf 'metric source failed\n' >&2
exit 23
SH
if sh "$FAIL_ROOT/.harness/scripts/new-retro.sh" --id failure > "$TMP/failure.stdout" 2> "$TMP/failure.stderr"; then
    echo "partial inputs unexpectedly created a retro" >&2
    exit 1
else
    status=$?
fi
[ "$status" -eq 23 ]
[ ! -e "$FAIL_ROOT/.harness/retro/$DATE-failure.md" ]
grep -Fq 'partial input' "$TMP/failure.stdout"
grep -Fq 'metric source failed' "$TMP/failure.stderr"
grep -Fq 'ВХОДЫ СОБРАНЫ НЕПОЛНОСТЬЮ' "$TMP/failure.stderr"
assert_no_temporary_files "$FAIL_ROOT/.harness/retro"

INCOMPLETE_ROOT=$(fixture incomplete)
cat > "$INCOMPLETE_ROOT/.harness/scripts/retro-inputs.sh" <<'SH'
#!/bin/sh
printf '1. КОПИЛКА  .harness/observations.md\nRETRO_INPUTS_COMPLETE=1\n'
SH
if sh "$INCOMPLETE_ROOT/.harness/scripts/new-retro.sh" --id incomplete > "$TMP/incomplete.stdout" 2> "$TMP/incomplete.stderr"; then
    echo "missing input sections unexpectedly created a retro" >&2
    exit 1
fi
[ ! -e "$INCOMPLETE_ROOT/.harness/retro/$DATE-incomplete.md" ]
grep -Fq 'отсутствует одна или несколько секций' "$TMP/incomplete.stderr"
assert_no_temporary_files "$INCOMPLETE_ROOT/.harness/retro"

COLLISION_ROOT=$(fixture collision)
cat > "$COLLISION_ROOT/.harness/scripts/retro-inputs.sh" <<'SH'
#!/bin/sh
printf x >> "$RETRO_TEST_CALLS"
printf '1. КОПИЛКА  .harness/observations.md\n2. ДИФФЫ  abcdef0\n3. ХВОСТЫ  .harness/handoff.md\n4. КЕЙСЫ  .harness/reproducers/*/case.md\n5. ИНВАРИАНТЫ И ДОЛГИ\nRETRO_INPUTS_COMPLETE=1\n'
SH
COLLISION_FILE="$COLLISION_ROOT/.harness/retro/$DATE-collision.md"
printf 'existing target\n' > "$COLLISION_FILE"
if RETRO_TEST_CALLS="$TMP/collision-calls" sh "$COLLISION_ROOT/.harness/scripts/new-retro.sh" --id collision > "$TMP/collision.stdout" 2> "$TMP/collision.stderr"; then
    echo "existing retro target unexpectedly overwritten" >&2
    exit 1
fi
grep -Fxq 'existing target' "$COLLISION_FILE"
[ ! -e "$TMP/collision-calls" ]
assert_no_temporary_files "$COLLISION_ROOT/.harness/retro"

RACE_ROOT=$(fixture race)
cat > "$RACE_ROOT/.harness/scripts/retro-inputs.sh" <<'SH'
#!/bin/sh
printf 'raced target\n' > "$RETRO_TEST_RACE_TARGET"
printf '1. КОПИЛКА  .harness/observations.md\n2. ДИФФЫ  abcdef0\n3. ХВОСТЫ  .harness/handoff.md\n4. КЕЙСЫ  .harness/reproducers/*/case.md\n5. ИНВАРИАНТЫ И ДОЛГИ\nRETRO_INPUTS_COMPLETE=1\n'
SH
RACE_FILE="$RACE_ROOT/.harness/retro/$DATE-race.md"
if RETRO_TEST_RACE_TARGET=$RACE_FILE sh "$RACE_ROOT/.harness/scripts/new-retro.sh" --id race > "$TMP/race.stdout" 2> "$TMP/race.stderr"; then
    echo "raced retro target unexpectedly overwritten" >&2
    exit 1
fi
grep -Fxq 'raced target' "$RACE_FILE"
assert_no_temporary_files "$RACE_ROOT/.harness/retro"

echo "new-retro generation checks passed"
