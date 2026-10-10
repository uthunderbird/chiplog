#!/bin/sh
# Петля ловит ошибки поштучно. Классы ошибок ловит ретро.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DATE=$(date +%Y-%m-%d)
ID=

has_complete_inputs() {
    file=$1
    [ "$(grep -Fxc '1. КОПИЛКА  .harness/observations.md' "$file" || true)" -eq 1 ] &&
    [ "$(grep -Ec '^2\. ДИФФЫ  ([[:xdigit:]]{7,40}(\.\.HEAD)?|вся история)$' "$file" || true)" -eq 1 ] &&
    [ "$(grep -Fxc '3. ХВОСТЫ  .harness/handoff.md' "$file" || true)" -eq 1 ] &&
    [ "$(grep -Fxc '4. КЕЙСЫ  .harness/reproducers/*/case.md' "$file" || true)" -eq 1 ] &&
    [ "$(grep -Fxc '5. ИНВАРИАНТЫ И ДОЛГИ' "$file" || true)" -eq 1 ] &&
    [ "$(grep -Fxc 'RETRO_INPUTS_COMPLETE=1' "$file" || true)" -eq 1 ]
}

if [ "$#" -gt 0 ]; then
    [ "$#" -eq 2 ] && [ "$1" = "--id" ] || {
        echo "→ команда: $0 [--id <a-z0-9-hyphen>]" >&2
        exit 2
    }
    ID=$2
    case "$ID" in
        ''|*[!a-z0-9-]*|-*|*-)
            echo "→ сделай: выбери id из строчных букв, цифр и внутренних дефисов" >&2
            echo "✓ id совпадает с [a-z0-9][a-z0-9-]*[a-z0-9] либо состоит из одного символа" >&2
            exit 2
            ;;
    esac
fi

SUFFIX=${ID:+-$ID}
FILE="$ROOT/.harness/retro/$DATE$SUFFIX.md"

if [ -e "$FILE" ] || [ -L "$FILE" ]; then
    echo "ретро уже существует: $FILE" >&2
    echo "→ сделай: открой этот файл, если продолжаешь ту же ретро; иначе повтори с явным уникальным --id <a-z0-9-hyphen>" >&2
    echo "✓ выбран ровно один исход: существующий файл продолжен либо создан новый уникальный target" >&2
    exit 1
fi

