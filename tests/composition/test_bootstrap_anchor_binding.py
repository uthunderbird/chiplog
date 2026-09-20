"""Bootstrap may anchor only its own exact fence over the observed authority rows."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from chiplog.architecture.r7_storage_surface import AUTHORITY_STORAGE_MEMBERS, StorageMember
from chiplog.composition import r7_planning
from chiplog.composition.r7_planning import R7PlanningRuntime, open_r7_runtime
from chiplog.platform._sqlite import FenceAdvanceCommand, PlatformMutationResult
from chiplog.platform.authority_reads import capture_authority_storage_state


async def _bootstrap(runtime: R7PlanningRuntime) -> None:
    await runtime.bootstrap(
        database_instance_id="database-1",
        principal_id="principal-1",
        credential_id="credential-1",
        session_id="session-1",
        token="token-1",
    )


def _insert_record(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            ("tenant-1", "foreign-record", "planning", "test-schema", b"{}", 1),
        )


@pytest.mark.parametrize("mutation", ["record", "metadata", "own-fence", "other-fence"])
def test_bootstrap_refuses_authority_change_after_real_fence_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    async def exercise() -> None:
        database = tmp_path / "runtime.sqlite"
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            original = runtime._appender.advance_fence
            reached: list[str] = []
            physical_after: list[str] = []

            async def mutate(command: FenceAdvanceCommand) -> PlatformMutationResult:
                result = await original(command)
                assert result.disposition == "COMMITTED"
                # The real trust owner authorized and the real writer committed the fence.
                assert runtime._trust.owner_snapshot_entries()
                assert runtime._commitment_journal.load("tenant-1") is None
                with sqlite3.connect(database) as connection:
                    assert connection.execute(
                        "SELECT generation, frontier FROM deletion_fences WHERE tenant_id = ?",
                        ("tenant-1",),
                    ).fetchone() == (command.generation, command.frontier)
                before = capture_authority_storage_state(database)[0]
                # Fault injection changes actual SQLite authority, not authorization evidence.
                if mutation == "record":
                    _insert_record(database)
                else:
                    with sqlite3.connect(database) as connection:
                        if mutation == "metadata":
                            connection.execute("UPDATE store_metadata SET store_version = 2")
                        elif mutation == "own-fence":
                            connection.execute(
                                "UPDATE deletion_fences SET frontier = 7 WHERE tenant_id = ?",
                                ("tenant-1",),
                            )
                        else:
                            connection.execute(
                                "INSERT INTO deletion_fences VALUES ('other', 'other-fence', 1)"
                            )
                physical_after.append(capture_authority_storage_state(database)[0])
                assert physical_after[-1] != before
                reached.append(mutation)
                return result

            monkeypatch.setattr(runtime._appender, "advance_fence", mutate)
            with pytest.raises(RuntimeError, match="changed beyond its exact fence"):
                await _bootstrap(runtime)
            assert reached == [mutation]
            assert runtime._commitment_journal.load("tenant-1") is None
            assert capture_authority_storage_state(database)[0] == physical_after[-1]

    asyncio.run(exercise())


def test_fresh_bootstrap_cannot_anchor_preexisting_authority_record(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "runtime.sqlite"
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            _insert_record(database)
            before = capture_authority_storage_state(database)[0]
            with pytest.raises(RuntimeError, match="cannot anchor preexisting authority data"):
                await _bootstrap(runtime)
            assert runtime._trust.owner_snapshot_entries() == ()
            assert runtime._commitment_journal.load("tenant-1") is None
            assert capture_authority_storage_state(database)[0] == before
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT * FROM deletion_fences").fetchall() == []

    asyncio.run(exercise())


def test_real_owner_denies_repeated_bootstrap_without_rewriting_anchor(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "runtime.sqlite"
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            await _bootstrap(runtime)
            path = database.with_suffix(database.suffix + ".r7-authority-commitment.json")
            before_bytes, before_inode = path.read_bytes(), path.stat().st_ino
            before_anchor = runtime._commitment_journal.load("tenant-1")
            assert before_anchor == capture_authority_storage_state(database)[0]
            before_trust = runtime._trust.owner_snapshot_entries()
            with pytest.raises(PermissionError, match=r"^bootstrap denied$"):
                await _bootstrap(runtime)
            assert runtime._trust.owner_snapshot_entries() == before_trust
            assert path.read_bytes() == before_bytes
            assert path.stat().st_ino == before_inode
            assert runtime._commitment_journal.load("tenant-1") == before_anchor
            assert capture_authority_storage_state(database)[0] == before_anchor

    asyncio.run(exercise())


def test_binding_covers_metadata_and_future_manifest_authority_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        database = tmp_path / "runtime.sqlite"
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE future_authority (key TEXT, value BLOB)")
                connection.execute("INSERT INTO future_authority VALUES ('future', x'0102')")
            monkeypatch.setattr(
                r7_planning,
                "AUTHORITY_STORAGE_MEMBERS",
                (
                    *AUTHORITY_STORAGE_MEMBERS,
                    StorageMember("future_authority", ("key", "value"), True),
                ),
            )
            with runtime._authority_gate().hold():
                before, own_fence = runtime._bootstrap_storage_binding()
                assert own_fence is None
                assert dict(before)["store_metadata"] == ((1, 1),)
                assert dict(before)["future_authority"] == (("future", b"\x01\x02"),)
                assert set(dict(before)) == {
                    member.table for member in AUTHORITY_STORAGE_MEMBERS if member.authority_bearing
                } | {"future_authority"}
                with sqlite3.connect(database) as connection:
                    connection.execute("UPDATE future_authority SET value = x'03'")
                after, _ = runtime._bootstrap_storage_binding()
                assert after != before
                assert dict(after)["future_authority"] == (("future", b"\x03"),)

    asyncio.run(exercise())
