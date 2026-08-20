#!/bin/sh
# Дашборд харнесса — упреждающая сводка на входе в сессию.
#
# Гейт говорит «нельзя» на pre-commit, то есть в конце работы. Дашборд говорит
# «вот где ты» в начале. Это разные роли, и дашборд не претендует на роль гейта.
#
# Два инварианта, без которых он начнёт врать:
#   1. Здесь нет ни одного порога. Все пороги живут в .harness/scripts/checks/*.py
#      и приходят сюда через --status. Копия порога разъедется с оригиналом.
#   2. Дашборд не печатает вердикт о том, чего не проверял. lint.sh и test.sh он
#      не гоняет (чужой стек, секунды-минуты) — и потому про них говорит только
#      то, что видно статически: подключены они или нет.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT" || exit 1
CHECKS="$ROOT/.harness/scripts/checks"
LOG_N=${HARNESS_DASHBOARD_COMMITS:-5}
STATUS_N=${HARNESS_DASHBOARD_FILES:-10}

val() {
    printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1
}

# --- состояние дерева -------------------------------------------------------
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "харнесс: не git-репозиторий — дашборду не на чём стоять"
    exit 0
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')
DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
STAGED=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')

STATE="ветка $BRANCH"
if [ "$DIRTY" -gt 0 ]; then
    STATE="$STATE · изменений $DIRTY (в индексе $STAGED)"
else
    STATE="$STATE · дерево чистое"
fi
UPSTREAM=$(git rev-parse --abbrev-ref '@{upstream}' 2>/dev/null || true)
if [ -n "$UPSTREAM" ]; then
    AHEAD=$(git rev-list --count "$UPSTREAM..HEAD" 2>/dev/null || echo 0)
    [ "$AHEAD" -gt 0 ] && STATE="$STATE · не запушено $AHEAD"
fi

printf '%s\n' "$STATE"

# --- незакоммиченные файлы --------------------------------------------------
# Имена, а не диффы: нужно узнать «что у меня на руках», а не «что именно менялось».
# Список режется порогом — дашборд обязан влезать в один экран, а грязное дерево
# на сотню файлов это само по себе один сигнал, а не сто.
if [ "$DIRTY" -gt 0 ]; then
    echo
    git status --porcelain 2>/dev/null | head -"$STATUS_N" | while read -r line; do
        printf '  %s\n' "$line"
    done
    [ "$DIRTY" -gt "$STATUS_N" ] && printf '  … и ещё %s\n' "$((DIRTY - STATUS_N))"
fi
echo

# --- лента коммитов ---------------------------------------------------------
# rule( помечается: статья №2 — история обязана читаться как список пойманных ошибок.
git log -"$LOG_N" --format='%h|%ad|%s' --date=short 2>/dev/null | while IFS='|' read -r h d s; do
    case "$s" in
        rule\(*) mark=' ⟵ правило' ;;
        *)       mark='' ;;
    esac
    printf '  %s  %s  %s%s\n' "$h" "$d" "$s" "$mark"
done
echo

# --- счётчики ---------------------------------------------------------------
S_RETRO=$(python3 "$CHECKS/retro_due.py" --status 2>/dev/null || true)
S_RULES=$(python3 "$CHECKS/rules_have_reproducers.py" --status 2>/dev/null || true)

R_SINCE=$(val "$S_RETRO" retro_since)
R_EVERY=$(val "$S_RETRO" retro_every)
R_ENTRIES=$(val "$S_RETRO" retro_entries)
R_SKIPS=$(val "$S_RETRO" retro_skips)
R_MAXSKIPS=$(val "$S_RETRO" retro_max_skips)

if [ "${R_ENTRIES:-0}" = "0" ]; then
    printf '  каданс ретро   ретро ещё не было · каданс %s коммитов\n' "${R_EVERY:-?}"
elif [ -n "$R_SINCE" ]; then
    printf '  каданс ретро   %s/%s коммитов с последней\n' "$R_SINCE" "$R_EVERY"