TEMPLATE="$ROOT/.harness/retro/TEMPLATE.md"
INPUTS_SCRIPT="$ROOT/.harness/scripts/retro-inputs.sh"
TMP=$(mktemp "$ROOT/.harness/retro/.new-retro.XXXXXX")
INPUTS_TMP=
ERRORS_TMP=
RENDER_TMP=
cleanup() {
    rm -f "$TMP"
    [ -z "$INPUTS_TMP" ] || rm -f "$INPUTS_TMP"
    [ -z "$ERRORS_TMP" ] || rm -f "$ERRORS_TMP"
    [ -z "$RENDER_TMP" ] || rm -f "$RENDER_TMP"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cp "$TEMPLATE" "$TMP"
chmod 644 "$TMP"
if ! python3 - "$DATE" "$TMP" <<'PY'
from pathlib import Path
import re
import sys

date, path = sys.argv[1], Path(sys.argv[2])
template = path.read_bytes()
updated, count = re.subn(rb"(?m)^date:.*$", b"date: " + date.encode("ascii"), template)
if count != 1:
    raise SystemExit("template must contain exactly one date field")
path.write_bytes(updated)
PY
then
    echo "→ команда: проверь date-поле в .harness/retro/TEMPLATE.md и повтори new-retro.sh" >&2
    exit 1
fi

if [ -f "$INPUTS_SCRIPT" ]; then
    INPUTS_TMP=$(mktemp "$ROOT/.harness/retro/.retro-inputs.XXXXXX")
    ERRORS_TMP=$(mktemp "$ROOT/.harness/retro/.retro-errors.XXXXXX")
    if sh "$INPUTS_SCRIPT" >"$INPUTS_TMP" 2>"$ERRORS_TMP"; then
        INPUTS_STATUS=0
    else
        INPUTS_STATUS=$?
    fi

    if [ "$INPUTS_STATUS" -eq 0 ]; then
        if ! has_complete_inputs "$INPUTS_TMP"; then
            echo "ВХОДЫ СОБРАНЫ НЕПОЛНОСТЬЮ: отсутствует одна или несколько секций либо завершающий маркер." >&2
            echo "→ сделай: проверь вывод ниже, восстанови недостающую секцию и повтори сбор до создания ретро." >&2
            echo "✓ запись создаётся только при наличии ровно пяти секций и RETRO_INPUTS_COMPLETE=1" >&2
            cat "$INPUTS_TMP"
            cat "$ERRORS_TMP" >&2
            exit 1
        fi

        RENDER_TMP=$(mktemp "$ROOT/.harness/retro/.new-retro-rendered.XXXXXX")
        if ! python3 - "$TMP" "$INPUTS_TMP" "$RENDER_TMP" <<'PY'
from pathlib import Path
import sys

template_path, inputs_path, output_path = map(Path, sys.argv[1:])
template = template_path.read_bytes()
inputs = inputs_path.read_bytes()
placeholder = "(вывод retro-inputs.sh)".encode("utf-8")
if template.count(placeholder) != 1:
    raise SystemExit("template must contain exactly one retro-inputs placeholder")
output_path.write_bytes(template.replace(placeholder, inputs))
PY
        then
            echo "→ команда: проверь .harness/retro/TEMPLATE.md и повтори new-retro.sh" >&2
            exit 1
        fi
        mv "$RENDER_TMP" "$TMP"
    else
        echo "ВХОДЫ СОБРАНЫ НЕПОЛНОСТЬЮ: retro-inputs.sh завершился со статусом $INPUTS_STATUS." >&2
        echo "→ сделай: устрани указанную ошибку и повтори sh .harness/scripts/new-retro.sh" >&2
        echo "✓ запись не создаётся из частичного stdout; статус отказа сохраняется" >&2
        cat "$INPUTS_TMP"
        cat "$ERRORS_TMP" >&2
        exit "$INPUTS_STATUS"
    fi
else
    echo "СБОР ВХОДОВ НЕВОЗМОЖЕН: отсутствует .harness/scripts/retro-inputs.sh." >&2
    echo "→ сделай: восстанови сборщик пяти входов и повтори new-retro.sh" >&2
    echo "✓ запись не создаётся без полного снимка входов" >&2
    exit 1
fi

# `ln` публикует уже готовый снимок атомарно и не перезаписывает цель,
# даже если она появилась после ранней проверки существования.
chmod 644 "$TMP"
if ! ln "$TMP" "$FILE" 2>/dev/null; then
    echo "ретро уже существует: $FILE" >&2
    echo "→ сделай: открой этот файл, если продолжаешь ту же ретро; иначе повтори с явным уникальным --id <a-z0-9-hyphen>" >&2
    echo "✓ выбран ровно один исход: существующий файл продолжен либо создан новый уникальный target" >&2
    exit 1
fi

echo "создано: $FILE"
if [ -s "$ERRORS_TMP" ]; then
    cat "$ERRORS_TMP" >&2
fi
echo
echo "--------------------------------------------------------------------"
cat "$INPUTS_TMP"
echo "--------------------------------------------------------------------"
echo
echo "Порядок — скилл retro (/retro). Кратко:"
echo "  1. проверь встроенный снимок входов в созданной записи"
echo "  2. доведи каждую проблему до класса, а не до случая"
echo "  3. outcome: change + rule: <id>  —  либо outcome: skip + reason: <почему>"
echo "  4. закоммить. Незакоммиченная ретро — незавершённая ретро."
