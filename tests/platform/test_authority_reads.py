from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from chiplog.architecture.r7_storage_surface import (
    AUTHORITY_STORAGE_MEMBERS,
    AUTHORITY_STORAGE_SURFACE_DIGEST,
    ReadEdge,
)
from chiplog.platform._sqlite import SQLiteMaterializer
from chiplog.platform.authority_reads import (
    AMR_FINGERPRINT,
    AuthorityReadFailure,
    BrokerAuthorityReader,
)
from chiplog.platform.read_ledger import (
    BrokerReadLedger,
    BrokerReadState,
    ReadOperation,
    ReadRelease,
)


def _scalar(value: object) -> object:
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def _independent_commitment(path: Path) -> str:
    materialized: list[dict[str, object]] = []
    with sqlite3.connect(path) as connection:
        for member in AUTHORITY_STORAGE_MEMBERS:
            if not member.authority_bearing:
                continue
            columns = ", ".join(f'"{column}"' for column in member.columns)
            order = ", ".join(str(index) for index in range(1, len(member.columns) + 1))
            rows = connection.execute(
                f'SELECT {columns} FROM "{member.table}" ORDER BY {order}'
            ).fetchall()
            materialized.append(
                {
                    "columns": member.columns,
                    "rows": [[_scalar(value) for value in row] for row in rows],
                    "table": member.table,
                }
            )
    return hashlib.sha256(
        json.dumps(materialized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _file_observation(path: Path) -> str:
    stat = path.stat()
    values = {
        "database_device": stat.st_dev,
        "database_inode": stat.st_ino,
        "database_path": str(path.resolve()),
        "wal_path": str(Path(f"{path}-wal").resolve()),
    }
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _database(path: Path) -> None:
    with SQLiteMaterializer(path, record_contracts={"planning": "chiplog.planning.record.v1"}):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO tenant_heads VALUES ('tenant-1', 1)")
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant-1",
                "CREATE_INTENTION_LINE",
                "command-1",
                "fingerprint-1",
                1,
                "\n".join(f"record-{index}" for index in range(1, 6)),
            ),
        )
        for index in range(1, 6):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "tenant-1",
                    f"record-{index}",
                    "planning",
                    "chiplog.planning.record.v1",
                    f'{{"record":{index}}}'.encode(),
                    1,
                ),
            )


def _operation(attempt: str = "attempt-1") -> ReadOperation:
    return ReadOperation(
        variant="PLANNING_PUBLICATIONS",
        request_id=f"request-{attempt}",
        read_attempt_id=attempt,
        tenant_id="tenant-1",
        broker_epoch=1,
        owner_id="projections",
        generation_id="generation-1",
        session_id="session-1",
        capability_id="planning.read",
        target_id="planning-store",
        max_rows=10,
        response_slot_id=f"slot-{attempt}",
    )


def _reader(
    tmp_path: Path,
) -> tuple[BrokerAuthorityReader, BrokerReadLedger, BrokerReadState, Path]:
    database = tmp_path / "data.sqlite3"
    _database(database)
    state = BrokerReadState(
        tenant_id="tenant-1",
        broker_epoch=1,
        credential_session_head="credential-1",
        endpoint_channel_head="endpoint-1",
        file_wal_observation=_file_observation(database),
        journal_head="journal-1",
        materialization_commitment=_independent_commitment(database),
        owner_generation="generation-1",
        owner_draining=False,
        principal_contour_head="contour-1",
        storage_mutation_generation=1,
        trust_transition_head="trust-1",
        authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        amr_fingerprint=AMR_FINGERPRINT,
    )
    ledger = BrokerReadLedger(tmp_path / "read.sqlite3")
    ledger.publish_initial_state(state)
    return BrokerAuthorityReader(database, "database-1", ledger), ledger, state, database


def test_verified_snapshot_releases_only_frozen_canonical_dto(tmp_path: Path) -> None:
    reader, ledger, _, _ = _reader(tmp_path)
    operation = _operation()
    result = reader.execute(operation)
    assert result.disposition == "RELEASED"
    assert result.canonical_result_bytes is not None
    assert b'"publications"' in result.canonical_result_bytes
    assert ledger.dequeue(operation) is None


