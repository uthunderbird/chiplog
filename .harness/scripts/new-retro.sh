#!/bin/sh
# Петля ловит ошибки поштучно. Классы ошибок ловит ретро.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DATE=$(date +%Y-%m-%d)
FILE="$ROOT/.harness/retro/$DATE.md"

[ -e "$FILE" ] && { echo "ретро за сегодня уже есть: $FILE" >&2; exit 1; }

cp "$ROOT/.harness/retro/TEMPLATE.md" "$FILE"
sed -i.bak "s/^date:.*/date: $DATE/" "$FILE" && rm -f "$FILE.bak"

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
