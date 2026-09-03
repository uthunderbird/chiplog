#!/usr/bin/env python3
"""Провал без императива — это выбор, отданный агенту молча.

Требование к самим проверкам, а не к их предмету: каждое сообщение о провале
обязано сказать, что делать дальше. Класс провала кодируется меткой действия,
и метка же определяет, кто владеет решением:

  → команда:      действие определено полностью — исполняемая строка
  → сделай:       действие определено, но результат это содержимое, а не команда
  → реши:         чек не знает, но агент может выяснить — выясняет, решает
                  и записывает выбор через decide.sh
  → стоп:         решение не выясняется никаким чтением — решает человек
  → чинить нечем: проверка не может судить вообще — чинится проверка, не работа

Два условных поля включаются по правилу, а не по вкусу:
  ✗  названный дешёвый обход — только там, где он реально существует;
  ✓  наблюдаемый исход починки — обязателен для всех меток, кроме «команды»:
     команда сама себе критерий. Метки «стоп» и «чинить нечем» дешевле остальных
     по замыслу и потому обязаны стоить хотя бы этого.
Канон схемы — .harness/HARNESS_MODEL.md, вопрос 3; здесь только то, что проверяет код.

Проверка статическая: разбирает исходники чеков через ast и видит все сообщения,
включая те, которые не триггерит ни одна фикстура. Это и есть её смысл —
покрытие здесь важнее реалистичности.

Статья №2 «У провала есть адрес»: адрес без указания дороги — половина адреса.
"""

import argparse
import ast
import pathlib
import re
import sys

MARKERS = ("→ команда:", "→ сделай:", "→ реши:", "→ стоп:", "→ чинить нечем:")
NEEDS_OUTCOME = ("→ сделай:", "→ реши:", "→ стоп:", "→ чинить нечем:")
FORBIDS_OUTCOME = ("→ команда:",)
NEEDS_JOURNAL = ("→ реши:",)
JOURNAL = "decide.sh"
OUTCOME = "✓"

# Строка, целиком состоящая из одной подстановки, — рендерер, а не сообщение.
RENDERER = re.compile(r"\s*\{[a-zA-Z_][a-zA-Z_0-9]*\}\s*")

# Охват задан правилом, а не списком: список пропустил бы новый скрипт молча.
#   .py в checks/  — problems.append и print(file=sys.stderr);
#   .sh в scripts/ — всё, что уходит в stderr (это и есть канал провала),
#                    плюс heredoc-и хайлайтов, которые по устройству идут в stdout.
HIGHLIGHT_CALL = "hl "


def strings_in(node):
    """Все строковые куски внутри выражения — конкатенация, f-строки, скобки."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return "".join(out)


def messages_from_python(path):
    """(строка_в_файле, текст) для каждого сообщения о провале в чеке."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # problems.append(...) — основной канал
        is_append = (
            isinstance(func, ast.Attribute)
            and func.attr == "append"
            and isinstance(func.value, ast.Name)
            and func.value.id == "problems"
        )
        # print(..., file=sys.stderr) — прямой канал в обход накопителя
        is_stderr_print = (
            isinstance(func, ast.Name)
            and func.id == "print"
            and any(
                kw.arg == "file" and "stderr" in ast.dump(kw.value)
                for kw in node.keywords
            )
        )
        if not (is_append or is_stderr_print):
            continue
        text = "".join(strings_in(arg) for arg in node.args)
        if not text.strip():
            continue
        # Рендер накопленных сообщений — не сообщение. Отличается тем, что весь
        # его текст это одна подстановка: своего содержания в нём нет.
        # Правило, а не список литералов: список пропустил бы новый рендерер молча.
        if is_stderr_print and RENDERER.fullmatch(text):
            continue
        found.append((node.lineno, text))
    return found


def messages_from_shell(path):
    """Блок сообщения в адаптере: подряд идущие echo ... >&2."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block, start, out = [], None, []
    for i, line in enumerate(lines, 1):
        if ">&2" in line and line.strip().startswith("echo"):
            m = re.search(r'echo\s+"(.*)"\s*>&2', line.strip())
            if start is None:
                start = i
            block.append(m.group(1) if m else line)
        elif block:
            out.append((start, "\n".join(block)))
            block, start = [], None
    if block:
        out.append((start, "\n".join(block)))
    return out


def messages_from_heredoc(path):
    """Сообщение heredoc-ом: только если блок уходит в stderr или это вызов hl.
    Прочие heredoc-и (шаблоны, справка) сообщениями о провале не являются."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out, term, start, block, keep = [], None, None, [], False
    for i, line in enumerate(lines, 1):
        if term is None:
            m = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z_0-9]*)'?\s*$", line)
            if m:
                term, start, block = m.group(1), i, []
                keep = ">&2" in line or line.strip().startswith(HIGHLIGHT_CALL)
        elif line.strip() == term:
            if keep:
                out.append((start, "\n".join(block)))
            term = None
        else:
            block.append(line)
    return out


