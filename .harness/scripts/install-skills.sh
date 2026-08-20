#!/bin/sh
# Локальные проектные скиллы. Растут вместе с проектом.
# Базовый набор — swarm-skill: холодный ред-тим, итеративная критика, полировка.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEST="$ROOT/.claude/skills"
REPO="${SWARM_SKILL_REPO:-https://github.com/uthunderbird/swarm-skill}"
REF="${SWARM_SKILL_REF:-main}"

mkdir -p "$DEST"

# Клон живёт во временном каталоге и удаляется после копирования,
# чтобы в .claude/skills/ не оставалось служебных папок.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

echo "ставлю swarm-skill из ${REPO}"
git clone --depth 1 --branch "$REF" "$REPO" "$TMP/swarm-skill"

# Старый кэш из предыдущих версий скрипта.
rm -rf "$DEST/.swarm-skill"

for skill in "$TMP/swarm-skill"/*/; do
    name=$(basename "$skill")
    [ -f "$skill/SKILL.md" ] || continue
    rm -rf "${DEST:?}/$name"
    cp -R "$skill" "$DEST/$name"
done

# Проектные скиллы, живущие в самом репозитории. Кладутся после клона, потому что
# .claude/skills/ под gitignore целиком: иначе они не поехали бы вместе с проектом.
for skill in "$ROOT/.harness/skills"/*/; do
    [ -f "$skill/SKILL.md" ] || continue
    name=$(basename "$skill")
    rm -rf "${DEST:?}/$name"
    cp -R "$skill" "$DEST/$name"
done

echo "скиллы на месте:"
ls -1 "$DEST" | grep -v '^\.'
echo
echo "Холодный контур из AGENTS.md §6 — это swarm-red-team, запущенный без родительского контекста."
