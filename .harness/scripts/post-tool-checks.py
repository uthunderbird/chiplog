#!/usr/bin/env python3
"""Incremental advisory checks; never a replacement for the commit gate."""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKS = ("messages_are_actionable", "checks_are_wired", "rules_have_reproducers")
CHECK_TIMEOUT = 1.5
OUTPUT_LIMIT = 1600


def fingerprint(root: Path, name: str) -> str:
    scripts = root / ".harness/scripts"
    paths = {scripts / "post-tool-checks.py", scripts / "checks" / f"{name}.py"}
    digest = hashlib.sha256()
    if name == "rules_have_reproducers":
        digest.update(datetime.date.today().isoformat().encode())
        for directory in (root / ".harness/rules", root / ".harness/reproducers"):
            paths.add(directory)
            paths.update(directory.rglob("*"))
    else:
        paths.update((scripts / "checks").rglob("*.py"))
        if name == "messages_are_actionable":
            paths.update(scripts.glob("*.sh"))
        else:
            paths.add(scripts / "gate.sh")
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        if path.is_symlink():
            raise OSError(f"symbolic link in check inputs: {path.relative_to(root)}")
        if path.is_dir():
            digest.update(b"directory\0")
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        else:
            digest.update(b"missing\0")
    return digest.hexdigest()


def run_check(root: Path, name: str) -> tuple[str, bool]:
    command = [
        sys.executable,
        str(root / ".harness/scripts/checks" / f"{name}.py"),
        "--root",
        str(root),
    ]
    # Child output goes to disk, not an unbounded capture_output buffer.
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(
                command, stdout=output, stderr=output, timeout=CHECK_TIMEOUT, check=False, cwd=root
            )
        except subprocess.TimeoutExpired:
            return f"{name}: проверка не завершилась за {CHECK_TIMEOUT} с; повтори вручную.", False
        output.seek(0)
        message = output.read(OUTPUT_LIMIT).decode("utf-8", errors="replace").strip()
        if output.read(1):
            message += "\n… вывод сокращён; полный результат — при ручном запуске."
    if result.returncode == 0:
        return "", True
    return f"{name}:\n{message or 'нет диагностики'}", result.returncode == 1


def inspect(root: Path, session: str) -> str:
    git_dir = Path(
        subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"], text=True, timeout=1
        ).strip()
    )
    state_dir = git_dir / "harness-post-tool"
    state_dir.mkdir(exist_ok=True)
    key = hashlib.sha256(session.encode()).hexdigest()
    state_path = state_dir / f"{key}.json"
    with (state_dir / f"{key}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return ""  # Another call owns this session's scan; next event can retry.
        try:
            state = json.loads(state_path.read_text())
            if not isinstance(state, dict):
                state = {}
        except FileNotFoundError, ValueError:
            state = {}
        findings = []
        for name in CHECKS:
            previous = state.get(name, {})
            if not isinstance(previous, dict):
                previous = {}
            before = fingerprint(root, name)
            if previous.get("input") == before:
                continue
            message, cacheable = run_check(root, name)
            after = fingerprint(root, name)
            if before != after:
                message = f"{name}: входы менялись во время проверки; результат не сохранён."
                cacheable = False
            diagnostic = hashlib.sha256(message.encode()).hexdigest() if message else ""
            if message and diagnostic != previous.get("diagnostic"):
                findings.append(message + f"\n→ команда: python3 .harness/scripts/checks/{name}.py")
            state[name] = {"input": before if cacheable else None, "diagnostic": diagnostic}
        with tempfile.NamedTemporaryFile(mode="w", dir=state_dir, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream)
        try:
            os.replace(temporary, state_path)
        finally:
            temporary.unlink(missing_ok=True)
    if not findings:
        return ""
    return "Ранняя проверка харнесса (неблокирующая; полный гейт не запускался):\n" + "\n\n".join(
        findings
    )


if __name__ == "__main__":
    try:
        message = inspect(Path(sys.argv[1]), sys.argv[2])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        message = f"Ранняя проверка харнесса недоступна: {exc}. Полный гейт не запускался."
    if message:
        print(message)
