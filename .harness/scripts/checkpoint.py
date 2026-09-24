#!/usr/bin/env python3
"""Explicit feature-branch checkpoint: selected tests, never merge readiness."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def reject(reason: str) -> int:
    print(f"checkpoint: {reason}", file=sys.stderr)
    print("→ сделай: исправь выборку новых тестов или используй CHIPLOG_COMMIT_MODE=full",
          file=sys.stderr)
    print("✓ рабочая ветка, неизменный staged-снимок и явные pytest selectors", file=sys.stderr)
    return 1


def main() -> int:
    try:
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        if branch in {"main", "master"}:
            return reject("main/master требуют полной проверки")
        git("rev-parse", "--verify", "HEAD")
        merge_head = Path(git("rev-parse", "--git-path", "MERGE_HEAD"))
        if not merge_head.is_absolute():
            merge_head = ROOT / merge_head
        if merge_head.exists():
            return reject("merge требует полной проверки")
        if git("diff", "--name-only"):
            return reject("worktree отличается от index; сначала зафиксируй проверяемые байты")
        staged = git("diff", "--cached", "--name-only", "--no-renames", "-z").split("\0")
        staged = [name for name in staged if name]
        if not staged:
            return reject("пустой staged-снимок")
        selectors = json.loads(os.environ.get("CHIPLOG_CHECKPOINT_TESTS", "[]"))
        if not isinstance(selectors, list) or any(
            not isinstance(item, str) or not item for item in selectors
        ):
            return reject("CHIPLOG_CHECKPOINT_TESTS должен быть JSON-массивом selectors")
        if not selectors:
            # Deletion/rename of a code file cannot masquerade as documentation.
            if not all(Path(name).suffix == ".md" for name in staged):
                return reject("изменён не только Markdown: укажи новые тесты")
            print("checkpoint: только Markdown; новых тестов нет; готовность к main не проверена")
            return 0
        for selector in selectors:
            name = selector.split("::", 1)[0]
            path = ROOT / name
            if (not name.startswith("tests/") or ".." in Path(name).parts
                    or not path.name.startswith("test_") or path.suffix != ".py"
                    or not path.is_file() or not path.resolve().is_relative_to(ROOT / "tests")):
                return reject(f"неверный selector: {selector!r}")
            git("rev-parse", "--verify", ":" + name)
        print("checkpoint: только выбранные тесты; без регрессии, не готовность к main", flush=True)
        result = subprocess.run(
            ["sh", str(ROOT / ".harness/scripts/clean-git-env.sh"),
             "uv", "run", "pytest", "-q", *selectors],
            cwd=ROOT,
        )
        if result.returncode:
            return reject(f"pytest завершился с кодом {result.returncode}")
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        return reject(f"не удалось проверить checkpoint: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
