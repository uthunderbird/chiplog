#!/bin/sh
# Журнал хвостов сессии: показать, закрыть запись, подтвердить пересказ.
#
#   handoff.sh              показать журнал
#   handoff.sh --close N    снять запись N — работа сделана или хвост сознательно снят
#   handoff.sh --ack        пересказано человеку и разрешено: решения закрываются,
#                           хвосты «следующим» переезжают в следующую сессию
#
# --ack не закрывает хвосты со сроком «сейчас»: их закрывает работа, а не разговор.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
LOG="$ROOT/.harness/handoff.md"
PY="$ROOT/.harness/scripts/handoff.py"

case "${1:-}" in
    "")
        if [ -f "$LOG" ]; then
            cat "$LOG"
        else
            echo "журнал пуст — за человека ничего не решено и ничего не отложено"
        fi
        ;;
    --close)
        N=${2:-}
        if [ -z "$N" ]; then
            echo "номер записи не задан" >&2
            echo "  → команда: ./.harness/scripts/handoff.sh --close <N>, где N — номер" >&2
            echo "    из шапки записи в .harness/handoff.md" >&2
            exit 2
        fi
        if python3 "$PY" "$ROOT" close "$N"; then
            echo "запись [$N] снята"
        else
            echo "записи [$N] в журнале нет" >&2
            echo "  → команда: ./.harness/scripts/handoff.sh — посмотреть номера" >&2
            exit 1
        fi
        ;;
    --ack)
        OUT=$(python3 "$PY" "$ROOT" ack)
        DROPPED=${OUT%%|*}
        KEPT=${OUT##*|}
        echo "пересказ подтверждён: решений закрыто $DROPPED, хвостов перенесено $KEPT"
        if [ "$KEPT" -gt 0 ]; then
            echo "перенесённые всплывут снова — журнал остался"
        fi
        ;;
    *)
        echo "неизвестный аргумент" >&2
        echo "  → команда: ./.harness/scripts/handoff.sh [--close <N> | --ack]" >&2
        exit 2
        ;;
esac
