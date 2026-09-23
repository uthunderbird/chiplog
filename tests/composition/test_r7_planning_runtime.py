from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition import r7_planning
from chiplog.composition.r6 import open_r6_runtime
from chiplog.composition.r7_planning import open_r7_runtime
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform.authority_reads import (
    AuthorityReadResult,
)
from chiplog.platform.r7_trust import TrustOwnerResult
from chiplog.platform.read_ledger import ReadOperation
from tests.support.r7_boundary_seed import prepare_boundary_database


def _command(tenant: TenantId, *, purpose: str = "Prepare release") -> CreateIntentionLine:
    return CreateIntentionLine(
        RecordId(tenant, "command-1"),
        RecordId(tenant, "intention-1"),
        RecordId(tenant, "revision-1"),
        purpose,
        "act-1",
    )


def test_brokered_r7_create_replays_and_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"
    tenant = TenantId("tenant-1")

    async def first_generation() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=b"r7-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            committed = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert committed.disposition == "COMMITTED"
            assert committed.result is not None
            assert committed.result.commit_sequence == 1
            replay = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert replay.disposition == "REPLAY"
            assert replay.result == committed.result

    asyncio.run(first_generation())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (5,)

    async def second_generation() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=b"r7-test-secret"
        ) as runtime:
            replay = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert replay.disposition == "REPLAY"
            conflict = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant, purpose="Changed"),
            )
            assert conflict.disposition == "CONFLICT"

    asyncio.run(second_generation())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (5,)


def test_brokered_r7_identity_substitution_denies_without_write(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"
    tenant = TenantId("tenant-1")

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=b"r7-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            denied = await runtime.create(
                principal_id="principal-2",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert denied.disposition == "DENIED"

    asyncio.run(exercise())
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)


def test_bootstrap_cannot_commit_when_quarantined_trust_owner_denies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "chiplog.sqlite3"

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"r7-test-secret"
        ) as runtime:

            async def denied(*_args: object, **_kwargs: object) -> TrustOwnerResult:
                return TrustOwnerResult(
                    disposition="DENIED", reference_bytes=None, reason="owner denied"
                )

            monkeypatch.setattr(runtime, "_trust_call", denied)
            with pytest.raises(PermissionError, match="owner denied"):
                await runtime.bootstrap(
                    database_instance_id="database-1",
                    principal_id="principal-1",
                    credential_id="credential-1",
                    session_id="session-1",
                    token="bootstrap-token-1",
                )
            assert runtime._journal.entries() == ()

    asyncio.run(exercise())


def test_runtime_auth_and_reads_cross_isolated_owner_and_verified_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "chiplog.sqlite3"
    tenant = TenantId("tenant-1")

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=b"r7-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            assert not hasattr(runtime._trust, "authenticate")
            assert not hasattr(runtime._trust, "revalidate")
            calls = 0
            original = runtime._reader.execute

            def observed(operation: ReadOperation) -> AuthorityReadResult:
                nonlocal calls
                calls += 1
                return original(operation)

            monkeypatch.setattr(runtime._reader, "execute", observed)
            outcome = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert outcome.disposition == "COMMITTED"
            assert await runtime.render(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
            ) == ("tenant=tenant-1 records=5", "intention-1", "purpose=Prepare release")
            assert calls == 2

    asyncio.run(exercise())


def test_restart_rejects_offline_authority_storage_tampering(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"

    async def establish() -> None:
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"r7-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(TenantId("tenant-1")),
            )

    asyncio.run(establish())
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE records SET canonical_bytes = X'00' WHERE rowid = 1")

    async def reopen() -> None:
        with pytest.raises(RuntimeError, match="authenticated commitment"):
            async with open_r7_runtime(
                database, tenant_id="tenant-1", operator_secret=b"r7-test-secret"
            ):
                pass

    asyncio.run(reopen())


