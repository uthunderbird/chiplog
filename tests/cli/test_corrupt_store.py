from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from functools import partial
from pathlib import Path

from tests.support.r8_journey import IDENTITY, SECRET, authorized_runtime, command, planning_request


async def test_canonical_cli_rejects_corrupt_authority_before_output(tmp_path: Path) -> None:
    database = tmp_path / "planning.sqlite"
    async with authorized_runtime(database) as runtime:
        value = command()
        result = await runtime.create(
            **IDENTITY, command=value, gate_request=planning_request(value)
        )
        assert result.disposition == "COMMITTED"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE records SET canonical_bytes=? WHERE record_id=?", (b"not-json", "revision-1")
        )
    process = await asyncio.to_thread(
        partial(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "chiplog.cli",
                "--database",
                str(database),
                "show",
                "--tenant",
                "t",
                "--principal",
                "p",
                "--credential",
                "c",
                "--session",
                "s",
            ],
            env={**os.environ, "CHIPLOG_R6_OPERATOR_SECRET": SECRET.decode()},
            capture_output=True,
            text=True,
            check=False,
        )
    )
    assert process.returncode != 0
    assert "operation=render tenant=t record_id=revision-1" in process.stderr
    assert process.stdout == ""
