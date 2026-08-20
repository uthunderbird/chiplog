#!/bin/sh
# Диспетчер гейта. Закрыт по умолчанию: любая упавшая проверка останавливает работу.
set -u

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT" || exit 1

FAILED=0
SELFTEST=0
[ "${1:-}" = "--self-test" ] && SELFTEST=1

# Единственное назначение — verify-integration.sh. Проверить, что проверки проекта
# живы, иначе нечем: гейт зовёт их сам через lint.sh, поэтому при работающем гейте
# отказ по битому коду не отличить от «сосед мёртв, а поймал его харнесс».
#
# Новой дыры это не открывает: `git commit --no-verify` существует и следов
# оставляет столько же. Поэтому пропуск громкий — молчаливый был бы дырой.
if [ "${HARNESS_GATE_SKIP:-}" = "1" ]; then
    echo "гейт пропущен: HARNESS_GATE_SKIP=1" >&2
    echo "  → сделай: если это не прогон verify-integration.sh, выясни, кто выставил" >&2
    echo "    переменную. Вне проверки интеграции у неё нет законного применения" >&2
    echo "  ✓ обычный коммит проходит через все строки run в gate.sh" >&2
    exit 0
fi

run() {
    name=$1
    shift
    if "$@"; then
        printf '  ok    %s\n' "$name"
    else
        printf '  FAIL  %s\n' "$name"
        if [ "${HARNESS_VERIFY_RECEIPT:-}" = 1 ] && [ "$name" = "правила и репродьюсеры" ]; then
            : "${HARNESS_VERIFY_NONCE:?verifier nonce is required}"
            : "${HARNESS_VERIFY_BASELINE:?verifier baseline is required}"
            : "${HARNESS_VERIFY_PROBE:?verifier probe path is required}"
            : "${HARNESS_VERIFY_PROBE_OID:?verifier probe oid is required}"
            receipt=$(git rev-parse --git-path \
                "harness-gate-receipt-$HARNESS_VERIFY_NONCE") || exit 1
            observed_oid=$(git rev-parse --verify ":$HARNESS_VERIFY_PROBE" 2>/dev/null || true)
            [ -n "$observed_oid" ] || exit 1
            [ "$observed_oid" = "$HARNESS_VERIFY_PROBE_OID" ] || exit 1
            printf 'checker=%s\nnonce=%s\nbaseline=%s\nprobe=%s\noid=%s\n' \
                '.harness/scripts/checks/rules_have_reproducers.py' \
                "$HARNESS_VERIFY_NONCE" "$HARNESS_VERIFY_BASELINE" \
                "$HARNESS_VERIFY_PROBE" "$observed_oid" > "$receipt" || exit 1
        fi
        FAILED=1
    fi
}

