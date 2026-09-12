#!/usr/bin/env python3
"""Adapt Codex hook payloads to the repository harness scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = {
    "session-start": "session-start.sh",
    "healthcheck": "healthcheck.sh",
    "guard-edit": "guard-edit.sh",
    "post-tool": "post-tool-checks.py",
}


def fail(message: str, *, blocking: bool, advisory: bool = False) -> int:
    if advisory:
        advise(f"Codex harness hook: {message}; ранняя проверка не выполнена.")
        return 0
    print(f"Codex harness hook: {message}", file=sys.stderr)
    return 2 if blocking else 0


def advise(message: str) -> None:
    if message:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": message[:7000],
                    }
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in SCRIPTS:
        return fail("unknown adapter mode", blocking=True)

    mode = sys.argv[1]
    blocking = mode == "guard-edit"
    advisory = mode == "post-tool"

    def failure(message: str) -> int:
        return fail(message, blocking=blocking, advisory=advisory)

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        return failure(f"invalid hook payload: {exc}")
    if not isinstance(payload, dict):
        return failure("hook payload is not an object")

    if advisory and (
        payload.get("hook_event_name") != "PostToolUse"
        or payload.get("tool_name") not in {"apply_patch", "Bash", "Edit", "Write"}
    ):
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return failure("payload has no cwd")

    env = os.environ.copy()
    if advisory:
        for name in subprocess.check_output(
            ["git", "rev-parse", "--local-env-vars"], text=True
        ).split():
            env.pop(name, None)

    discovered = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        timeout=1 if advisory else None,
    )
    if discovered.returncode != 0 or not discovered.stdout.strip():
        return failure("payload cwd is not inside a Git worktree")
    project = Path(discovered.stdout.strip()).resolve()
    trusted_project = Path(sys.argv[2]).resolve()
    if project != trusted_project:
        return failure("payload cwd belongs to a different Git worktree")
    script = trusted_project / ".harness" / "scripts" / SCRIPTS[mode]
    if not script.is_file():
        return failure(f"harness script not found: {script}")

    env["HARNESS_PROJECT_DIR"] = str(trusted_project)
    if advisory:
        session = payload.get("session_id")
        if not isinstance(session, str) or not session:
            return failure("payload has no session_id")
        try:
            completed = subprocess.run(
                [sys.executable, str(script), str(trusted_project), session],
                cwd=trusted_project,
                env=env,
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return failure(f"advisory checks unavailable: {exc}")
        if completed.returncode:
            return failure("advisory dispatcher failed; run it manually to diagnose")
        advise(completed.stdout.strip())
        return 0
    completed = subprocess.run(["sh", str(script)], cwd=trusted_project, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError) as exc:
        if len(sys.argv) > 1 and sys.argv[1] == "post-tool":
            advise(f"Ранняя проверка харнесса недоступна: {exc}; полный гейт не запускался.")
        else:
            raise
