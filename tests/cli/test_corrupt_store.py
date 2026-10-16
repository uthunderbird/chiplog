import os
import subprocess
import sys

from chiplog.platform.postgres import PostgresServer


def test_canonical_cli_rejects_corrupt_authority_before_output(
    postgres_server: PostgresServer,
) -> None:
    database = postgres_server.user_database("corrupt-cli")
    identity = [
        "--tenant",
        database.identity,
        "--principal",
        "p",
        "--credential",
        "c",
        "--session",
        "s",
    ]
    environment = {
        **os.environ,
        "CHIPLOG_POSTGRES_DSN": postgres_server.conninfo,
        "CHIPLOG_R6_OPERATOR_SECRET": "corrupt-fixture",
    }
    bootstrap = subprocess.run(
        [
            sys.executable,
            "-m",
            "chiplog.cli",
            "bootstrap",
            *identity,
            "--database-instance",
            "db",
            "--token",
            "token",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    with database.connect() as connection:
        connection.execute(
            "UPDATE trust_journal SET canonical_bytes=%s WHERE sequence=1", (b"not-json",)
        )
    process = subprocess.run(
        [sys.executable, "-m", "chiplog.cli", "show", *identity],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0
    assert f"operation=trust.entries tenant={database.identity} record_id=" in process.stderr
    assert process.stdout == ""
