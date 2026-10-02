#!/usr/bin/env python3
"""Run one pytest selector with durable, auditable completion evidence."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=root)


def process_identity(pid: int) -> str:
    try:
        result = subprocess.run(
            ("ps", "-p", str(pid), "-o", "lstart="),
            capture_output=True,
            text=True,
        )
    except OSError:
        return "identity-unavailable"
    return result.stdout.strip() if result.returncode == 0 else "absent"


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def r8_digest(root: Path) -> str:
    paths = sorted(
        path
        for path in git(root, "ls-files", "-z", "--", "src/chiplog", "tests").split(b"\0")
        if path.endswith(b".py")
        or path == b"src/chiplog/inert_shared/r8-implementation-v1.json"
    )
    digest = hashlib.sha256()
    for raw in paths:
        relative = raw.decode("utf-8", "surrogateescape")
        body = (root / relative).read_bytes()
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def untracked_python(root: Path) -> list[str]:
    return sorted(
        raw.decode("utf-8", "surrogateescape")
        for raw in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if raw.endswith(b".py") and (raw.startswith(b"src/") or raw.startswith(b"tests/"))
    )


def tracked_clean(root: Path) -> bool:
    return all(
        subprocess.run(("git", *args), cwd=root).returncode == 0
        for args in (("diff", "--quiet"), ("diff", "--cached", "--quiet"))
    )


def preflight(root: Path) -> tuple[str, str]:
    if not tracked_clean(root):
        raise RuntimeError("tracked index or worktree is dirty")
    stray = untracked_python(root)
    if stray:
        raise RuntimeError("untracked Python input: " + ", ".join(stray))
    return git(root, "rev-parse", "HEAD").decode().strip(), r8_digest(root)


def normalized_selector(root: Path, selector: str) -> str:
    path_text, separator, node = selector.partition("::")
    path = (root / path_text).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError("selector must stay below the repository root") from error
    if not path.exists():
        raise RuntimeError("selector path does not exist")
    return relative.as_posix() + (separator + node if separator else "")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        print("usage: .harness/scripts/run-selector-durable.sh <pytest-selector>", file=sys.stderr)
        return 64
    selector = argv[0]
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    try:
        selector = normalized_selector(root, selector)
        head, before = preflight(root)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"selector launcher preflight: {error}", file=sys.stderr)
        return 1

    key = hashlib.sha256(b"selector\0" + selector.encode("utf-8")).hexdigest()
    runs = root / ".harness/runs"
    common_dir = Path(
        git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    )
    locks = common_dir / "selector-locks"
    runs.mkdir(parents=True, exist_ok=True)
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / key
    try:
        lock.mkdir()
    except FileExistsError:
        print(f"selector launcher lock exists: {lock}", file=sys.stderr)
        return 1
    launcher = {"pid": os.getpid(), "identity": process_identity(os.getpid())}
    atomic_json(
        lock / "lock.json",
        {"head": head, "selector": selector, "digest": before, "launcher": launcher},
    )
    run = runs / f"{int(time.time_ns())}-{key[:16]}"
    run.mkdir()
    started = time.time_ns()
    start = {
        "head": head,
        "selector": selector,
        "r8_digest": before,
        "started_ns": started,
        "run_dir": str(run),
        "lock": str(lock),
        "launcher": launcher,
    }
    atomic_json(run / "start.json", start)
    interrupted: list[int] = []
    child: subprocess.Popen[bytes] | None = None
    escalation_requested = False
    interrupted_at: float | None = None
    signal_grace = float(os.environ.get("CHIPLOG_SELECTOR_SIGNAL_GRACE_SECONDS", "5"))

    def interrupt(signum: int, _frame: object) -> None:
        nonlocal escalation_requested, interrupted_at
        if not interrupted:
            interrupted.append(signum)
            interrupted_at = time.monotonic()
        elif escalation_requested:
            return
        else:
            escalation_requested = True
        if child is not None:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL if escalation_requested else signum)

    for value in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(value, interrupt)
    rc = 1
    child_identity = "not-started"
    try:
        with (run / "stdout.log").open("xb") as log:
            if interrupted:
                raise OSError("signal received before child start")

            child = subprocess.Popen(
                (
                    sys.executable,
                    str(Path(__file__).with_name("run-selector-durable-child.py")),
                    str(run),
                    selector,
                ),
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            child_identity = process_identity(child.pid)
            if interrupted:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, interrupted[0])
            while True:
                try:
                    rc = child.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - (interrupted_at or time.monotonic())
                    if not interrupted or (not escalation_requested and elapsed < signal_grace):
                        continue
                    escalation_requested = True
                    with suppress(ProcessLookupError):
                        os.killpg(child.pid, signal.SIGKILL)
                    try:
                        rc = child.wait(timeout=signal_grace)
                    except subprocess.TimeoutExpired:
                        rc = 1
                    break
    except OSError as error:
        (run / "stdout.log").open("ab").write((f"launcher failure: {error}\n").encode())
    finally:
        try:
            after = r8_digest(root)
            clean = tracked_clean(root)
            stray = untracked_python(root)
            drift = after != before or not clean or bool(stray)
        except (OSError, subprocess.CalledProcessError):
            after, clean, stray, drift = "unavailable", False, ["unavailable"], True
        status = (
            "interrupted"
            if interrupted
            else "drift"
            if drift
            else "failed"
            if rc
            else "verified"
        )
        result = {
            "return_code": rc,
            "r8_digest": after,
            "tracked_clean": clean,
            "untracked_python": stray,
            "status": status,
            "verified": status == "verified",
            "signal": signal.Signals(interrupted[0]).name if interrupted else None,
            "escalated": escalation_requested,
            "ended_ns": time.time_ns(),
            "child": {"identity": child_identity, "pid": child.pid if child else None},
            "manual_cleanup": (
                "inspect recorded launcher and child pid+identity; PID reuse is unsafe"
            ),
        }
        try:
            atomic_json(run / "exit.json", result)
        finally:
            if child is None or child.poll() is not None:
                try:
                    (lock / "lock.json").unlink()
                    lock.rmdir()
                except OSError:
                    pass
    print(f"run_dir={run}")
    return 0 if status == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
