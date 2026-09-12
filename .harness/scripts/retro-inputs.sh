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

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
C=.harness/scripts/checks

# Значение и команда, которой оно получено. Команда одна: исполняемая и печатная.
# Пустой вывод не оставляем пустым местом: пробел на позиции числа читается как
# число, которого никто не заметил.
num() {
    _v=$(eval "$2" 2>/dev/null || echo '? команда упала')
    [ -z "$_v" ] && _v='? команда ничего не вернула'
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

# Коммит, которым ретро ЗАВЕДЕНА (--diff-filter=A), а не последний тронувший файл:
# опечатка, поправленная в старой записи, иначе сдвинет период вперёд и покажет
# «нет изменений» там, где работа была. И один git log на весь список, а не цикл:
# цикл выбирал бы алфавитно последнее имя, а инцидентная ретро датируется задним
# числом штатно.
LAST=""
# shellcheck disable=SC2086
[ -n "$RECORDS" ] && LAST=$(git log -1 --diff-filter=A --format=%h -- $RECORDS 2>/dev/null || true)

S_T=$(python3 "$C/retro_due.py" --status 2>/dev/null || echo BROKEN)
if [ "$S_T" = BROKEN ]; then
    NOTE="  (retro_due.py не отвечает — период определён по файлам, сверь сам)"
else
    NOTE=""
fi

if [ -n "$LAST" ]; then
    RANGE="$LAST..HEAD"; SINCE="с последней ретро ($LAST)$NOTE"
elif [ -n "$RECORDS" ]; then
    RANGE=""; SINCE="запись ретро есть, но не закоммичена — период за всю историю$NOTE"
else
    RANGE=""; SINCE="ретро ещё не было — период за всю историю$NOTE"
fi

echo "входы ретро — $SINCE"
echo

# --- 1. копилка наблюдений ---------------------------------------------------
echo "1. КОПИЛКА  .harness/observations.md"
if [ -f .harness/observations.md ]; then
    PAT='^- 2[0-9][0-9][0-9]-'
    CMD="grep -c '$PAT' .harness/observations.md"
    N=$(eval "$CMD" 2>/dev/null || echo 0)
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
    git log --oneline "$RANGE" 2>/dev/null | sed 's/^/   /' || true
    num "объём: " "git diff --shortstat $RANGE | sed 's/^ *//' | grep . || echo 'нет изменений'"
else
    git log --oneline 2>/dev/null | sed 's/^/   /' || true
    num "объём: " "git diff --shortstat \$(git rev-list --max-parents=0 HEAD)..HEAD | sed 's/^ *//' | grep . || echo 'нет изменений'"
fi
num "незакоммиченного сейчас (в период НЕ входит): " "git status --porcelain | wc -l | tr -d ' '"
echo

# --- 3. журнал хвостов -------------------------------------------------------
echo "3. ХВОСТЫ  .harness/handoff.md"
if [ -f .harness/handoff.md ]; then
    num "записей: " "python3 $C/handoff_pending.py --status | sed -n 's/^handoff_total=//p'"
    grep '^- что:' .harness/handoff.md 2>/dev/null | sed 's/^- что:/   ·/' || true
    echo "   вопрос: попадала ли сюда одна формулировка дважды"
else
    echo "   файла нет — за человека ничего не решено и ничего не отложено"
fi
echo

# --- 4. регрессия кейсов -----------------------------------------------------
echo "4. КЕЙСЫ  .harness/reproducers/*/case.md"
CMD="find .harness/reproducers -name case.md 2>/dev/null | wc -l | tr -d ' '"
CASES=$(eval "$CMD" || echo 0)
num "кейсов: " "$CMD"
if [ "$CASES" -gt 0 ]; then
    find .harness/reproducers -name case.md 2>/dev/null | while IFS= read -r c; do
        d=${c%/case.md}
        printf '   · %s  repro: %s\n' "${d##*/}" "$(sed -n 's/^repro: *//p' "$c" | head -1)"
    done
    echo "   прогон: ./.harness/scripts/replay.sh <id> --harness <ветка>; доля, а не флаг"
else
    echo "   регрессия вакуумна — прогонять нечего, отметь это, а не «держит»"
fi
echo

# --- 5. инварианты и долги ---------------------------------------------------
echo "5. ИНВАРИАНТЫ И ДОЛГИ"
S_R=$(python3 "$C/rules_have_reproducers.py" --status 2>/dev/null || true)
num "правил-файлов: " "python3 $C/rules_have_reproducers.py --status | sed -n 's/^rules_total=//p'"
[ "$(st "$S_R" rules_total)" = 0 ] &&
    echo "     инвариант 1 истинен пусто — «нечего проверять», а не «держится»"

if [ -f AGENTS.md ]; then
    num "разделов AGENTS.md: " "grep -c '^## [0-9]' AGENTS.md"
    echo "     это левая половина инварианта 2: у каждого раздела должен быть"
    echo "     гейт или пометка [без гейта] — правую половину читай глазами"
else
    echo "   AGENTS.md отсутствует — инвариант 2 проверять не на чем"
fi

if [ -f .harness/scripts/gate.sh ]; then
    num "строк run в gate.sh: " "grep -c '^run ' .harness/scripts/gate.sh"
    num "из них адаптеров: " "grep -c '^run .*/\\(lint\\|test\\)\\.sh' .harness/scripts/gate.sh"
else
    echo "   gate.sh отсутствует — инвариант 3 проверять не на чем"
fi
echo "   самотест здесь не запускается; он проверяет harness-пары и ограниченные"
echo "   тесты fast/контрактов адаптеров. Досрочно: sh .harness/scripts/gate.sh --self-test"

echo "   долги:"
if [ ! -f .harness/HARNESS_MODEL.md ]; then
    echo "   · .harness/HARNESS_MODEL.md отсутствует — список долгов читать негде"
elif grep -q '^| D[0-9]' .harness/HARNESS_MODEL.md; then
    grep '^| D[0-9]' .harness/HARNESS_MODEL.md | while IFS='|' read -r _ id _ hook _; do
        printf '   · %s — крючок: %s\n' "$(echo "$id" | xargs)" "$(echo "$hook" | xargs)"
    done
    echo "     (grep '^| D' .harness/HARNESS_MODEL.md)"
    echo "     вопрос: сработал ли крючок. Сработал и не закрыт — исход обязателен"
else
    echo "   · нет открытых   (grep '^| D' .harness/HARNESS_MODEL.md)"
fi
echo
echo "Вставь этот вывод в раздел «Что перечитано» записи ретро."
