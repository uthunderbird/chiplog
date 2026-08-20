#!/usr/bin/env python3
"""Разрешение на коммит человек даёт сам, а дерево остаётся чистым.

Две проверки над одним журналом .harness/handoff.md:

  1. Хвост со сроком «сейчас» держит коммит, пока не закрыт работой.
     Это и есть чистота дерева: задача, про которую ты сказал «в этом коммите»,
     не уезжает в следующий молча.
  2. Непересказанная запись держит коммит, пока человек не услышал и не разрешил.
     Это единственный настоящий «стоп» в харнессе.

Статья №4 «Отпустить взгляд, не руку»: надзор не исчезает, он перетекает в гейт.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import handoff  # type: ignore[import-not-found]  # noqa: E402 — формат журнала в одном месте


def report(problems):
    for p in problems:
        for line in p.splitlines():
            print(f"    {line}", file=sys.stderr)
    return 1


def plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} {few}"
    return f"{n} {many}"


def listing(entries):
    return "\n".join(
        f"    · [{e['id']}] {e['kind']}"
        + (f" ({e['when']})" if e["when"] else "")
        + f": {e['fields'].get('что', '—')}"
        for e in entries
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    ap.add_argument("--status", action="store_true",
                    help="напечатать счётчики key=value и выйти — для дашборда")
    args = ap.parse_args()
    root = args.root

    if args.status:
        for k, v in handoff.counts(root).items():
            print(f"handoff_{k}={v}")
        return 0

    entries = handoff.parse(root)
    if not entries:
        return 0

    problems = []

    now = [e for e in entries if e["kind"] == "хвост" and e["when"] == "сейчас"]
    if now:
        problems.append(
            f".harness/handoff.md: {plural(len(now), 'хвост помечен', 'хвоста помечены', 'хвостов помечены')} "
            f"сроком «сейчас»\n"
            f"{listing(now)}\n"
            f"  → сделай: закрой их работой в этом коммите и сними\n"
            f"    ./.harness/scripts/handoff.sh --close <N>. Срок «сейчас» ты поставил\n"
            f"    сам, и он существует ради чистоты дерева. Понял, что не в этот\n"
            f"    коммит, — сними хвост и заведи заново со сроком «следующим»,\n"
            f"    чтобы перенос был виден, а не случился молча\n"
            f"  ✗ не переводи срок правкой файла руками: перенос без следа это ровно\n"
            f"    то размывание границы коммита, от которого срок и заведён\n"
            f"  ✓ работа лежит в этом же коммите, и в журнале хвоста больше нет"
        )

    fresh = [e for e in entries if e["status"] == "new"]
    if fresh:
        problems.append(
            f".harness/handoff.md: {plural(len(fresh), 'запись не пересказана', 'записи не пересказаны', 'записей не пересказаны')}, "
            f"разрешение на коммит не получено\n"
            f"{listing(fresh)}\n"
            f"  → стоп: перескажи человеку записи выше своими словами — коротко, по одной\n"
            f"    на строку: что решил за него и с чем, что отложил и почему.\n"
            f"    Получил разрешение — ./.harness/scripts/handoff.sh --ack:\n"
            f"    решения закроются, хвосты «следующим» переедут в следующую сессию.\n"
            f"    Запись, попавшая сюда второй раз с той же формулировкой, — кандидат\n"
            f"    в .harness/observations.md: повторяющийся отложенный хвост это дыра\n"
            f"    в постановке, а не свойство задачи\n"
            f"  ✓ человек в явном виде разрешил коммит, и --ack прошёл"
        )

    # Журнал может быть непустым и при этом никого не держать: пересказанный
    # хвост «следующим» живёт до своего коммита и коммиту не мешает.
    if not problems:
        return 0
    return report(problems)


if __name__ == "__main__":
    sys.exit(main())
