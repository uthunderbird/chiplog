#!/usr/bin/env python3
"""Ретро заканчивается коммитом.

Ловит:
  1. просроченный каданс — коммитов с последней ретро больше порога;
  2. ретро без вывода: ни одной правки харнесса и ни одного явного «почему нет»;
  3. серию подряд идущих отказов — ветка отказа легальна, но не бесконечна.

Каданс считается в коммитах: RETRO_EVERY коммитов между ретро (не «итераций»).
Статья №3 «Ретро заканчивается коммитом».
"""

import argparse
import pathlib
import re
import subprocess
import sys

RETRO_EVERY = 5
MAX_CONSECUTIVE_SKIPS = 2
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def parse_frontmatter(text):
    m = FRONTMATTER.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip("\"'")
    return out


def resolves(root, rule):
    """Адрес правки существует: файл правила либо раздел AGENTS.md с таким номером."""
    rule = rule.strip()
    m = re.search(r"AGENTS\.md\s*§\s*(\d+)", rule)
    if m:
        agents = root / "AGENTS.md"
        if not agents.is_file():
            return False
        return bool(re.search(rf"^## {m.group(1)}\.", agents.read_text(encoding="utf-8"), re.M))
    # Правкой харнесса может быть не только правило: уставка в thresholds.sh,
    # строка в модели, удалённая проверка. Требуем не вид адреса, а его
    # разрешимость — иначе законный outcome: change упирается в закрытый гейт.
    path = rule.split(",")[0].split(":")[0].strip()
    if path and (root / path).exists():
        return True
    stem = rule.removesuffix(".md").split("/")[-1]
    return (root / ".harness" / "rules" / f"{stem}.md").is_file()


