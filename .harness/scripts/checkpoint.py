#!/usr/bin/env python3
"""Explicit feature-branch checkpoint: selected tests, never merge readiness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R8_CATALOG = "src/chiplog/inert_shared/r8-implementation-v1.json"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def reject(reason: str, action: str) -> int:
    print(f"checkpoint: {reason}", file=sys.stderr)
    print(f"→ сделай: {action}", file=sys.stderr)
    print("✓ рабочая ветка, неизменный staged-снимок и явные pytest selectors", file=sys.stderr)
    return 1


def r8_implementation_preflight(staged: list[str]) -> int | None:
    untracked_names = git(
        "ls-files", "--others", "--exclude-standard", "-z", "--", "src/chiplog"
    ).split("\0")
    untracked = [
        name
        for name in untracked_names
        if name and Path(name).suffix == ".py"
    ]
    if untracked:
        return reject(
            f"неотслеживаемый Python вне R8 catalog: {untracked[0]}",
            "добавь Python-файл в index, обнови проверенный R8 catalog и повтори checkpoint",
        )
    implementation_changed = any(
        name == R8_CATALOG
        or (name.startswith("src/chiplog/") and Path(name).suffix == ".py")
        for name in staged
    )
    if not implementation_changed:
        return None
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from chiplog.verification.r8_surface import verify_implementation_identity

        verify_implementation_identity(ROOT)
    except Exception as error:
        return reject(
            f"не прошла проверка identity реализации R8: {error}",
            "исправь import или R8 catalog, сделай узкий проверенный repin и повтори checkpoint",
        )
    return None


def main() -> int:
    try:
        git("rev-parse", "--verify", "HEAD")
        try:
            branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
        except subprocess.CalledProcessError:
            return reject(
                "HEAD не указывает на именованную ветку",
                "создай или переключись на именованную рабочую ветку и повтори checkpoint",
            )
        if branch in {"main", "master"}:
            return reject(
                "main/master требуют полной проверки", "используй CHIPLOG_COMMIT_MODE=full"
            )
        merge_head = Path(git("rev-parse", "--git-path", "MERGE_HEAD"))
        if not merge_head.is_absolute():
            merge_head = ROOT / merge_head
        if merge_head.exists():
            return reject("merge требует полной проверки", "заверши merge и запусти полный гейт")
        if git("diff", "--name-only"):
            return reject(
                "worktree отличается от index; проверяемые байты ещё меняются",
                "добавь изменения в index или верни worktree к index; повтори checkpoint",
            )
        staged = git("diff", "--cached", "--name-only", "--no-renames", "-z").split("\0")
        staged = [name for name in staged if name]
        if not staged:
            return reject("пустой staged-снимок", "добавь проверяемый срез в index")
        preflight = r8_implementation_preflight(staged)
        if preflight is not None:
            return preflight
        selectors = json.loads(os.environ.get("CHIPLOG_CHECKPOINT_TESTS", "[]"))
        if not isinstance(selectors, list) or any(
            not isinstance(item, str) or not item for item in selectors
        ):
            return reject(
                "CHIPLOG_CHECKPOINT_TESTS должен быть JSON-массивом selectors",
                "задай CHIPLOG_CHECKPOINT_TESTS как JSON-массив явных pytest selectors",
            )
        if not selectors:
            # Deletion/rename of a code file cannot masquerade as documentation.
            if not all(Path(name).suffix == ".md" for name in staged):
                return reject(
                    "изменён не только Markdown: укажи новые тесты",
                    "задай явные pytest selectors для этого кодового среза",
                )
            print("checkpoint: только Markdown; новых тестов нет; готовность к main не проверена")
            return 0
        for selector in selectors:
            name = selector.split("::", 1)[0]
            path = ROOT / name
            if (not name.startswith("tests/") or ".." in Path(name).parts
                    or not path.name.startswith("test_") or path.suffix != ".py"
                    or not path.is_file() or not path.resolve().is_relative_to(ROOT / "tests")):
                return reject(
                    f"неверный selector: {selector!r}",
                    "исправь selector на существующий test_*.py под tests/",
                )
            git("rev-parse", "--verify", ":" + name)
        print("checkpoint: только выбранные тесты; без регрессии, не готовность к main", flush=True)
        result = subprocess.run(
            ["sh", str(ROOT / ".harness/scripts/clean-git-env.sh"),
             "uv", "run", "pytest", "-q", *selectors],
            cwd=ROOT,
        )
        if result.returncode:
            return reject(
                f"pytest завершился с кодом {result.returncode}",
                "исправь упавший выбранный тест или его selector и повтори checkpoint",
            )
        return 0
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        return reject(
            f"не удалось проверить checkpoint: {error}",
            "разбери ошибку Git, JSON или запуска тестов и повтори checkpoint",
        )


if __name__ == "__main__":
    raise SystemExit(main())