def test_restart_rejects_missing_commitment_for_bootstrapped_empty_store(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"

    async def establish() -> None:
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"r7-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )

    asyncio.run(establish())
    database.with_suffix(database.suffix + ".r7-authority-commitment.json").unlink()

    async def reopen() -> None:
        with pytest.raises(RuntimeError, match="commitment journal is absent"):
            async with open_r7_runtime(
                database, tenant_id="tenant-1", operator_secret=b"r7-test-secret"
            ):
                pass

    asyncio.run(reopen())


def test_bounded_pages_remain_referentially_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "chiplog.sqlite3"
    tenant = TenantId("tenant-1")
    monkeypatch.setattr(r7_planning, "_READ_PAGE_SIZE", 1)

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=b"r7-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            for suffix in ("1", "2"):
                outcome = await runtime.create(
                    principal_id="principal-1",
                    credential_id="credential-1",
                    session_id="session-1",
                    command=CreateIntentionLine(
                        RecordId(tenant, f"command-{suffix}"),
                        RecordId(tenant, f"intention-{suffix}"),
                        RecordId(tenant, f"revision-{suffix}"),
                        f"purpose-{suffix}",
                        f"act-{suffix}",
                    ),
                )
                assert outcome.disposition == "COMMITTED"
            rendered = await runtime.render(
                principal_id="principal-1", credential_id="credential-1", session_id="session-1"
            )
            assert rendered[0] == "tenant=tenant-1 records=10"
            assert "intention-1" in rendered and "intention-2" in rendered

    asyncio.run(exercise())


def test_default_pages_cross_1000_and_1001_predecessor_boundaries(tmp_path: Path) -> None:
    database = tmp_path / "chiplog.sqlite3"
    tenant = TenantId("tenant-1")
    secret = b"boundary-secret"
    prepare_boundary_database(database)

    async def cross_boundary_r7() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=secret
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            outcome = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=CreateIntentionLine(
                    RecordId(tenant, "command-1002"),
                    RecordId(tenant, "intention-1002"),
                    RecordId(tenant, "revision-1002"),
                    "purpose-1002",
                    "act-1002",
                ),
            )
            assert outcome.disposition == "COMMITTED"
            rendered = await runtime.render(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
            )
            assert rendered[0] == "tenant=tenant-1 records=5010"
            assert "intention-0001" in rendered
            assert "intention-1002" in rendered

    asyncio.run(cross_boundary_r7())


def test_r6_and_r7_emit_identical_durable_bytes_and_rendering(tmp_path: Path) -> None:
    r6_database = tmp_path / "r6.sqlite3"
    r7_database = tmp_path / "r7.sqlite3"
    tenant = TenantId("tenant-1")

    async def exercise_r6() -> tuple[str, ...]:
        async with open_r6_runtime(r6_database, operator_secret=b"parity-secret") as runtime:
            await runtime.bootstrap(
                tenant_id=tenant.value,
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            outcome = await runtime.create(
                tenant_id=tenant.value,
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert outcome.disposition == "COMMITTED"
            return runtime.render(
                tenant_id=tenant.value,
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
            )

    async def exercise_r7() -> tuple[str, ...]:
        async with open_r7_runtime(
            r7_database, tenant_id=tenant.value, operator_secret=b"parity-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            outcome = await runtime.create(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                command=_command(tenant),
            )
            assert outcome.disposition == "COMMITTED"
            return await runtime.render(
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
            )

    r6_render = asyncio.run(exercise_r6())
    r7_render = asyncio.run(exercise_r7())
    assert r7_render == r6_render
    with sqlite3.connect(r6_database) as r6, sqlite3.connect(r7_database) as r7:
        query = "SELECT * FROM records ORDER BY tenant_id, record_id"
        assert r7.execute(query).fetchall() == r6.execute(query).fetchall()


def test_prepare_only_keeps_database_empty_then_legacy_create_replays(tmp_path: Path) -> None:
    """The actual isolated owner may prepare bytes without publishing authority."""
    import json

    from chiplog.composition.r7_planning import PreparedPlanningCandidate

    database = tmp_path / "prepare.sqlite3"
    tenant = TenantId("tenant-1")

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id=tenant.value, operator_secret=b"prepare-test-secret"
        ) as runtime:
            await runtime.bootstrap(
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            kwargs = dict(
                principal_id="principal-1", credential_id="credential-1", session_id="session-1"
            )
            candidate = await runtime._prepare_create(**kwargs, command=_command(tenant))
            assert isinstance(candidate, PreparedPlanningCandidate)
            assert candidate.disposition == "PREPARED"
            assert candidate.expected_tenant_head == 0
            assert json.loads(candidate.command_bytes)["command_id"] == "command-1"
            assert json.loads(candidate.request_bytes)["command_id"] == "command-1"
            proposal = json.loads(candidate.owner_result_bytes)
            assert len(proposal["records"]) == 5
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)
                assert connection.execute("SELECT COUNT(*) FROM publications").fetchone() == (0,)
            committed = await runtime.create(**kwargs, command=_command(tenant))
            assert committed.disposition == "COMMITTED"
            with sqlite3.connect(database) as connection:
                actual = dict(connection.execute("SELECT record_id, canonical_bytes FROM records"))
            import base64

            assert actual == {
                row["record_id"]: base64.b64decode(row["canonical_bytes"], validate=True)
                for row in proposal["records"]
            }
            replay = await runtime._prepare_create(**kwargs, command=_command(tenant))
            assert not isinstance(replay, PreparedPlanningCandidate)
            assert replay.disposition == "REPLAY"
            assert replay.result == committed.result
            conflict = await runtime._prepare_create(
                **kwargs, command=_command(tenant, purpose="changed")
            )
            assert not isinstance(conflict, PreparedPlanningCandidate)
            assert conflict.disposition == "CONFLICT"

    asyncio.run(exercise())