fi
[ "${R_SKIPS:-0}" -gt 0 ] 2>/dev/null && \
    printf '  отказы подряд  %s/%s\n' "$R_SKIPS" "$R_MAXSKIPS"

RULES=$(val "$S_RULES" rules_total)
NORMS=$(val "$S_RULES" rules_norms)
REVIEW=$(val "$S_RULES" norm_review_in)
LINE="  правила        ${RULES:-0}"
[ "${NORMS:-0}" != "0" ] && LINE="$LINE · норм ${NORMS}"
[ -n "$REVIEW" ] && LINE="$LINE · ближайшая ревизия через ${REVIEW} дн."
printf '%s\n' "$LINE"

S_HAND=$(python3 "$CHECKS/handoff_pending.py" --status 2>/dev/null || true)
H_TOTAL=$(val "$S_HAND" handoff_total)
if [ "${H_TOTAL:-0}" -gt 0 ] 2>/dev/null; then
    H_DEC=$(val "$S_HAND" handoff_decisions)
    H_NOW=$(val "$S_HAND" handoff_now)
    H_NEXT=$(val "$S_HAND" handoff_next)
    H_NEW=$(val "$S_HAND" handoff_new)
    LINE="  хвосты сессии "
    [ "${H_DEC:-0}" -gt 0 ] && LINE="$LINE решений за человека $H_DEC ·"
    [ "${H_NOW:-0}" -gt 0 ] && LINE="$LINE в этот коммит $H_NOW ·"
    [ "${H_NEXT:-0}" -gt 0 ] && LINE="$LINE следующим $H_NEXT ·"
    # Держит коммит не всякая запись: пересказанный хвост «следующим» не держит.
    if [ "${H_NOW:-0}" -gt 0 ] || [ "${H_NEW:-0}" -gt 0 ]; then
        printf '%s коммит закрыт\n' "${LINE% ·}"
    else
        printf '%s\n' "${LINE% ·}"
    fi
fi

OBS="$ROOT/.harness/observations.md"
if [ -f "$OBS" ]; then
    N_OBS=$(grep -c '^- 2[0-9][0-9][0-9]-' "$OBS" 2>/dev/null || true)
    N_OBS=${N_OBS:-0}
    if [ "$N_OBS" -gt 0 ]; then
        OLDEST=$(grep -o '^- 2[0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' "$OBS" | sort | head -1 | cut -c3-)
        DAYS=$(python3 -c "import datetime,sys;print((datetime.date.today()-datetime.date.fromisoformat(sys.argv[1])).days)" "$OLDEST" 2>/dev/null || echo '?')
        printf '  копилка        %s наблюдений · старшему %s дн.\n' "$N_OBS" "$DAYS"
    else
        printf '  копилка        пусто\n'
    fi
fi

# Не прогоняли — значит и «ок» не пишем. Говорим ровно то, что видно статически.
STUBS=''
grep -q '^HARNESS_UNCONFIGURED=1' "$ROOT/.harness/scripts/lint.sh" 2>/dev/null && STUBS="линтеры"
grep -q '^HARNESS_UNCONFIGURED=1' "$ROOT/.harness/scripts/test.sh" 2>/dev/null && \
    STUBS="${STUBS:+$STUBS, }тесты"
[ -n "$STUBS" ] && printf '  дыры           не подключены: %s (гейт закрыт)\n' "$STUBS"

# --- внимание ---------------------------------------------------------------
# Дешёвая половина гейта: три файловых чека. Смысл — узнать о падении сейчас,
# а не на pre-commit. lint/test сюда не входят намеренно.
ATTN=$( { python3 "$CHECKS/rules_have_reproducers.py" || true
          python3 "$CHECKS/retro_due.py"              || true
          python3 "$CHECKS/commit_trail.py"           || true
        } 2>&1 >/dev/null )

if [ -n "$ATTN" ]; then
    printf '\nвнимание\n%s\n' "$ATTN"
fi
