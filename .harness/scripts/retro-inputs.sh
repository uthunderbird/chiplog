#!/bin/sh
# Пять входов ретро одним прогоном.
#
# Собирать их разовой командой — способ получить числа, которых потом не
# воспроизвести: именно так в первую ретро попали 552 строки вместо 576
# и 43 сообщения вместо 51. Поэтому каждое число печатается вместе с командой,
# которой получено, — вывод вставляется в запись ретро как есть, и колонка
# «как проверить» заполняется сама (AGENTS.md §6).
#
# Команда исполняется той же строкой, которая печатается (`num`). Держать их
# порознь бесполезно: расходятся они молча, а печатное обещание «вот команда»
# остаётся. Дважды за один вечер разошлись — сначала регексп копилки, потом
# путь до питоновских чеков.
#
# Полный гейт с адаптерами отработает на коммите. Самотест отдельно запускает
# harness-пары и ограниченные тесты fast/контрактов адаптеров, не полный набор.
set -eu
# POSIX sh has no pipeline failure propagation; preserve the sh entrypoint.
if ! (set -o pipefail) 2>/dev/null; then
    exec bash "$0" "$@"
fi
set -o pipefail
source_failed() {
    echo "→ сделай: устрани ошибку источника выше и повтори sh .harness/scripts/retro-inputs.sh" >&2
    echo "✓ сборщик завершился успешно и напечатал RETRO_INPUTS_COMPLETE=1" >&2
}
trap source_failed ERR

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
C=.harness/scripts/checks

# Значение и команда, которой оно получено. Команда одна: исполняемая и печатная.
# Пустой вывод не оставляем пустым местом: пробел на позиции числа читается как
# число, которого никто не заметил.
num() {
    _v=$(eval "$2")
    if [ -z "$_v" ]; then
        if [ "$#" -lt 3 ]; then
            echo "→ сделай: восстанови непустой вывод источника: $2" >&2
            echo "✓ команда источника завершилась успешно с непустым stdout" >&2
            exit 1
        fi
        _v=$3
    fi
    printf '   %s%s   (%s)\n' "$1" "$_v" "$2"
}
# Поле из вывода `--status` чека.
st()  { printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1; }

# --- период ------------------------------------------------------------------
# TEMPLATE.md и README.md лежат в том же каталоге и правятся по своим поводам —
# записями ретро они не считаются.
RECORDS=""
for f in .harness/retro/*.md; do
    case "$f" in */TEMPLATE.md|*/README.md) continue ;; esac
    [ -f "$f" ] && RECORDS="$RECORDS $f"
done

# Последний коммит, затронувший каталог ретро. Каданс `retro_due.py` считает от
# того же пути, поэтому diff-вход использует тот же baseline. Инцидентная ретро
# датируется задним числом штатно.
LAST=""
[ -n "$RECORDS" ] && LAST=$(git log -1 --format=%h -- .harness/retro)

S_T=$(python3 "$C/retro_due.py" --status)
NOTE=""

if [ -n "$LAST" ]; then
    RANGE="$LAST..HEAD"; SINCE="с последней ретро ($LAST)$NOTE"
elif [ -n "$RECORDS" ]; then
    RANGE=""; SINCE="запись ретро есть, но не закоммичена — период за всю историю$NOTE"
else
    RANGE=""; SINCE="ретро ещё не было — период за всю историю$NOTE"
fi
MEASURE_RANGE=${RANGE:-HEAD}

echo "входы ретро — $SINCE"
echo

# --- 1. копилка наблюдений ---------------------------------------------------
echo "1. КОПИЛКА  .harness/observations.md"
if [ -f .harness/observations.md ]; then
    PAT='^- 2[0-9][0-9][0-9]-'
    CMD="grep -c '$PAT' .harness/observations.md || [ \$? -eq 1 ]"
    N=$(eval "$CMD")
    num "записей: " "$CMD"
    if [ "$N" -gt 0 ]; then
        grep "$PAT" .harness/observations.md | sed 's/^/   /'
        echo "   вопрос: что здесь ломается ПОСТОЯННО, а не что сломалось"
    else
        echo "   пусто — класса на серии не видно, кандидатов ищи в диффах"
    fi
else
    echo "   файла нет — копилка не заведена. Это не «наблюдений ноль»:"
    echo "   вход отсутствует, и первый же класс сегодня будет некуда записать"
fi
echo

# --- 2. диффы за период ------------------------------------------------------
echo "2. ДИФФЫ  ${RANGE:-вся история}"
if [ -n "$RANGE" ]; then
    git log --oneline "$RANGE" | sed 's/^/   /'
    num "объём: " "git diff --shortstat $RANGE | sed 's/^ *//'" "нет изменений"
else
    git log --oneline | sed 's/^/   /'
    INITIAL=$(git rev-list --max-parents=0 HEAD)
    num "объём: " "git diff --shortstat $INITIAL..HEAD | sed 's/^ *//'" "нет изменений"