def commits_since(root, path):
    """Сколько коммитов прошло с момента последнего касания path."""
    try:
        staged = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--quiet", "--", str(path)],
            check=False,
        )
        if staged.returncode == 1:
            # Во время pre-commit новая ретро уже входит в проверяемый снимок.
            # Требовать её появления в HEAD означало бы запретить сам коммит,
            # который должен закрыть cadence.
            return 0
        if staged.returncode != 0:
            return None
        last = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%H", "--", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if not last:
            return None
        out = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", f"{last}..HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return int(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


def order_entries_by_history(root, entries):
    """Order committed retros by repository history; current work follows HEAD."""
    try:
        history = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--reverse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        positions = {commit: index for index, commit in enumerate(history)}
    except subprocess.CalledProcessError:
        positions = {}

    staged_sources = {}
    try:
        staged = subprocess.run(
            [
                "git", "-C", str(root), "diff", "--cached", "--name-status", "-M", "--",
                ".harness/retro",
            ],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        for line in staged:
            fields = line.split("\t")
            if fields and fields[0].startswith("R") and len(fields) == 3:
                staged_sources[fields[2]] = fields[1]
    except subprocess.CalledProcessError:
        pass

    def key(path):
        relative = str(path.relative_to(root))
        historical_path = staged_sources.get(relative, relative)
        try:
            created = subprocess.run(
                [
                    "git", "-C", str(root), "log", "--follow", "--diff-filter=A", "-1",
                    "--format=%H", "--", historical_path,
                ],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            created = ""
        return (positions.get(created, len(positions)), path.name)

    return sorted(entries, key=key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    ap.add_argument("--status", action="store_true",
                    help="напечатать счётчики key=value и выйти — для дашборда")
    args = ap.parse_args()
    root = pathlib.Path(args.root)
    retro_dir = root / ".harness" / "retro"

    entries = order_entries_by_history(
        root,
        [p for p in retro_dir.glob("*.md") if p.name not in ("README.md", "TEMPLATE.md")],
    )
    problems = []

    if args.status:
        # Пороги живут здесь и только здесь. Дашборд их рендерит, а не хранит.
        streak = 0
        for outcome in reversed([parse_frontmatter(p.read_text(encoding="utf-8")).get("outcome", "")
                                 for p in entries]):
            if outcome.lower() == "skip":
                streak += 1
            else:
                break
        since = commits_since(root, retro_dir) if entries else None
        print(f"retro_entries={len(entries)}")
        print(f"retro_since={'' if since is None else since}")
        print(f"retro_every={RETRO_EVERY}")
        print(f"retro_skips={streak}")
        print(f"retro_max_skips={MAX_CONSECUTIVE_SKIPS}")
        return 0

    # 1. Каждая ретро обязана иметь вывод.
    for path in entries:
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root)
        outcome = fm.get("outcome", "").lower()
        if outcome not in ("change", "skip"):
            problems.append(
                f"{rel}: outcome не change и не skip — у ретро нет исхода\n"
                f"  → сделай: перечитай запись и проставь исход. change — если урок доведён\n"
                f"    до правки харнесса, тогда укажи rule. skip — если правки нет,\n"
                f"    тогда укажи reason\n"
                f"  ✓ обе ветки легальны и обе оставляют след; исход отражает то, что\n"
                f"    в записи действительно произошло"
            )
            continue
        if outcome == "change":
            rule = fm.get("rule", "")
            if not rule:
                problems.append(
                    f"{rel}: outcome: change, но поле rule пустое — какая правка родилась?\n"
                    f"  → сделай: укажи rule: адрес правки — либо id правила-файла,\n"
                    f"    либо раздел вида «AGENTS.md §7»\n"
                    f"  ✗ не переключай outcome на skip, чтобы снять требование: правка была,\n"
                    f"    и подмена исхода стирает её след\n"
                    f"  ✓ адрес разрешается в существующий файл или раздел"
                )
            elif not resolves(root, rule):
                problems.append(
                    f"{rel}: rule: {rule} — такого адреса нет\n"
                    f"  → сделай: укажи существующий адрес: файл .harness/rules/<id>.md\n"
                    f"    либо раздел вида «AGENTS.md §N». Ретро без адреса не закрыта:\n"
                    f"    признак закрытия — изменившийся файл, а не заполненная клетка\n"
                    f"  ✗ не пиши адрес «на будущее»: правка либо есть в этом коммите,\n"
                    f"    либо исход ретро — skip\n"
                    f"  ✓ адрес открывается и содержит правку, о которой говорит ретро"
                )
        if outcome == "skip" and not fm.get("reason"):
            problems.append(
                f"{rel}: outcome: skip без reason — отказ легален, но оставляет след\n"
                f"  → сделай: впиши reason: почему в этот раз ни один урок не дошёл\n"
                f"    до правки харнесса\n"
                f"  ✓ причина называет, что помешало, а не «нечего чинить»"
            )

    # 2. Серия отказов подряд.
    tail = [parse_frontmatter(p.read_text(encoding="utf-8")).get("outcome", "") for p in entries]
    streak = 0
    for outcome in reversed(tail):
        if outcome.lower() == "skip":
            streak += 1
        else:
            break
    if streak > MAX_CONSECUTIVE_SKIPS:
        problems.append(
            f".harness/retro/: {streak} отказа подряд при пороге {MAX_CONSECUTIVE_SKIPS}\n"
            f"  → реши: перечитай reason у последних отказов. Причины разные и\n"
            f"    содержательные — ретро работает, доведи следующую до outcome: change.\n"
            f"    Причина повторяется дословно — ретро стала разговором, и это уже\n"
            f"    не твой выбор: выноси человеку. Порог трогать не вправе.\n"
            f"    Решение запиши: ./.harness/scripts/decide.sh \"веду ретро до правки\"\n"
            f"    \"причины отказов разные\" \"поднять порог\"\n"
            f"  ✓ появилась ретро с outcome: change — либо развилка вынесена человеку\n"
            f"    и записана в журнал решений"
        )

    # 3. Каданс.
    if entries:
        n = commits_since(root, retro_dir)
        if n is not None and n >= RETRO_EVERY:
            problems.append(
                f".harness/retro/: {n} коммитов с последней ретро при кадансе {RETRO_EVERY}\n"
                f"  → сделай: запусти ./.harness/scripts/new-retro.sh, перечитай\n"
                f"    .harness/observations.md и диффы за период, доведи хотя бы один урок\n"
                f"    до правки харнесса — или прямо запиши, почему в этот раз нет\n"
                f"  ✗ не откладывай ретро «до следующего коммита»: каданс считается\n"
                f"    в коммитах, откладывание только увеличивает счётчик\n"
                f"  ✓ в .harness/retro/ есть запись с outcome: change или skip, и она закоммичена"
            )

    if problems:
        for p in problems:
            for line in p.splitlines():
                print(f"    {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
