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
}


def fail(message: str, *, blocking: bool) -> int:
    print(f"Codex harness hook: {message}", file=sys.stderr)
    return 2 if blocking else 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in SCRIPTS:
        return fail("unknown adapter mode", blocking=True)

    mode = sys.argv[1]
    blocking = mode == "guard-edit"
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        return fail(f"invalid hook payload: {exc}", blocking=blocking)
    if not isinstance(payload, dict):
        return fail("hook payload is not an object", blocking=blocking)

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return fail("payload has no cwd", blocking=blocking)

    discovered = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if discovered.returncode != 0 or not discovered.stdout.strip():
        return fail("payload cwd is not inside a Git worktree", blocking=blocking)
    project = Path(discovered.stdout.strip()).resolve()
    trusted_project = Path(sys.argv[2]).resolve()
    if project != trusted_project:
        return fail("payload cwd belongs to a different Git worktree", blocking=blocking)
    script = trusted_project / ".harness" / "scripts" / SCRIPTS[mode]
    if not script.is_file():
        return fail(f"harness script not found: {script}", blocking=blocking)

    env = os.environ.copy()
    env["HARNESS_PROJECT_DIR"] = str(trusted_project)
    completed = subprocess.run(["sh", str(script)], cwd=trusted_project, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
