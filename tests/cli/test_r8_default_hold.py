from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_real_cli_has_no_development_entitlement_bypass(tmp_path: Path) -> None:
    database = tmp_path / "cli.sqlite"
    prefix = [sys.executable, "-m", "chiplog.cli", "--database", str(database)]
    identity = ["--tenant", "t", "--principal", "p", "--credential", "c", "--session", "s"]
    environment = {**os.environ, "CHIPLOG_R6_OPERATOR_SECRET": "isolated-cli-fixture"}
    bootstrap = subprocess.run(
        [*prefix, "bootstrap", *identity, "--database-instance", "db", "--token", "token"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    create = subprocess.run(
        [
            *prefix,
            "create",
            "synthetic purpose",
            *identity,
            "--command-id",
            "command",
            "--intention-id",
            "line",
            "--revision-id",
            "revision",
            "--authority-act",
            "act",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert create.returncode != 0 and "HOLD" in create.stderr
    assert create.stdout == ""
    show = subprocess.run(
        [*prefix, "show", *identity], capture_output=True, text=True, env=environment, check=False
    )
    assert show.returncode != 0 and "HOLD" in show.stderr and show.stdout == ""
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
