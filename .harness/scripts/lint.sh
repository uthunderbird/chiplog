#!/bin/sh
# Разбор исходников. Адаптер под стек проекта.
#
# Проектный адаптер Chiplog: Ruff, проверка форматирования и строгий mypy.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
FAILED=0

# --- shell: синтаксис всех скриптов харнесса и хуков -------------------------
for f in .harness/scripts/*.sh .githooks/*; do
    [ -f "$f" ] || continue
    if ! sh -n "$f" 2>/dev/null; then
        echo "    $f: скрипт не разбирается shell-ом" >&2
        echo "    → команда: sh -n $f" >&2
        FAILED=1
    fi
done

# --- python: разбор всех проверок и вспомогательных модулей ------------------
if ! python3 - <<'PY' 2>/dev/null
import ast, pathlib, sys
bad = []
for p in list(pathlib.Path(".harness/scripts").rglob("*.py")):
    try:
        ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        bad.append(f"{p}:{e.lineno}: {e.msg}")
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
PY
then
    echo "    python-модуль харнесса не разбирается" >&2
    echo "    → команда: python3 -c \"import ast,pathlib;[ast.parse(p.read_text()) for p in pathlib.Path('.harness/scripts').rglob('*.py')]\"" >&2
    FAILED=1
fi

# --- json: настройки агента --------------------------------------------------
if [ -f .claude/settings.json ] && ! python3 -c "import json,sys;json.load(open('.claude/settings.json'))" 2>/dev/null; then
    echo "    .claude/settings.json: не разбирается как JSON" >&2
    echo "    → команда: python3 -m json.tool .claude/settings.json" >&2
    FAILED=1
fi

# --- chiplog: lint, format и типы -------------------------------------------
if ! uv run ruff check --force-exclude .; then
    echo "    проект не прошёл Ruff" >&2
    echo "    → команда: uv run ruff check --force-exclude ." >&2
    FAILED=1
fi
if ! uv run ruff format --check --force-exclude .; then
    echo "    форматирование проекта не совпадает с Ruff" >&2
    echo "    → команда: uv run ruff format ." >&2
    FAILED=1
fi
if ! uv run mypy; then
    echo "    проект не прошёл mypy" >&2
    echo "    → команда: uv run mypy" >&2
    FAILED=1
fi

exit "$FAILED"
