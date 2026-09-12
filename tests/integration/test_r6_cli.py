from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from chiplog.capabilities.planning import (
    CreateIntentionLine,
    PlanningOutcome,
    PlanningTrustDecision,
)
from chiplog.composition.r6 import open_r6_runtime
from chiplog.domain_primitives import RecordId, TenantId


def _run(database: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "CHIPLOG_R6_OPERATOR_SECRET": "r6-test-operator-secret"}
    return subprocess.run(
        # Historical R6/R7 semantics only. R8's production CLI is separately
        # tested for default HOLD; this fixture never supplies R8 gate evidence.
        [
            sys.executable,
            "-c",
            "from chiplog import cli; "
            "from chiplog.composition.r7_planning import open_r7_runtime; "
            "cli.open_r8_runtime = open_r7_runtime; cli.main()",
            "--database",
            str(database),
            *arguments,
        ],
        check=check,
        capture_output=True,
        text=True,
        env=environment,
    )


def _identity() -> tuple[str, ...]:
    return (
        "--tenant",
        "tenant-1",
        "--principal",
        "principal-1",
        "--credential",
        "credential-1",
        "--session",
        "session-1",
    )


def _bootstrap(database: Path) -> None:
    result = _run(
        database,
        "bootstrap",
        *_identity(),
        "--database-instance",
        "database-1",
        "--token",
        "bootstrap-token-1",
    )
    assert result.stdout == "BOOTSTRAPPED\n"


def _create_arguments(
    *, command: str = "command-1", purpose: str = "Prepare release"
) -> tuple[str, ...]:
    suffix = command.rsplit("-", 1)[-1]
    return (
        "create",
        purpose,
        *_identity(),
        "--command-id",
        command,
        "--intention-id",
        f"intention-{suffix}",
        "--revision-id",
        f"revision-{suffix}",
        "--authority-act",
        f"act-{suffix}",
    )


def _planning_record_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])


def _authoritative_snapshot(database: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    tables = ("deletion_fences", "tenant_heads", "publications", "records")
    with sqlite3.connect(database) as connection:
        return {
            table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall())
            for table in tables
        }


def test_cli_persists_multiple_commands_replays_and_renders_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)
    assert "COMMITTED commit_sequence=1" in _run(database, *_create_arguments()).stdout
    assert (
        "COMMITTED commit_sequence=2"
        in _run(database, *_create_arguments(command="command-2", purpose="Prepare rollout")).stdout
    )
    assert "REPLAY commit_sequence=1" in _run(database, *_create_arguments()).stdout
    rendered = _run(database, "show", *_identity()).stdout
    assert "tenant=tenant-1 records=10" in rendered
    assert "intention-1" in rendered
    assert "purpose=Prepare release" in rendered
    assert "intention-2" in rendered
    assert "purpose=Prepare rollout" in rendered


@pytest.mark.parametrize(
    "substitution",
    [
        ("--tenant", "tenant-2"),
        ("--principal", "principal-2"),
        ("--credential", "credential-2"),
        ("--session", "session-2"),
    ],
)
def test_cli_identity_substitutions_deny_without_planning_write(
    tmp_path: Path, substitution: tuple[str, str]
) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)
    before = _planning_record_count(database)
    arguments = list(_create_arguments(command="attacker-command"))
    flag, value = substitution
    arguments[arguments.index(flag) + 1] = value
    result = _run(database, *arguments, check=False)
    assert result.returncode != 0
    assert result.stderr.startswith(("DENIED:", "STALE:"))
    assert _planning_record_count(database) == before


def test_structurally_invalid_command_denies_without_any_authoritative_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)
    before = _authoritative_snapshot(database)
    result = _run(database, *_create_arguments(purpose="   "), check=False)
    assert result.returncode != 0
    assert result.stderr.startswith("DENIED:")
    assert _authoritative_snapshot(database) == before


def test_foreign_payload_identifier_denies_without_any_authoritative_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)
    before = _authoritative_snapshot(database)

    async def exercise() -> PlanningOutcome:
        async with open_r6_runtime(database, operator_secret=b"r6-test-operator-secret") as runtime:
            tenant = TenantId("tenant-1")
            foreign = TenantId("tenant-2")
            return await runtime.create(
                tenant_id="tenant-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=CreateIntentionLine(
                    RecordId(tenant, "command-1"),
                    RecordId(foreign, "intention-1"),
                    RecordId(tenant, "revision-1"),
                    "Prepare release",
                    "act-1",
                ),
            )

    outcome = asyncio.run(exercise())
    assert outcome.disposition == "DENIED"
    assert _authoritative_snapshot(database) == before


def test_commit_time_revalidation_denial_rolls_back_the_physical_publication(
    tmp_path: Path,
) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)
    before = _authoritative_snapshot(database)

    async def exercise() -> PlanningOutcome:
        async with open_r6_runtime(database, operator_secret=b"r6-test-operator-secret") as runtime:
            runtime._repository.set_commit_revalidator(
                lambda *_: PlanningTrustDecision("STALE", "trust changed before commit")
            )
            tenant = TenantId("tenant-1")
            return await runtime.create(
                tenant_id="tenant-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=CreateIntentionLine(
                    RecordId(tenant, "command-1"),
                    RecordId(tenant, "intention-1"),
                    RecordId(tenant, "revision-1"),
                    "Prepare release",
                    "act-1",
                ),
            )

    outcome = asyncio.run(exercise())
    assert outcome.disposition == "STALE"
    assert _authoritative_snapshot(database) == before


def test_cli_fails_loudly_on_corrupt_authoritative_record(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)
    _run(database, *_create_arguments())
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE records SET canonical_bytes = ? WHERE record_id = ?",
            (b"not-json", "revision-1"),
        )
        connection.commit()
    result = _run(database, "show", *_identity(), check=False)
    assert result.returncode != 0
    assert "operation=render tenant=tenant-1 record_id=revision-1" in result.stderr
    assert result.stdout == ""


def test_r6_realized_call_graph_crosses_composed_boundaries(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"
    _bootstrap(database)

    async def exercise() -> tuple[str, ...]:
        async with open_r6_runtime(database, operator_secret=b"r6-test-operator-secret") as runtime:
            tenant = TenantId("tenant-1")
            outcome = await runtime.create(
                tenant_id="tenant-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=CreateIntentionLine(
                    RecordId(tenant, "command-1"),
                    RecordId(tenant, "intention-1"),
                    RecordId(tenant, "revision-1"),
                    "Prepare release",
                    "act-1",
                ),
            )
            assert outcome.disposition == "COMMITTED"
            runtime.render(
                tenant_id="tenant-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
            )
            return runtime.call_trace

    trace = asyncio.run(exercise())
    assert trace == (
        "r4.authenticate",
        "planning.inbound",
        "planning.repository",
        "r4.revalidate",
        "r3.event_appender",
        "r4.revalidate",
        "r4.authenticate",
        "projections.rebuild",
    )
