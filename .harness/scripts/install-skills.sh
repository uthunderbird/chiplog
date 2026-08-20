#!/bin/sh
# Локальные проектные скиллы. Растут вместе с проектом.
# Репозиторные скиллы отслеживаются в .agents/skills — это единственный runtime-
# канон Codex. Скрипт проверяет checkout, но не переписывает tracked-файлы.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEST="$ROOT/.agents/skills"

mkdir -p "$DEST"

TRACKED=$(git -C "$ROOT" ls-files --stage -- .agents/skills)
[ -n "$TRACKED" ] || {
    echo "СТОП: в Git нет отслеживаемых project skills" >&2
    echo "→ сделай: восстанови .agents/skills из Git" >&2
    echo "✓ git ls-files --stage -- .agents/skills печатает manifest" >&2
    exit 1
}

printf '%s\n' "$TRACKED" | while IFS="$(printf '\t')" read -r metadata path; do
    set -- $metadata
    mode=$1
    oid=$2
    stage=$3
    [ "$stage" = 0 ] || {
        echo "СТОП: unmerged skill entry: $path" >&2
        echo "→ сделай: разреши конфликт в index" >&2
        echo "✓ git ls-files --stage -- .agents/skills содержит только stage 0" >&2
        exit 1
    }
    case "$mode" in
        100644|100755) ;;
        *)
            echo "СТОП: недопустимый tracked mode у $path: $mode" >&2
            echo "→ сделай: замени symlink/необычный объект обычным tracked-файлом" >&2
            echo "✓ git ls-files --stage -- .agents/skills содержит только 100644/100755" >&2
            exit 1
            ;;
    esac
    [ -f "$ROOT/$path" ] && [ ! -L "$ROOT/$path" ] || {
        echo "СТОП: отсутствует или подменён tracked skill file: $path" >&2
        echo "→ сделай: восстанови файл из Git" >&2
        echo "✓ test -f $path && test ! -L $path" >&2
        exit 1
    }
    actual=$(git -C "$ROOT" hash-object -- "$path")
    [ "$actual" = "$oid" ] || {
        echo "СТОП: skill bytes отличаются от проверенного index: $path" >&2
        echo "→ сделай: проверь изменение и добавь его в index либо восстанови файл" >&2
        echo "✓ git hash-object $path совпадает с его stage-0 OID" >&2
        exit 1
    }
done

UNTRACKED=$(git -C "$ROOT" ls-files --others --exclude-standard -- .agents/skills)
[ -z "$UNTRACKED" ] || {
    echo "СТОП: в .agents/skills есть untracked instruction state" >&2
    echo "→ реши: удали его либо явно добавь в Git после ревью" >&2
    echo "  решение запиши: ./.harness/scripts/decide.sh \"принял untracked project skill\" \"провёл ревью instruction state\" \"удалить как посторонний\"" >&2
    echo "✓ git ls-files --others --exclude-standard -- .agents/skills ничего не печатает" >&2
    exit 1
}

for skill in "$DEST"/*/; do
    [ -e "$skill" ] || continue
    [ -f "$skill/SKILL.md" ] && [ ! -L "$skill/SKILL.md" ] || {
        echo "СТОП: у project skill нет обычного SKILL.md: $skill" >&2
        echo "→ сделай: восстанови entrypoint из Git" >&2
        echo "✓ каждый каталог .agents/skills/* содержит обычный SKILL.md" >&2
        exit 1
    }
done

echo "скиллы на месте:"
ls -1 "$DEST" | grep -v '^\.'
echo
echo "Холодный контур из AGENTS.md §6 — это swarm-red-team, запущенный без родительского контекста."