if [ "$SELFTEST" -eq 1 ]; then
    # Самотест парный: заведомо плохой вход обязан уронить проверку, заведомо
    # хороший — пройти. Только падение недостаточно: проверка, возвращающая
    # единицу всегда, удовлетворила бы требование, ничего не проверив.
    echo "self-test: парные входы"

    # Самотест создаёт временные репозитории. Под git-хуком в окружении уже стоят
    # GIT_INDEX_FILE, GIT_DIR и GIT_WORK_TREE — и тогда `git add` внутри фикстуры
    # пишет не в неё, а в индекс ХОЗЯИНА. Наблюдалось на живом проекте: индекс
    # схлопывался с 376 записей до двух, ровно до размера фикстуры, и коммит
    # унёс бы с собой удаление всего остального.
    unset GIT_INDEX_FILE GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY \
          GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_PREFIX 2>/dev/null || true

    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    SELF_FAILED=0
    C=.harness/scripts/checks

    pair() { # pair <имя> <чек> <функция-хорошего-входа>; плохой уже разложен
        _name=$1; _check=$2; _good=$3
        if python3 "$C/$_check" --root "$TMP" >/dev/null 2>&1; then
            echo "  FAIL  $_name: проверка не упала на заведомо плохом входе"
            echo "    → чинить нечем: проверка ничего не проверяет. Чини $C/$_check,"
            echo "      а не вход"
            echo "    ✓ прогон на плохом входе возвращает ненулевой код"
            SELF_FAILED=1; return
        fi
        $_good
        if python3 "$C/$_check" --root "$TMP" >/dev/null 2>&1; then
            echo "  ok    $_name"
        else
            echo "  FAIL  $_name: проверка упала на заведомо хорошем входе"
            echo "    → чинить нечем: проверка ловит лишнее. Чини $C/$_check"
            echo "    ✓ прогон на хорошем входе возвращает ноль"
            SELF_FAILED=1
        fi
    }

    reset() { rm -rf "$TMP"; mkdir -p "$TMP/.harness/rules" "$TMP/.harness/reproducers" \
        "$TMP/.harness/retro" "$TMP/.harness/scripts/checks"; }

    # --- правила и репродьюсеры ---
    reset
    printf -- '---\nid: bad\n---\nПравило без класса и без входа.\n' > "$TMP/.harness/rules/bad.md"
    good_rules() {
        printf -- '---\nid: bad\nerror_class: класс ошибок\n---\nПравило.\n' > "$TMP/.harness/rules/bad.md"
        mkdir -p "$TMP/.harness/reproducers/bad"; echo x > "$TMP/.harness/reproducers/bad/in.txt"; }
    pair "правила и репродьюсеры" rules_have_reproducers.py good_rules

    # --- формат доли в кейсе: своя пара, иначе ветка не покрыта ---
    reset
    mkdir -p "$TMP/.harness/reproducers/k"
    printf -- '---\nid: k\nerror_class: класс\nkind: agent\n---\n' > "$TMP/.harness/rules/k.md"
    printf -- '---\nid: k\ncommit: abc\nrepro: да\n---\nтекст\n' > "$TMP/.harness/reproducers/k/case.md"
    good_repro() {
        printf -- '---\nid: k\ncommit: abc\nrepro: 3/3\n---\nтекст\n' > "$TMP/.harness/reproducers/k/case.md"; }
    pair "формат доли в кейсе" rules_have_reproducers.py good_repro

    # --- каданс ретро ---
    reset
    printf -- '---\ndate: 2026-01-01\noutcome: неясно\n---\n' > "$TMP/.harness/retro/2026-01-01.md"
    good_retro() {
        printf -- '---\ndate: 2026-01-01\noutcome: skip\nreason: нечего\n---\n' > "$TMP/.harness/retro/2026-01-01.md"; }
    pair "каданс ретро" retro_due.py good_retro

    # --- след правил в истории ---
    reset
    ( cd "$TMP" && git init -q && git config user.email t@t && git config user.name t )
    printf -- '---\nid: bad\nerror_class: x\n---\n' > "$TMP/.harness/rules/bad.md"
    ( cd "$TMP" && git add -A >/dev/null 2>&1 )
    good_commit() {
        mkdir -p "$TMP/.harness/reproducers/bad"; echo x > "$TMP/.harness/reproducers/bad/in.txt"
        ( cd "$TMP" && git add -A >/dev/null 2>&1 ); }
    pair "след правил в истории" commit_trail.py good_commit

    # --- императив в провалах ---
    reset
    printf 'problems = []\nproblems.append("файл: беда, а что делать не сказано")\n' \
        > "$TMP/.harness/scripts/checks/bad_check.py"
    good_msg() {
        printf 'problems = []\nproblems.append("файл: беда\\n  → сделай: почини\\n  ✓ починено")\n' \
            > "$TMP/.harness/scripts/checks/bad_check.py"; }
    pair "императив в провалах" messages_are_actionable.py good_msg

    # --- проверки подключены ---
    reset
    cp .harness/scripts/gate.sh "$TMP/.harness/scripts/gate.sh"
    cp .harness/scripts/checks/*.py "$TMP/.harness/scripts/checks/"
    printf 'import sys\nsys.exit(0)\n' > "$TMP/.harness/scripts/checks/zabytyy.py"
    good_wired() { rm -f "$TMP/.harness/scripts/checks/zabytyy.py"; }
    pair "проверки подключены" checks_are_wired.py good_wired

    # --- леса полировки ---
    reset
    printf 'ledger\n' > "$TMP/polish-ledger.md"
    good_polish() { rm -f "$TMP/polish-ledger.md"; }
    pair "леса полировки" polish_artifacts.py good_polish

    # --- хвосты сессии ---
    reset
    printf -- '## 1 · хвост · сейчас · carried\n- что: обещал закрыть в этом коммите\n' \
        > "$TMP/.harness/handoff.md"
    good_handoff() { rm -f "$TMP/.harness/handoff.md"; }
    pair "хвосты сессии" handoff_pending.py good_handoff

    if [ "$SELF_FAILED" -ne 0 ]; then exit 1; fi
    echo "самотест пройден: 8 пар входов на семи проверках"
    echo "adapters lint.sh/test.sh под инвариант 3 не подпадают: пока стек не вписан,"
    echo "хорошего входа у них не существует."
    exit 0
fi

echo "gate:"
run "правила и репродьюсеры" python3 .harness/scripts/checks/rules_have_reproducers.py
run "каданс ретро"           python3 .harness/scripts/checks/retro_due.py
run "след правил в истории"  python3 .harness/scripts/checks/commit_trail.py
run "императив в провалах"   python3 .harness/scripts/checks/messages_are_actionable.py
run "проверки подключены"    python3 .harness/scripts/checks/checks_are_wired.py
run "леса полировки"         python3 .harness/scripts/checks/polish_artifacts.py
run "хвосты сессии"          python3 .harness/scripts/checks/handoff_pending.py
run "разбор исходников"      sh .harness/scripts/lint.sh
run "тесты и эвалы"          sh .harness/scripts/test.sh

if [ "$FAILED" -ne 0 ]; then
    echo
    echo "гейт закрыт. Работа дальше не едет."
    echo "  → сделай: закрой провалы выше по их же императивам, по одному."
    echo "Каждый провал выше называет следующее действие. Метка говорит, кто решает:"
    echo "  команда / сделай — агент;  стоп / чинить нечем — человек."
    echo "  ✗ не обходи через git commit --no-verify: обход виден в истории по"
    echo "    отсутствию правки рядом с правилом."
    echo "  ✓ ./.harness/scripts/gate.sh возвращает ноль."
    echo "Если проверка неверна — чини проверку, а не обходи её."
    exit 1
fi

echo "гейт открыт."
