#!/bin/sh
# Петля ловит ошибки поштучно. Классы ошибок ловит ретро.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DATE=$(date +%Y-%m-%d)
ID=

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

[ -e "$FILE" ] && {
    echo "ретро уже существует: $FILE" >&2
    echo "→ сделай: открой этот файл, если продолжаешь ту же ретро; иначе повтори с явным уникальным --id <a-z0-9-hyphen>" >&2
    echo "✓ выбран ровно один исход: существующий файл продолжен либо создан новый уникальный target" >&2
    exit 1
}

TMP=$(mktemp "$ROOT/.harness/retro/.new-retro.XXXXXX")
trap 'rm -f "$TMP"' EXIT HUP INT TERM
cp "$ROOT/.harness/retro/TEMPLATE.md" "$TMP"
sed -i.bak "s/^date:.*/date: $DATE/" "$TMP" && rm -f "$TMP.bak"
if ! ln "$TMP" "$FILE" 2>/dev/null; then
    echo "ретро уже существует: $FILE" >&2
    echo "→ сделай: открой этот файл, если продолжаешь ту же ретро; иначе повтори с явным уникальным --id <a-z0-9-hyphen>" >&2
    echo "✓ выбран ровно один исход: существующий файл продолжен либо создан новый уникальный target" >&2
    exit 1
fi
rm -f "$TMP"
trap - EXIT HUP INT TERM

echo "создано: $FILE"
echo
if [ -f "$ROOT/.harness/scripts/retro-inputs.sh" ]; then
    echo "--------------------------------------------------------------------"
    # Оборван вывод входов — усечённый блок неотличим от полного, и порядок
    # шагов ниже пропал бы вместе с ним. Поэтому провал называется вслух,
    # а печать продолжается.
    sh "$ROOT/.harness/scripts/retro-inputs.sh" || {
        echo
        echo "ВХОДЫ СОБРАНЫ НЕ ПОЛНОСТЬЮ: retro-inputs.sh упал, вывод выше оборван."
        echo "→ команда: sh .harness/scripts/retro-inputs.sh"
    }
    echo "--------------------------------------------------------------------"
else
    echo "retro-inputs.sh нет — входы придётся собрать руками, и числа тогда"
    echo "обязаны идти с командами, которыми получены (AGENTS.md §6)"
fi
echo
echo "Порядок — скилл retro (/retro). Кратко:"
echo "  1. вставь вывод входов выше в раздел «Что перечитано» — как есть"
echo "  2. доведи каждую проблему до класса, а не до случая"
echo "  3. outcome: change + rule: <id>  —  либо outcome: skip + reason: <почему>"
echo "  4. закоммить. Незакоммиченная ретро — незавершённая ретро."
