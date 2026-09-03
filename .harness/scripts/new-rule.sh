#!/bin/sh
# Провал агента — адрес дыры в харнессе. Заводим правило и место для плохого входа.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
LESSON=${1:-}

if [ -z "$LESSON" ]; then
    echo "имя правила не задано" >&2
    echo "  → команда: ./.harness/scripts/new-rule.sh \"что именно сломалось\"" >&2
    exit 1
fi

ID=$(printf '%s' "$LESSON" | python3 "$ROOT/.harness/scripts/checks/slugify.py")
[ -n "$ID" ] || ID="rule-$(date +%Y%m%d%H%M%S)"

RULE="$ROOT/.harness/rules/$ID.md"
REPRO="$ROOT/.harness/reproducers/$ID"

[ -e "$RULE" ] && { echo "уже есть: $RULE" >&2; exit 1; }

mkdir -p "$REPRO"
cat > "$RULE" <<EOF
---
id: $ID
error_class: ЗАПОЛНИ — класс ошибок, а не один случай
reproducer: .harness/reproducers/$ID
born: $(date +%Y-%m-%d)
---

## Провал

$LESSON

## Правило

ЗАПОЛНИ — что теперь обязано происходить.

## Где живёт

Корзину выбери по \`.harness/HARNESS_MODEL.md\`, вопрос 2: сначала модальность (обязано ли
блокировать), потом момент. Класс воспроизводится в проекте с другим стеком —
переселяй в общие навыки агента.

## Дешёвый обход

ЗАПОЛНИ — что делало неверное поведение дешевле верного. Канонический перечень —
\`AGENTS.md\` §4. На него же ставится поле \`✗\` в сообщении проверки.

## Как удалить

Удали правило и прогони \`.harness/reproducers/$ID\`.
Гейт всё равно ловит — правило было мёртвым весом.
Ошибка рецидивирует — верни.
EOF

cat > "$REPRO/README.md" <<EOF
# Репродьюсер: $ID

Заведомо плохой вход, на котором провал воспроизводится.
Положи сюда файл, тест или скрипт и подключи его к \`.harness/scripts/test.sh\`.

Пустой каталог гейт не пропустит — и это правильно.
EOF

echo "создано:"
echo "  $RULE"
echo "  $REPRO/"
echo
echo "Дальше:"
echo "  1. заполни error_class и текст правила"
echo "  2. положи плохой вход в $REPRO/"
echo "  3. убедись, что он падает без правила и проходит с ним"
echo "  4. git add + git commit -m 'rule($ID): <урок>'"
