#!/usr/bin/env python3
"""Полировка оставляет леса — коммит их не переживает.

`/polish` ведёт журнал раундов и снимает точку отката перед первой правкой.
И то и другое — строительные леса: они нужны, пока идёт цикл, и превращаются
в мусор, как только он закончился. Забытый `polish-ledger.md` в следующий раз
прочитают как состояние текущего прогона и продолжат несуществующий цикл;
забытый бэкап тихо расходится с оригиналом.

Проверка смотрит на рабочее дерево, а не на индекс: артефакт, лежащий рядом
и не добавленный в коммит, всё равно остаётся в проекте.

Список шаблонов явный. Эвристика «всё, что похоже на резервную копию» ловила бы
чужие файлы, а молчаливое исключение по расширению вернуло бы ровно ту дыру,
которую проверка закрывает.
"""

import argparse
import pathlib
import sys

# Точное имя, а не шаблон: `polish-ledger*.md` ловил бы и файл-документацию
# о журнале. Точку отката здесь не ищем намеренно — `/polish` снимает её копией
# только для целей вне git, а всё в этом репозитории под контролем версий,
# и откат там идёт диффом. Появится копия — появится и строка.
NAMES = ("polish-ledger.md",)

# Каталоги чужого кода: под шаблон могут попасть их файлы, а удалять чужое
# по метке «команда» — худшее, что может сделать проверка.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "vendor", "__pycache__"}


def plural(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} артефакт полировки остался"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} артефакта полировки остались"
    return f"{n} артефактов полировки осталось"


def report(problems):
    for p in problems:
        for line in p.splitlines():
            print(f"    {line}", file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    found = []
    for name in NAMES:
        for path in sorted(root.rglob(name)):
            if SKIP_DIRS & set(path.parts):
                continue
            found.append(path.relative_to(root))

    if not found:
        return 0

    listing = "\n".join(f"    · {f}" for f in found)
    return report([
        f"{plural(len(found))} в рабочем дереве\n"
        f"{listing}\n"
        f"  → команда: rm " + " ".join(str(f) for f in found)
    ])


if __name__ == "__main__":
    sys.exit(main())