def audit(rel, lineno, text):
    """Одно сообщение против схемы. Возвращает список нарушений."""
    bad = []
    hits = [m for m in MARKERS if m in text]
    if not hits:
        bad.append(
            f"{rel}:{lineno}: сообщение о провале без метки действия\n"
            f"  → сделай: добавь строку с одной из меток — {', '.join(MARKERS)} —\n"
            f"    и допиши после неё само действие. Метка несёт класс провала:\n"
            f"    «команда» и «сделай» решает агент, «стоп» и «чинить нечем» — человек\n"
            f"  ✓ читатель сообщения знает следующее действие, не выходя из сообщения"
        )
        return bad
    if len(hits) > 1:
        bad.append(
            f"{rel}:{lineno}: в одном сообщении {len(hits)} метки: {', '.join(hits)}\n"
            f"  → сделай: раздели на два сообщения или выбери класс. Две метки значат,\n"
            f"    что провалов на самом деле два, либо что класс не определён\n"
            f"  ✓ на сообщение приходится ровно одна метка действия"
        )
    marker = hits[0]
    has_outcome = OUTCOME in text
    if marker in NEEDS_OUTCOME and not has_outcome:
        bad.append(
            f"{rel}:{lineno}: «{marker}» без наблюдаемого исхода\n"
            f"  → сделай: добавь строку с ✓ — по какому наблюдаемому признаку видно,\n"
            f"    что починено. Не можешь назвать признак — ты не знаешь, что требуешь,\n"
            f"    и получишь пустой каталог ради зелёного гейта\n"
            f"  ✓ признак проверяется глазами или командой, а не доверием к исполнителю"
        )
    if marker in NEEDS_JOURNAL and JOURNAL not in text:
        bad.append(
            f"{rel}:{lineno}: «{marker}» без записи решения в журнал\n"
            f"  → сделай: допиши в императив вызов ./.harness/scripts/decide.sh\n"
            f"    с примером аргументов. Решение, принятое за человека молча, — это\n"
            f"    выбор ветки без флага; журнал и есть флаг, поднятый не мешая работе\n"
            f"  ✓ императив заканчивается тем, чем агент запишет свой выбор"
        )
    if marker in FORBIDS_OUTCOME and has_outcome:
        bad.append(
            f"{rel}:{lineno}: «{marker}» с полем ✓ — команда сама себе критерий\n"
            f"  → сделай: убери строку с ✓ либо смени метку на «→ сделай:», если\n"
            f"    действие на самом деле не сводится к исполнению строки\n"
            f"  ✓ у «команды» ровно две строки: адрес с нарушением и сама команда"
        )
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    checks_dir = root / ".harness" / "scripts" / "checks"
    scripts_dir = root / ".harness" / "scripts"

    problems = []
    seen = 0

    for path in sorted(checks_dir.glob("*.py")):
        if path.name == pathlib.Path(__file__).name:
            continue  # свои сообщения проверяются самотестом, не собой же
        rel = path.relative_to(root)
        for lineno, text in messages_from_python(path):
            seen += 1
            problems.extend(audit(rel, lineno, text))

    for path in sorted(scripts_dir.glob("*.sh")):
        rel = path.relative_to(root)
        for lineno, text in messages_from_shell(path) + messages_from_heredoc(path):
            seen += 1
            problems.extend(audit(rel, lineno, text))

    if seen == 0:
        print(
            "    сообщений о провале не найдено — проверять нечего, и это подозрительно\n"
            "      → чинить нечем: проверь, что .harness/scripts/checks/ на месте\n"
            "        и что чеки складывают сообщения в problems\n"
            "      ✓ messages_are_actionable.py видит хотя бы одно сообщение",
            file=sys.stderr,
        )
        return 1

    if problems:
        for p in problems:
            for line in p.splitlines():
                print(f"    {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
