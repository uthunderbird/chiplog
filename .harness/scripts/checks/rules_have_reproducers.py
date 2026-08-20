#!/usr/bin/env python3
"""Правило без теста-репродьюсера — суеверие с хорошей памятью.

Ловит четыре вещи:
  1. правило без каталога репродьюсера;
  2. репродьюсер без правила (осиротевший плохой вход);
  3. правило без указанного класса ошибки;
  4. правило, помеченное norm: true, у которого просрочена ревизия.

Статья №2 «У провала есть адрес», статья №3 «Ретро заканчивается коммитом».
"""

import argparse
import datetime as dt
import pathlib
import re
import sys

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
REVISION_DAYS = 90
PLACEHOLDER = "ЗАПОЛНИ"
# Доля прогонов: «3/3», а не «да». Флаг скрывает, сколько раз пробовали.
REPRO_FRACTION = re.compile(r"\A([0-9]+)/([0-9]+)\Z")


def parse_frontmatter(text):
    m = FRONTMATTER.match(text)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        v = v.split("#", 1)[0]
        out[k.strip()] = v.strip().strip("\"'")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    ap.add_argument("--status", action="store_true",
                    help="напечатать счётчики key=value и выйти — для дашборда")
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    rules_dir = root / ".harness" / "rules"
    repro_dir = root / ".harness" / "reproducers"
    problems = []

    if args.status:
        # Порог ревизии живёт здесь и только здесь. Дашборд его рендерит, а не хранит.
        total = norms = 0
        soonest = None
        for path in sorted(rules_dir.glob("*.md")):
            if path.name == "README.md":
                continue
            total += 1
            fm = parse_frontmatter(path.read_text(encoding="utf-8")) or {}
            if fm.get("norm", "").lower() != "true":
                continue
            norms += 1
            try:
                age = (dt.date.today() - dt.date.fromisoformat(fm.get("reviewed", ""))).days
            except ValueError:
                continue
            left = REVISION_DAYS - age
            soonest = left if soonest is None else min(soonest, left)
        print(f"rules_total={total}")
        print(f"rules_norms={norms}")
        print(f"norm_review_in={'' if soonest is None else soonest}")
        print(f"revision_days={REVISION_DAYS}")
        return 0

    rule_ids = set()
    for path in sorted(rules_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root)
        if fm is None:
            problems.append(
                f"{rel}: нет frontmatter — не видно ни id, ни класса ошибки, ни репродьюсера\n"
                f"  → сделай: добавь в начало файла блок\n"
                f"      ---\n"
                f"      id: {path.stem}\n"
                f"      error_class: <класс ошибок, а не этот случай>\n"
                f"      reproducer: .harness/reproducers/{path.stem}\n"
                f"      ---\n"
                f"  ✓ все три поля на месте, и error_class называет класс: под него подходит\n"
                f"    хотя бы один мыслимый случай, отличный от исходного"
            )
            continue

        # Ключ — имя файла, и только оно: commit_trail.py берёт его оттуда же.
        # Поле id, разошедшееся с именем, делало правило некоммитируемым.
        rule_id = path.stem
        rule_ids.add(rule_id)

        error_class = fm.get("error_class", "")
        if not error_class or error_class.startswith(PLACEHOLDER):
            problems.append(
                f"{rel}: не заполнен error_class\n"
                f"  → сделай: назови класс ошибок, а не этот случай. Проверь формулировку\n"
                f"    вопросом: подойдёт ли она под следующую ошибку того же рода в другом файле\n"
                f"  ✓ под error_class подходит хотя бы один мыслимый случай, отличный от\n"
                f"    исходного — иначе родня пройдёт в метре от правила"
            )

        is_norm = fm.get("norm", "").lower() == "true"
        target = repro_dir / rule_id

        if is_norm:
            # Норму человек вправе установить словом. Но она живёт под ревизией,
            # пока не обрастёт входом.
            reviewed = fm.get("reviewed")
            if not reviewed:
                problems.append(
                    f"{rel}: norm: true без поля reviewed — норма без входа не под ревизией\n"
                    f"  → сделай: добавь reviewed: <YYYY-MM-DD> — дату, когда норму\n"
                    f"    последний раз пересматривали по существу\n"
                    f"  ✗ не ставь сегодняшнюю дату, если ревизии не было: это обнулит\n"
                    f"    каданс, не проведя ревизию, — правка артефакта вместо починки\n"
                    f"  ✓ дата отсылает к реальному пересмотру, след которого есть в истории"
                )
            else:
                try:
                    age = (dt.date.today() - dt.date.fromisoformat(reviewed)).days
                except ValueError:
                    problems.append(
                        f"{rel}: reviewed: {reviewed} — не дата в формате YYYY-MM-DD\n"
                        f"  → команда: перепиши поле как reviewed: 2026-01-31 (год-месяц-день)"
                    )
                    age = 0
                if age > REVISION_DAYS:
                    problems.append(
                        f"{rel}: норма не ревизовалась {age} дн. (порог {REVISION_DAYS})\n"
                        f"  → реши: проведи ревизию сам. Поищи рецидив класса в\n"
                        f"    .harness/observations.md и в истории (git log --grep). Нашёл —\n"
                        f"    обрасти норму репродьюсером. Не нашёл — продли reviewed\n"
                        f"    сегодняшним числом. Выселить норму, установленную человеком\n"
                        f"    словом, ты не вправе: эту ветку выноси человеку.\n"
                        f"    Решение запиши: ./.harness/scripts/decide.sh \"продлил ревизию\n"
                        f"    {rule_id}\" \"рецидива не нашёл там-то\" \"обрастить входом\"\n"
                        f"  ✗ не продлевай reviewed, не поискав рецидив: это правка артефакта\n"
                        f"    вместо ревизии, и следующая ревизия придёт к тому же месту\n"
                        f"  ✓ ревизия оставила след: либо появился\n"
                        f"    .harness/reproducers/{rule_id}/, либо reviewed обновлён,\n"
                        f"    и в журнале решений записано, на чём основан выбор"
                    )
        elif not target.is_dir():
            problems.append(
                f"{rel}: нет репродьюсера {target.relative_to(root)}/\n"
                f"  → сделай: положи в {target.relative_to(root)}/ заведомо плохой вход, на\n"
                f"    котором провал воспроизводится, и подключи его к .harness/scripts/test.sh.\n"
                f"    Вход невозможен — пометь правило norm: true и добавь reviewed\n"
                f"  ✓ вход падает без правила и проходит с ним"
            )
        elif fm.get("kind", "").lower() == "agent":
            # Провал агента не воспроизводится файлом. Вход это тройка:
            # что просили, в каком состоянии был мир, при каком харнессе.
            case = target / "case.md"
            if not case.is_file():
                problems.append(
                    f"{rel}: kind: agent, но нет {target.relative_to(root)}/case.md\n"
                    f"  → сделай: заведи кейс ./.harness/scripts/new-case.sh {rule_id} и заполни\n"
                    f"    его: реплики человека дословно, предикат отказа, диагноз отдельно.\n"
                    f"    Файлом провал агента не воспроизводится — входом служит тройка\n"
                    f"    «что просили, в каком состоянии мир, при каком харнессе»\n"
                    f"  ✓ ./.harness/scripts/replay.sh {rule_id} готовит прогон, и провал\n"
                    f"    на нём повторяется"
                )
            else:
                body = case.read_text(encoding="utf-8")
                fm_case = parse_frontmatter(body) or {}
                repro = fm_case.get("repro", "").strip()
                bad = None
                if not repro:
                    bad = "поля repro нет или оно пустое"
                elif PLACEHOLDER not in repro:
                    m = REPRO_FRACTION.match(repro)
                    if not m:
                        bad = f"repro: {repro} — не доля"
                    elif int(m.group(2)) == 0:
                        bad = f"repro: {repro} — ноль прогонов"
                    elif int(m.group(1)) > int(m.group(2)):
                        bad = f"repro: {repro} — воспроизведений больше, чем прогонов"
                if bad:
                    problems.append(
                        f"{target.relative_to(root)}/case.md: {bad}\n"
                        f"  → сделай: запиши долю вида 3/3 — сколько прогонов из скольких\n"
                        f"    воспроизвели провал. Один прогон недетерминированной системы\n"
                        f"    не значит ничего, поэтому флаг тут не подходит: «да» скрывает,\n"
                        f"    сколько раз на самом деле пробовали, а 0/0 скрывает, что\n"
                        f"    не пробовали вовсе\n"
                        f"  ✓ поле читается как N/M без пробелов, M больше нуля, N не больше M"
                    )
                if PLACEHOLDER in body:
                    problems.append(
                        f"{target.relative_to(root)}/case.md: остались незаполненные поля\n"
                        f"  → сделай: заполни всё, что помечено {PLACEHOLDER}. Реплики человека —\n"
                        f"    дословно, без дописывания смысла. Предикат отказа — наблюдаемый\n"
                        f"    признак, а не пересказ. Диагноз — отдельным разделом, он на\n"
                        f"    прогон не подаётся\n"
                        f"  ✓ третье лицо без твоего контекста может прогнать кейс по одному\n"
                        f"    только этому файлу"
                    )
        elif not any(p for p in target.iterdir() if p.name != "README.md"):
            problems.append(
                f"{rel}: каталог репродьюсера {target.relative_to(root)}/ пуст\n"
                f"  → сделай: положи туда сам плохой вход — файл, тест или скрипт,\n"
                f"    на котором провал воспроизводится\n"
                f"  ✗ не оставляй пустой каталог ради зелёного гейта: это правка\n"
                f"    артефакта вместо починки, обратного хода она не даёт\n"
                f"  ✓ вход падает без правила и проходит с ним"
            )

    if repro_dir.is_dir():
        for path in sorted(repro_dir.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.name not in rule_ids:
                problems.append(
                    f".harness/reproducers/{path.name}/: репродьюсер без правила\n"
                    f"  → реши: выясни по истории —\n"
                    f"    git log --diff-filter=D -- .harness/rules/{path.name}.md\n"
                    f"    Правило удаляли — прогони вход: рецидивирует, верни правило;\n"
                    f"    не рецидивирует, удали каталог. Правила не было — допиши правило\n"
                    f"    под этот вход.\n"
                    f"    Решение запиши: ./.harness/scripts/decide.sh \"удалил осиротевший\n"
                    f"    вход {path.name}\" \"рецидива при прогоне нет\" \"вернуть правило\"\n"
                    f"  ✓ либо есть .harness/rules/{path.name}.md, либо каталог удалён,\n"
                    f"    а его вход прогнан на отсутствие рецидива"
                )

    if problems:
        for p in problems:
            for line in p.splitlines():
                print(f"    {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
