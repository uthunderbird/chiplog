"""Mechanical physical membership at a caller-owned transaction cut.

The caller must independently authenticate selection, database identity and the
current authority anchor. COMPLETE establishes none of those on its own.
"""

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ExpectedPublicationRecord:
    record_id: str
    owner: str
    schema_id: str
    canonical_bytes: bytes
    fingerprint: str


@dataclass(frozen=True)
class PublicationExpectation:
    tenant_id: str
    operation_kind: str
    idempotency_key: str
    request_fingerprint: str
    expected_head: int
    records: tuple[ExpectedPublicationRecord, ...]


def inspect_publication(
    connection: sqlite3.Connection,
    command: PublicationExpectation,
    selected_sequence: int,
) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
    """Compare an exact selected command without writing or invoking its guards.

    Invalid expected input or a missing read transaction raises ValueError.
    Valid expectations with partial, extra or changed rows return CONFLICT.
    """
    if not connection.in_transaction:
        raise ValueError("publication readback requires an active transaction")
    if not isinstance(command, PublicationExpectation):
        raise ValueError("publication readback requires an exact physical command")
    if (
        type(command.expected_head) is not int
        or command.expected_head < 0
        or type(selected_sequence) is not int
        or selected_sequence != command.expected_head + 1
    ):
        raise ValueError("selected sequence must equal the exact predecessor plus one")
    if any(
        type(value) is not str or not value
        for value in (
            command.tenant_id,
            command.operation_kind,
            command.idempotency_key,
            command.request_fingerprint,
        )
    ):
        raise ValueError("publication identity fields must be nonempty strings")
    if type(command.records) is not tuple or not command.records:
        raise ValueError("publication records must be a nonempty tuple")
    for record in command.records:
        if not isinstance(record, ExpectedPublicationRecord) or any(
            type(value) is not str or not value
            for value in (record.record_id, record.owner, record.schema_id)
        ):
            raise ValueError("publication record identity fields must be nonempty strings")
        if "\n" in record.record_id or "\r" in record.record_id:
            raise ValueError("publication record identity contains a newline")
        if (
            type(record.canonical_bytes) is not bytes
            or record.fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
        ):
            raise ValueError("publication record canonical bytes or fingerprint differ")
    record_ids = tuple(record.record_id for record in command.records)
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("publication record identities must be unique")

    identity_rows = connection.execute(
        """SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint,
                  commit_sequence, record_ids FROM publications
           WHERE tenant_id = ? AND operation_kind = ? AND idempotency_key = ?""",
        (command.tenant_id, command.operation_kind, command.idempotency_key),
    ).fetchall()
    expected_rows = tuple(
        (
            command.tenant_id,
            record.record_id,
            record.owner,
            record.schema_id,
            record.canonical_bytes,
            selected_sequence,
        )
        for record in command.records
    )
    individual_rows = tuple(
        tuple(row)
        for record_id in record_ids
        for row in connection.execute(
            """SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence
               FROM records WHERE tenant_id = ? AND record_id = ?""",
            (command.tenant_id, record_id),
        )
    )
    sequence_records = connection.execute(
        """SELECT tenant_id, record_id, owner, schema_id, canonical_bytes, commit_sequence
           FROM records WHERE tenant_id = ? AND commit_sequence = ? ORDER BY record_id""",
        (command.tenant_id, selected_sequence),
    ).fetchall()
    sequence_publications = connection.execute(
        """SELECT tenant_id, operation_kind, idempotency_key, request_fingerprint,
                  commit_sequence, record_ids FROM publications
           WHERE tenant_id = ? AND commit_sequence = ?""",
        (command.tenant_id, selected_sequence),
    ).fetchall()
    if not (identity_rows or individual_rows or sequence_records or sequence_publications):
        return "ABSENT"
    expected_publication = (
        command.tenant_id,
        command.operation_kind,
        command.idempotency_key,
        command.request_fingerprint,
        selected_sequence,
        "\n".join(record_ids),
    )
    if (
        tuple(map(tuple, identity_rows)) == (expected_publication,)
        and individual_rows == expected_rows
        and tuple(map(tuple, sequence_records))
        == tuple(sorted(expected_rows, key=lambda row: row[1]))
        and tuple(map(tuple, sequence_publications)) == (expected_publication,)
    ):
        return "COMPLETE"
    return "CONFLICT"