def test_same_head_raw_content_substitution_fails_full_amr_recomputation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, _, state, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE records SET canonical_bytes = ? WHERE record_id = 'record-1'",
            (b'{"record":"two"}',),
        )
    monkeypatch.setattr(
        "chiplog.platform.authority_reads._file_wal_observation",
        lambda _: state.file_wal_observation,
    )
    with pytest.raises(AuthorityReadFailure, match="AMR commitment"):
        reader.execute(_operation())


def test_prepared_query_edge_substitution_denies_before_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, _, _, _ = _reader(tmp_path)
    monkeypatch.setattr(
        "chiplog.platform.authority_reads.PLANNING_PUBLICATION_READ_EDGES",
        (ReadEdge("PLANNING_PUBLICATIONS", "records", ("tenant_id", "record_id")),),
    )
    with pytest.raises(AuthorityReadFailure, match="prepared query edge"):
        reader.execute(_operation())


def test_publication_with_six_records_is_denied_before_release(tmp_path: Path) -> None:
    reader, ledger, state, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE publications SET record_ids = ? WHERE idempotency_key = 'command-1'",
            ("\n".join(f"record-{index}" for index in range(1, 7)),),
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant-1",
                "record-6",
                "planning",
                "chiplog.planning.record.v1",
                b'{"record":6}',
                1,
            ),
        )
    ledger.reconcile_generation(
        BrokerReadState(
            **{
                **state.model_dump(),
                "materialization_commitment": _independent_commitment(database),
            }
        )
    )
    with pytest.raises(AuthorityReadFailure, match="exact-five"):
        reader.execute(_operation("six-records"))
    assert ledger.dequeue(_operation("six-records")) is None


def test_duplicate_publication_sequence_is_denied_before_pagination(tmp_path: Path) -> None:
    reader, ledger, state, database = _reader(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
            (
                "tenant-1",
                "CREATE_INTENTION_LINE",
                "command-2",
                "fingerprint-2",
                1,
                "\n".join(f"record-{index}" for index in range(6, 11)),
            ),
        )
        for index in range(6, 11):
            connection.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "tenant-1",
                    f"record-{index}",
                    "planning",
                    "chiplog.planning.record.v1",
                    f'{{"record":{index}}}'.encode(),
                    1,
                ),
            )
    ledger.reconcile_generation(
        BrokerReadState(
            **{
                **state.model_dump(),
                "materialization_commitment": _independent_commitment(database),
            }
        )
    )
    with pytest.raises(AuthorityReadFailure, match="commit sequence is not unique"):
        reader.execute(_operation("duplicate-sequence"))
    assert ledger.dequeue(_operation("duplicate-sequence")) is None


class _InvalidatingLedger(BrokerReadLedger):
    def release(
        self,
        operation: ReadOperation,
        expected_state: BrokerReadState,
        proof_fingerprint: str,
        result_bytes: bytes,
    ) -> ReadRelease:
        self.start_owner_drain(operation.tenant_id, expected_state.fingerprint())
        return super().release(operation, expected_state, proof_fingerprint, result_bytes)


def test_invalidator_at_release_cut_suppresses_read_bytes(tmp_path: Path) -> None:
    database = tmp_path / "data.sqlite3"
    _database(database)
    base = BrokerReadState(
        tenant_id="tenant-1",
        broker_epoch=1,
        credential_session_head="credential-1",
        endpoint_channel_head="endpoint-1",
        file_wal_observation=_file_observation(database),
        journal_head="journal-1",
        materialization_commitment=_independent_commitment(database),
        owner_generation="generation-1",
        owner_draining=False,
        principal_contour_head="contour-1",
        storage_mutation_generation=1,
        trust_transition_head="trust-1",
        authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        amr_fingerprint=AMR_FINGERPRINT,
    )
    ledger = _InvalidatingLedger(tmp_path / "read.sqlite3")
    ledger.publish_initial_state(base)
    result = BrokerAuthorityReader(database, "database-1", ledger).execute(_operation())
    assert result.disposition == "STALE_OR_INDETERMINATE_READ"
    assert result.canonical_result_bytes is None
