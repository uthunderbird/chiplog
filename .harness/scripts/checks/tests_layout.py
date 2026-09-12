#!/usr/bin/env python3
"""Check the complete test tree's path/type contract, not test semantics."""

import argparse
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys

CATEGORIES = frozenset({
    "architecture", "domain_primitives", "platform", "capabilities", "composition",
    "cli", "evaluation", "verification", "tooling", "support",
})
ROOT_FILES = frozenset({"AGENTS.md", "conftest.py", "__init__.py"})


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    ).stdout


def snapshot(root, index):
    entries = {}
    raw = git(root, "ls-files", "--stage", "-z", "--", "tests")
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, _oid, stage = header.split()
        if stage != b"0":
            raise ValueError("index contains unresolved merge entries")
        entries[os.fsdecode(name)] = int(mode, 8)
    if index:
        return entries
    for name in git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", "tests").split(b"\0"):
        if name:
            entries.setdefault(os.fsdecode(name), 0)
    observed = {}
    for name, mode in entries.items():
        path = root / name
        # Index tracks deletions until staged; worktree mode observes the actual tree.
        try:
            actual = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if mode == 0o160000:
            observed[name] = mode
            continue
        for parent in path.parents:
            if parent == root:
                break
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise ValueError(f"non-directory ancestor: {parent.relative_to(root)}")
        observed[name] = actual
    return observed


def violations(entries):
    problems = []
    for name, mode in sorted(entries.items()):
        parts = PurePosixPath(name).parts
        if len(parts) < 2 or parts[0] != "tests":
            problems.append(f"{name}: tests must be a directory\n"
                "  → сделай: размести обычные файлы по tests/AGENTS.md и обнови evidence-селекторы\n"
                "  ✓ все пути снимка принадлежат разрешённым категориям и типам")
        elif len(parts) == 2:
            if parts[1] not in ROOT_FILES or not stat.S_ISREG(mode):
                problems.append(f"{name}: root entry must be an allowed ordinary service file\n"
                "  → сделай: размести обычные файлы по tests/AGENTS.md и обнови evidence-селекторы\n"
                "  ✓ все пути снимка принадлежат разрешённым категориям и типам")
        elif parts[1] not in CATEGORIES:
            problems.append(f"{name}: unknown test category {parts[1]}\n"
                "  → сделай: размести обычные файлы по tests/AGENTS.md и обнови evidence-селекторы\n"
                "  ✓ все пути снимка принадлежат разрешённым категориям и типам")
        elif not stat.S_ISREG(mode):
            problems.append(f"{name}: test tree entries must be ordinary files, not links/submodules\n"
                "  → сделай: размести обычные файлы по tests/AGENTS.md и обнови evidence-селекторы\n"
                "  ✓ все пути снимка принадлежат разрешённым категориям и типам")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--index", action="store_true")
    group.add_argument("--worktree", action="store_true")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        actual_root = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
        if actual_root != root:
            raise ValueError("--root must identify the Git worktree root")
        entries = snapshot(root, args.index)
        if not entries:
            raise ValueError("tests has no files in the selected snapshot")
        problems = violations(entries)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(
            f"tests layout: cannot read selected snapshot: {error}\n"
            "  → сделай: восстанови доступный Git-снимок tests и разреши конфликты index\n"
            "  ✓ указанный режим читает полное дерево tests без ошибки", file=sys.stderr,
        )
        return 2
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
