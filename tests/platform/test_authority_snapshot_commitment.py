"""Physical AMR hash uses the exact caller transaction, never another read cut."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.platform._sqlite import (
    SCHEMA_SQL,
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_reads import (
    AuthoritySnapshotIntegrityError,
    capture_authority_snapshot_bytes,
    capture_authority_snapshot_commitment,
    capture_authority_storage_state,
)
from chiplog.platform.workspace_snapshot import read_connection


async def test_commitment_and_rows_remain_on_same_snapshot_during_publication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "database.sqlite"
    with SQLiteMaterializer(path, record_contracts={"fixture": "fixture.v1"}) as store:
        async with EventAppender(store, capacity=2) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            before = capture_authority_storage_state(path)[0]
            with read_connection(path) as connection:
                assert capture_authority_snapshot_commitment(connection, "tenant") == before
                assert connection.execute("SELECT * FROM records").fetchall() == []
                result = await appender.submit(
                    PhysicalPublicationCommand(
                        tenant_id="tenant",
                        operation_kind="fixture",
                        idempotency_key="one",
                        request_fingerprint="request",
                        expected_head=0,
                        fence_generation="fence",
                        expected_fence_frontier=0,
                        minimum_fence_frontier=0,
                        records=(
                            PhysicalRecord(
                                "record",
                                "fixture",
                                "fixture.v1",
                                b"record",
                                hashlib.sha256(b"record").hexdigest(),
                            ),
                        ),
                    )
                )
                assert result.disposition == "COMMITTED"
                after = capture_authority_storage_state(path)[0]
                assert after != before
                assert capture_authority_snapshot_commitment(connection, "tenant") == before
                assert connection.in_transaction
                assert connection.execute("SELECT * FROM records").fetchall() == []
            with read_connection(path) as fresh:
                assert capture_authority_snapshot_commitment(fresh, "tenant") == after
                assert len(fresh.execute("SELECT * FROM records").fetchall()) == 1


@pytest.mark.parametrize("shadow", ["table", "view"])
def test_temp_shadow_cannot_substitute_physical_main_authority(
    tmp_path: Path,
    shadow: str,
) -> None:
    path = tmp_path / "database.sqlite"
    with SQLiteMaterializer(path, record_contracts={"fixture": "fixture.v1"}):
        expected = capture_authority_storage_state(path)[0]
        with closing(sqlite3.connect(path)) as connection:
            if shadow == "table":
                connection.execute("CREATE TEMP TABLE records AS SELECT * FROM main.records")
            else:
                connection.execute("CREATE TEMP TABLE forged AS SELECT * FROM main.records")
                connection.execute("CREATE TEMP VIEW records AS SELECT * FROM forged")
            table = "records" if shadow == "table" else "forged"
            connection.execute(
                f"INSERT INTO temp.{table} VALUES (?, ?, ?, ?, ?, ?)",
                ("tenant", "forged", "fixture", "fixture.v1", b"forged", 99),
            )
            assert len(connection.execute("SELECT * FROM records").fetchall()) == 1
            assert connection.execute("SELECT * FROM main.records").fetchall() == []
            assert capture_authority_snapshot_commitment(connection, "tenant") == expected
            assert connection.in_transaction


@pytest.mark.parametrize("invalid", ["no_transaction", "extra_table", "changed_column", "closed"])
def test_unverifiable_snapshot_has_contextual_typed_failure(tmp_path: Path, invalid: str) -> None:
    path = tmp_path / "database.sqlite"
    with (
        SQLiteMaterializer(path, record_contracts={"fixture": "fixture.v1"}),
        closing(sqlite3.connect(path)) as connection,
    ):
        if invalid == "closed":
            # Explicitly close only to inject an invalid caller-supplied resource.
            connection.close()
        elif invalid != "no_transaction":
            connection.execute("BEGIN")
            if invalid == "extra_table":
                connection.execute("CREATE TABLE unknown_authority (value TEXT)")
            else:
                connection.execute("ALTER TABLE records ADD COLUMN unknown TEXT")
        with pytest.raises(
            AuthoritySnapshotIntegrityError,
            match=r"capture_authority_snapshot_commitment.*tenant=tenant.*record=authority_storage",
        ) as caught:
            capture_authority_snapshot_commitment(connection, "tenant")
        assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "capture",
    [
        capture_authority_snapshot_bytes,
        capture_authority_snapshot_commitment,
    ],
)
@pytest.mark.parametrize("table", ["sqliteXauthority", "unknown_authority", 'quoted"authority'])
def test_snapshot_rejects_unknown_names_before_schema_pragma(capture, table):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SCHEMA_SQL)
        escaped = table.replace('"', '""')
        connection.execute(f'CREATE TABLE "{escaped}" (value TEXT)')
        connection.execute("BEGIN")
        statements = []
        connection.set_trace_callback(statements.append)
        with pytest.raises(AuthoritySnapshotIntegrityError) as caught:
            capture(connection, "tenant")
        assert isinstance(caught.value.__cause__, ValueError)
        assert not any("table_info" in sql.lower() for sql in statements)
        assert connection.in_transaction


@pytest.mark.parametrize("change", ["DROP TABLE records", "ALTER TABLE records RENAME TO renamed"])
def test_snapshot_rejects_missing_registered_table_before_schema_pragma(change):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute(change)
        connection.execute("BEGIN")
        statements = []
        connection.set_trace_callback(statements.append)
        with pytest.raises(AuthoritySnapshotIntegrityError):
            capture_authority_snapshot_bytes(connection, "tenant")
        assert not any("table_info" in sql.lower() for sql in statements)


def test_closed_schema_keeps_exact_v1_blob_null_and_scalar_preimage():
    # Literal table/column/row vocabulary is independent of the production manifest.
    expected = [
        {
            "table": "deletion_fences",
            "columns": ["tenant_id", "generation", "frontier"],
            "rows": [],
        },
        {
            "table": "evidence_inbox",
            "columns": [
                "tenant_id",
                "source_id",
                "evidence_id",
                "fingerprint",
                "canonical_bytes",
                "followup_kind",
                "state",
                "attempt_id",
                "transport_version",
                "cursor",
            ],
            "rows": [
                [
                    "tenant",
                    "source",
                    "evidence",
                    "fingerprint",
                    {"base64": "AP8="},
                    "followup",
                    "pending",
                    None,
                    None,
                    None,
                ]
            ],
        },
        {
            "table": "publications",
            "columns": [
                "tenant_id",
                "operation_kind",
                "idempotency_key",
                "request_fingerprint",
                "commit_sequence",
                "record_ids",
            ],
            "rows": [],
        },
        {
            "table": "records",
            "columns": [
                "tenant_id",
                "record_id",
                "owner",
                "schema_id",
                "canonical_bytes",
                "commit_sequence",
            ],
            "rows": [["tenant", "record", "fixture", "fixture.v1", {"base64": "AP8="}, 3]],
        },
        {"table": "store_metadata", "columns": ["singleton", "store_version"], "rows": [[1, 1]]},
        {"table": "tenant_heads", "columns": ["tenant_id", "head"], "rows": [["tenant", 3]]},
    ]
    expected_bytes = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("INSERT INTO store_metadata VALUES (1,1)")
        connection.execute("INSERT INTO tenant_heads VALUES ('tenant',3)")
        connection.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            ("tenant", "record", "fixture", "fixture.v1", b"\x00\xff", 3),
        )
        connection.execute(
            "INSERT INTO evidence_inbox VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "tenant",
                "source",
                "evidence",
                "fingerprint",
                b"\x00\xff",
                "followup",
                "pending",
                None,
                None,
                None,
            ),
        )
        assert capture_authority_snapshot_bytes(connection, "tenant") == expected_bytes
        assert capture_authority_snapshot_commitment(connection, "tenant") == (
            hashlib.sha256(expected_bytes).hexdigest()
        )
