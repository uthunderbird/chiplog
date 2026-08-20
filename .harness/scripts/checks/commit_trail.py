#!/usr/bin/env python3
"""Починка заканчивается коммитом, а коммит правила привязан к ошибке.

Ловит:
  1. правило в рабочем дереве, не доехавшее до коммита (незавершённая починка);
  2. правило и его репродьюсер, разъехавшиеся по разным коммитам —
     дифф правила и плохой вход должны лежать рядом.

Заголовки коммитов подделываются за вечер, поэтому эта проверка смотрит
на состав коммита, а не на его текст.
Статья №2 «У провала есть адрес».
"""

import argparse
import pathlib
import subprocess
import sys


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def report(problems):
    """Единственный выход наружу. Всё, что печатается в stderr, проходит здесь —
    иначе сообщение окажется невидимым для messages_are_actionable.py."""
    for p in problems:
        for line in p.splitlines():
            print(f"    {line}", file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[3]))
    args = ap.parse_args()
    root = pathlib.Path(args.root)

    problems = []

    try:
        git(root, "rev-parse", "--git-dir")
    except subprocess.CalledProcessError:
        problems.append(
            "не git-репозиторий — след правил проверить нечем\n"
            "  → чинить нечем: выполни git init или убери commit_trail.py из gate.sh.\n"
            "    Проверка смотрит на состав коммитов, а коммитов не существует\n"
            "  ✓ git rev-parse --git-dir отвечает без ошибки"
        )
        return report(problems)

    # 1. Правило лежит в дереве, но не отслеживается и не проиндексировано.
    untracked = git(root, "ls-files", "--others", "--exclude-standard", ".harness/").splitlines()
    stray = [
        p for p in untracked
        if p.startswith(".harness/rules/")
        and p.endswith(".md")
        and not p.endswith("/README.md")
    ]
    if stray:
        for p in stray:
            problems.append(
                f"{p}: правило не в индексе — незакоммиченная починка\n"
                f"  → команда: git add {p}"
            )

    # 2. Правило и репродьюсер в одном коммите.
    staged = git(root, "diff", "--cached", "--name-only").splitlines()
    def is_norm(rel):
        # Норма обоснована словом человека, а не входом: требовать к ней
        # репродьюсер значит блокировать легальную ветку модели.
        f = root / rel
        try:
            head = f.read_text(encoding="utf-8").split("---")[1]
        except (OSError, IndexError):
            return False
        return any(l.strip().lower() == "norm: true" for l in head.splitlines())

    staged_rules = {
        pathlib.Path(p).stem
        for p in staged
        if p.startswith(".harness/rules/") and p.endswith(".md")
        and not p.endswith("README.md") and not is_norm(p)
    }
    staged_repros = {
        pathlib.Path(p).parts[2]
        for p in staged
        if p.startswith(".harness/reproducers/") and len(pathlib.Path(p).parts) > 2
    }
    try:
        committed_repros = {
            pathlib.Path(p).parts[2]
            for p in git(root, "ls-files", ".harness/reproducers/").splitlines()
            if len(pathlib.Path(p).parts) > 2
        }
    except subprocess.CalledProcessError:
        committed_repros = set()

    for rule_id in sorted(staged_rules - staged_repros - committed_repros):
        problems.append(
            f".harness/rules/{rule_id}.md: правило коммитится без своего репродьюсера\n"
            f"  → сделай: положи плохой вход в .harness/reproducers/{rule_id}/ и добавь\n"
            f"    его в этот же коммит: git add .harness/reproducers/{rule_id}\n"
            f"  ✗ не коммить правило отдельно «а вход потом»: обратного хода не останется —\n"
            f"    удалить правило можно только прогнав его вход\n"
            f"  ✓ git show --stat содержит и правило, и путь под\n"
            f"    .harness/reproducers/{rule_id}/"
        )

    if problems:
        return report(problems)
    return 0


if __name__ == "__main__":
    sys.exit(main())
