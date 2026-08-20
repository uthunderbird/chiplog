#!/bin/sh
# Захват кейса — провала агента, который надо будет воспроизвести.
#
# Входом для провала агента служит не файл, а тройка: что просили, в каком
# состоянии был мир, при каком харнессе. Скрипт берёт вторую и третью части
# механически; первую заполняет человек или агент — руками, дословно.
#
# Захват стоит одну команду. Дороже — не будет применяться, и правила
# о поведении агента останутся недоказуемыми.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"

. "$ROOT/.harness/scripts/thresholds.sh"
MAX_FILES=$HC_CASE_MAX_FILES
MAX_BYTES=$HC_CASE_MAX_BYTES
FORCE=0

ID=""
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -*) echo "неизвестный флаг: $arg" >&2; exit 2 ;;
        *) ID=$arg ;;
    esac
done

if [ -z "$ID" ]; then
    echo "id кейса не задан" >&2
    echo "  → команда: ./.harness/scripts/new-case.sh <id>, где id — имя файла правила" >&2
    echo "    в .harness/rules/<id>.md. Патч больше порога берётся флагом --force" >&2
    exit 2
fi

DIR="$ROOT/.harness/reproducers/$ID"
REL=".harness/reproducers/$ID"

# Неотслеживаемое снимается ДО того, как скрипт создаст свои файлы: иначе
# каталог кейса, сам ещё неотслеживаемый, попадёт в собственный слепок.
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | grep -v "^$REL/" || true)

mkdir -p "$DIR"

SHA=$(git rev-parse HEAD 2>/dev/null || echo "НЕТ-КОММИТОВ")

# --- состояние мира: отслеживаемое и индекс одним патчем ---------------------
git diff HEAD > "$DIR/world.patch" 2>/dev/null || : > "$DIR/world.patch"
N_FILES=$(git diff HEAD --name-only 2>/dev/null | wc -l | tr -d ' ')
N_BYTES=$(wc -c < "$DIR/world.patch" | tr -d ' ')

if [ "$N_FILES" -eq 0 ]; then
    rm -f "$DIR/world.patch"
fi

if [ "$FORCE" -eq 0 ] && { [ "$N_FILES" -gt "$MAX_FILES" ] || [ "$N_BYTES" -gt "$MAX_BYTES" ]; }; then
    rm -f "$DIR/world.patch"
    echo "патч слишком велик: $N_FILES файлов, $N_BYTES байт (порог $MAX_FILES / $MAX_BYTES)" >&2
    echo "  → сделай: ужми состояние мира до того, на чём провал ещё живёт, и повтори." >&2
    echo "    Кейс на сорок файлов не учит ничему: непонятно, что из этого причина." >&2
    echo "    Ужать нельзя — повтори с --force и напиши в case.md, почему." >&2
    echo "  ✓ патч читается глазами, и по нему видно, из-за чего провалился агент" >&2
    exit 1
fi

# --- неотслеживаемое: только копированием, git его не берёт нигде ------------
N_UNTRACKED=0
if [ -n "$UNTRACKED" ]; then
    printf '%s\n' "$UNTRACKED" | while IFS= read -r f; do
        [ -f "$f" ] || continue
        mkdir -p "$DIR/untracked/$(dirname "$f")"
        cp "$f" "$DIR/untracked/$f"
    done
    N_UNTRACKED=$(printf '%s\n' "$UNTRACKED" | wc -l | tr -d ' ')
fi

# --- скелет кейса -----------------------------------------------------------
if [ ! -f "$DIR/case.md" ]; then
    cat > "$DIR/case.md" <<EOF
---
id: $ID
commit: $SHA
model: ЗАПОЛНИ
captured: $(date +%Y-%m-%d)
repro: ЗАПОЛНИ
---

## Вход — реплики человека дословно

<!-- Все реплики человека за сессию, как набраны, без правки и без дописывания
     смысла. Это протокол, а не реконструкция. Агентскую сторону не пишем:
     на прогоне она порождается заново, в этом смысл. -->

1. > ЗАПОЛНИ

## Предикат отказа

<!-- По какому наблюдаемому признаку видно, что это тот самый провал,
     а не какой-то другой. Без предиката прогон превращается в чтение
     выхлопа и убеждение себя. -->

ЗАПОЛНИ

## Диагноз — НЕ подавать на прогон

<!-- Как агент это прочитал и где молчал харнесс. Скормишь обратно —
     воспроизведёшь правильное поведение и запишешь ложную победу. -->

ЗАПОЛНИ

## Что человек считал очевидным

<!-- То, что не пришло в голову сказать. Обычно здесь и лежит дыра. -->

ЗАПОЛНИ
EOF
fi

echo "кейс заведён: .harness/reproducers/$ID/"
echo "  состояние мира: $SHA"
[ -f "$DIR/world.patch" ] && echo "  world.patch:    $N_FILES файлов, $N_BYTES байт"
[ "$N_UNTRACKED" -gt 0 ] && echo "  untracked/:     $N_UNTRACKED файлов"
echo
echo "Дальше:"
echo "  1. заполни case.md — реплики дословно, предикат отказа, диагноз"
echo "  2. проверь кейс: ./.harness/scripts/replay.sh $ID"
echo "     провал обязан повториться. Не повторился — это не кейс, а наблюдение,"
echo "     его место в .harness/observations.md"
echo "  3. после правила: ./.harness/scripts/replay.sh $ID --harness <ветка>"
echo "     провал обязан исчезнуть. Записывай долю, а не флаг: 3/3 → 0/3"
