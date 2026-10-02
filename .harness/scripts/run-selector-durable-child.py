#!/usr/bin/env python3
"""Record the durable child identity before this process execs ``uv``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def identity(pid: int) -> str:
    try:
        result = subprocess.run(
            ("ps", "-p", str(pid), "-o", "lstart="),
            capture_output=True,
            text=True,
        )
    except OSError:
        return "identity-unavailable"
    return result.stdout.strip() if result.returncode == 0 else "absent"


def write(path: Path, value: object) -> None:
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


if len(sys.argv) != 3:
    raise SystemExit(64)
run, selector = Path(sys.argv[1]), sys.argv[2]
pid = os.getpid()
write(run / "child.pid", {"pid": pid, "pgid": os.getpgrp(), "identity": identity(pid)})
os.execvp("uv", ("uv", "run", "pytest", "--", selector))