fi
num "незакоммиченного сейчас (в период НЕ входит): " "git status --porcelain | wc -l | tr -d ' '"
num "коммитов периода: " "git rev-list --count $MEASURE_RANGE"
num "коммитов с R8 catalog: " "git rev-list --count $MEASURE_RANGE -- src/chiplog/inert_shared/r8-implementation-v1.json"
echo

# --- 3. журнал хвостов -------------------------------------------------------
echo "3. ХВОСТЫ  .harness/handoff.md"
if [ -f .harness/handoff.md ]; then
    num "записей: " "python3 $C/handoff_pending.py --status | sed -n 's/^handoff_total=//p'"
    { grep '^- что:' .harness/handoff.md || [ $? -eq 1 ]; } | sed 's/^- что:/   ·/'
    echo "   вопрос: попадала ли сюда одна формулировка дважды"
else
    echo "   файла нет — текущий журнал handoff отсутствует"
fi
echo "   DURABLE DEFECTS  .harness/deferred"
if [ ! -d .harness/deferred ]; then
    echo "   реестр отсутствует"
else
    FOUND=0
    for entry in .harness/deferred/D-*.md; do
        [ -f "$entry" ] || continue
        FOUND=1
        # Inventory only: keep unknown/duplicate statuses visible; admission and
        # transition validation remain owned by deferred.py. Never read body labels.
        STATUS=$(awk '
            NR == 1 { if ($0 != "---") exit; next }
            $0 == "---" { exit }
            /^status:/ { sub(/^status:[[:space:]]*/, ""); print }
        ' "$entry")
        [ -n "$STATUS" ] || STATUS='неизвестен: поле status отсутствует'
        printf '   · %s — status: %s\n' "$entry" "$STATUS"
    done
    [ "$FOUND" -ne 0 ] || echo "   записей нет"
fi
echo

# --- 4. регрессия кейсов -----------------------------------------------------
echo "4. КЕЙСЫ  .harness/reproducers/*/case.md"
CMD="find .harness/reproducers -name case.md | wc -l | tr -d ' '"
CASES=$(eval "$CMD")
num "кейсов: " "$CMD"
if [ "$CASES" -gt 0 ]; then
    find .harness/reproducers -name case.md | while IFS= read -r c; do
        d=${c%/case.md}
        REPRO=$(sed -n 's/^repro: *//p' "$c" | head -1)
        printf '   · %s  repro: %s\n' "${d##*/}" "$REPRO"
    done
    echo "   прогон: ./.harness/scripts/replay.sh <id> --harness <ветка>; доля, а не флаг"
else
    echo "   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»"
fi
echo

# --- 5. инварианты и долги ---------------------------------------------------
echo "5. ИНВАРИАНТЫ И ДОЛГИ"
S_R=$(python3 "$C/rules_have_reproducers.py" --status)
num "правил-файлов: " "python3 $C/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p'"
[ "$(st "$S_R" rules_total)" = 0 ] &&
    echo "     инвариант 1 истинен пусто — «нечего проверять», а не «держится»"

if [ -f AGENTS.md ]; then
    num "разделов AGENTS.md: " "grep -c '^## [0-9]' AGENTS.md || [ \$? -eq 1 ]"
    echo "     это левая половина инварианта 2: у каждого раздела должен быть"
    echo "     гейт или пометка [без гейта] — правую половину читай глазами"
else
    echo "   AGENTS.md отсутствует — инвариант 2 проверять не на чем"
fi

if [ -f .harness/scripts/gate.sh ]; then
    num "строк run в gate.sh: " "grep -c '^run ' .harness/scripts/gate.sh || [ \$? -eq 1 ]"
    num "из них адаптеров: " "grep -c '^run .*/\\(lint\\|test\\)\\.sh' .harness/scripts/gate.sh || [ \$? -eq 1 ]"
else
    echo "   gate.sh отсутствует — инвариант 3 проверять не на чем"
fi
echo "   самотест здесь не запускается; он проверяет harness-пары и ограниченные"
echo "   тесты fast/контрактов адаптеров. Досрочно: sh .harness/scripts/gate.sh --self-test"

echo "   долги:"
if [ ! -f .harness/HARNESS_MODEL.md ]; then
    echo "   · .harness/HARNESS_MODEL.md отсутствует — список долгов читать негде"
else
    DEBTS=$(grep '^| D[0-9]' .harness/HARNESS_MODEL.md || [ $? -eq 1 ])
    if [ -n "$DEBTS" ]; then
        printf '%s\n' "$DEBTS" | while IFS='|' read -r _ id _ hook _; do
            ID=$(echo "$id" | xargs)
            HOOK=$(echo "$hook" | xargs)
            printf '   · %s — крючок: %s\n' "$ID" "$HOOK"
        done
        echo "     (grep '^| D' .harness/HARNESS_MODEL.md)"
        echo "     вопрос: сработал ли крючок. Сработал и не закрыт — исход обязателен"
    else
        echo "   · нет открытых   (grep '^| D' .harness/HARNESS_MODEL.md)"
    fi
fi
echo
echo "Этот снимок автоматически встраивается в запись при запуске через new-retro.sh."
echo 'RETRO_INPUTS_COMPLETE=1'
