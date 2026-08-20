#!/usr/bin/env python3
"""Проверка, забытая в диспетчере, не выполняется никогда — и молчит об этом.

Правило выглядит доросшим до скрипта: файл написан, лежит в checks/. Но пока
на него нет строки `run` в gate.sh, он не исполняется ни разу, и отличить его
от вспомогательного модуля нечем.

Вспомогательные модули объявляются явно — списком ниже, а не догадкой по имени.
Список короткий и растёт медленно; молчаливое исключение по эвристике вернуло бы
ровно ту дыру, которую проверка закрывает.

Модель харнесса, вопрос 2: «проверка считается доросшей до скрипта только после
строки run в gate.sh».
"""

import argparse
import pathlib
import re
import sys

# Модули, которые проверками не являются и в диспетчере быть не должны.
HELPERS = {"slugify.py"}


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
    checks_dir = root / ".harness" / "scripts" / "checks"
    gate = root / ".harness" / "scripts" / "gate.sh"

    if not gate.is_file():
        return 0

    # Только строки диспетчера: имя checks/*.py встречается и внутри фикстур
    # самотеста, и такое совпадение дало бы ложную находку.
    wired = set()
    for line in gate.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("run "):
            wired.update(re.findall(r"checks/([A-Za-z_0-9]+\.py)", line))
    problems = []

    for path in sorted(checks_dir.rglob("*.py")):
        if path.name in HELPERS or path.name in wired:
            continue
        problems.append(
            f".harness/scripts/checks/{path.name}: проверка не подключена к gate.sh\n"
            f"  → сделай: допиши строку run в .harness/scripts/gate.sh и парный блок\n"
            f"    в ветку --self-test. Файл в checks/ без строки run не выполняется\n"
            f"    никогда — правило выглядит доросшим до скрипта, не будучи исполняемым.\n"
            f"    Это вспомогательный модуль, а не проверка — впиши его в HELPERS здесь\n"
            f"  ✓ ./.harness/scripts/gate.sh называет эту проверку в своём списке"
        )

    for name in sorted(wired - {p.name for p in checks_dir.rglob("*.py")}):
        problems.append(
            f".harness/scripts/gate.sh: вызывает checks/{name}, которого нет\n"
            f"  → сделай: верни файл или убери строку run. Диспетчер, зовущий\n"
            f"    несуществующую проверку, печатает FAIL, который не про работу\n"
            f"  ✓ каждая строка run указывает на существующий файл"
        )

    if problems:
        return report(problems)
    return 0


if __name__ == "__main__":
    sys.exit(main())
