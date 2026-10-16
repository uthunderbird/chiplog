import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.adapters.driven.codex_auth import CodexTokenStorage
from chiplog.adapters.driven.loop_postgres import PostgresLoopStore
from chiplog.adapters.driven.sqlite_chat_import import import_sqlite_chat
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    EndpointSelection,
    LoopRejected,
    RunRecord,
)
from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding
from chiplog.composition.codex_chat import open_codex_chat
from chiplog.platform._sqlite import SCHEMA_SQL
from chiplog.platform.postgres import PostgresServer


def source_records(tenant: str) -> tuple[RunRecord, ...]:
    principal = f"uid:{os.getuid()}"
    origin = EndpointSelection(
        kind="ORIGIN_EXACT",
        ingress_binding_head=principal,
        endpoint_head=principal,
        endpoint_id="local-cli",
        provider="local-cli",
        recipient=principal,
        canonical_address="local://" + principal,
        credential_binding_head="account",
    )
    run = RunRecord(
        tenant=tenant,
        principal=principal,
        run_id="original-run",
        state="CREATED",
        head="pending",
        predecessor=None,
        prompt="original text",
        policy=BudgetPolicy(live_model=LiveModelBinding(credential_identity="account")),
        origin=origin,
        contour_head="local-cli-codex.v1",
        policy_head="explicit-chat-disclosure.v1",
        worker_session="local-cli",
        event="RunCreated",
    )
    run = run.model_copy(update={"head": "loop:" + run.digest()})
    return run, domain.transition(run, "ACTIVE")


def sqlite_source(path: Path, tenant: str) -> tuple[RunRecord, ...]:
    records = source_records(tenant)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("INSERT INTO store_metadata VALUES (1,1)")
        connection.execute("INSERT INTO deletion_fences VALUES (?, 'r6', 0)", (tenant,))
        connection.execute("INSERT INTO tenant_heads VALUES (?, ?)", (tenant, len(records)))
        for sequence, record in enumerate(records, 1):
            payload = record.canonical_bytes()
            connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                (
                    tenant,
                    record.head,
                    "agent_loop",
                    "chiplog.agent-loop.record.v1",
                    payload,
                    sequence,
                ),
            )
            connection.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?)",
                (
                    tenant,
                    "agent_loop",
                    record.head,
                    hashlib.sha256(payload).hexdigest(),
                    sequence,
                    record.head,
                ),
            )
    return records


def test_explicit_sqlite_chat_import_preserves_original_bytes_and_source(
    tmp_path: Path,
    postgres_server: PostgresServer,
) -> None:
    source = tmp_path / "old.sqlite3"
    originals = sqlite_source(source, "cli:account")
    unchanged = source.read_bytes()
    target = postgres_server.user_database("cli:account")
    assert import_sqlite_chat(source, target, "cli:account") == "IMPORTED"
    assert source.read_bytes() == unchanged
    imported = PostgresLoopStore(target, "cli:account", local_chat=True).snapshot()
    assert imported.records == originals and imported.tenant_head == len(originals)
    assert tuple(
        record.canonical_bytes for batch in target.snapshot().batches for record in batch.records
    ) == tuple(record.canonical_bytes() for record in originals)
    assert import_sqlite_chat(source, target, "cli:account") == "REPLAY"
    assert source.read_bytes() == unchanged


def test_sqlite_import_rejects_foreign_or_nonchat_source_without_partial_rows(
    tmp_path: Path,
    postgres_server: PostgresServer,
) -> None:
    source = tmp_path / "old.sqlite3"
    sqlite_source(source, "cli:account")
    target = postgres_server.user_database("target")
    target.provision()
    with pytest.raises(ValueError, match="fence"):
        import_sqlite_chat(source, target, "foreign")
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(
            "UPDATE publications SET operation_kind='planning.r8_create_intention_line'"
        )
    unchanged = source.read_bytes()
    with pytest.raises(ValueError, match="planning/R14"):
        import_sqlite_chat(source, target, "cli:account")
    assert target.snapshot().head == 0 and source.read_bytes() == unchanged


async def test_existing_sqlite_chat_requires_explicit_import_then_reopens_in_client(
    tmp_path: Path,
    postgres_server: PostgresServer,
) -> None:
    storage = CodexTokenStorage(tmp_path / "private")
    storage.prepare()
    source = storage.directory / "chat-account.sqlite3"
    records = sqlite_source(source, "cli:account")
    before = source.read_bytes()
    binding = LiveModelBinding(credential_identity="account")
    with pytest.raises(LoopRejected, match="explicit migrate-sqlite-chat"):
        async with open_codex_chat(storage, binding):
            raise AssertionError("unmigrated source must not open as a fresh empty dialogue")
    target = postgres_server.user_database("cli:account")
    assert import_sqlite_chat(source, target, "cli:account") == "IMPORTED"
    async with open_codex_chat(storage, binding) as loop:
        assert loop.record("original-run") == records[-1]
    assert source.read_bytes() == before
