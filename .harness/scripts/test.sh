#!/bin/sh
# Тесты и эвалы. Адаптер под стек проекта.
#
# Проектный адаптер Chiplog: pytest плюс собственные проверки harness ниже.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
FAILED=0
PREFLIGHT=0
case "$#:$*" in
    0:) ;;
    1:--preflight) PREFLIGHT=1 ;;
    *) echo "→ команда: sh .harness/scripts/test.sh [--preflight]" >&2; exit 2 ;;
esac

: "${UV_CACHE_DIR:=${TMPDIR:-/tmp}/chiplog-uv-cache}"
export UV_CACHE_DIR

# --- самотест гейта: каждая проверка на паре входов --------------------------
# Проверяется критерий и наблюдаемый исход, а не исполнение: плохой вход обязан
# уронить проверку, хороший — пройти.
if ! sh .harness/scripts/gate.sh --self-test >/dev/null 2>&1; then
    echo "    самотест гейта не прошёл" >&2
    echo "    → сделай: прогони sh .harness/scripts/gate.sh --self-test и почини" >&2
    echo "      проверку, которую он назвал. Не вход: вход заведомо плохой или" >&2
    echo "      заведомо хороший, спорить с ним нечем" >&2
    echo "    ✓ каждая проверка падает на плохом входе и проходит на хорошем" >&2
    FAILED=1
fi

# --- входы из .harness/reproducers/ ------------------------------------------
# Правило без прогоняемого входа — суеверие с хорошей памятью. Здесь входы
# прогоняются; агентские кейсы (kind: agent) сюда не входят — их гоняет
# replay.sh вручную, потому что их исход недетерминирован.
for dir in .harness/reproducers/*/; do
    [ -d "$dir" ] || continue
    [ -f "$dir/case.md" ] && continue
    runner="$dir/run.sh"
    [ -f "$runner" ] || continue
    if ! sh "$runner" >/dev/null 2>&1; then
        echo "    $runner: вход упал при действующем правиле" >&2
        echo "    → сделай: почини правило либо сам вход. Контракт такой: run.sh" >&2
        echo "      возвращает ноль, пока правило держит, и ненулевой код, если" >&2
        echo "      правило убрать. Здесь правило на месте, значит сломалось одно" >&2
        echo "      из двух" >&2
        echo "    ✓ sh $runner возвращает ноль при действующем правиле" >&2
        FAILED=1
    fi
done

[ "$FAILED" -eq 0 ] || exit "$FAILED"
# Предварительный успех не заменяет полный гейт коммита.
[ "$PREFLIGHT" -eq 0 ] || exit 0

if ! uv run pytest; then
    echo "    тесты Chiplog не прошли" >&2
    echo "    → команда: uv run pytest" >&2
    exit 1
fi
