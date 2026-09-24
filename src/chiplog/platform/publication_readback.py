"""Compatibility readback for selected physical writer commands."""

import sqlite3
from typing import Literal

from ._sqlite import PhysicalPublicationCommand, PhysicalRecord
from .publication_inspection import (
    ExpectedPublicationRecord,
    PublicationExpectation,
)
from .publication_inspection import (
    inspect_publication as inspect_expectation,
)


def inspect_publication(
    connection: sqlite3.Connection,
    command: PhysicalPublicationCommand,
    selected_sequence: int,
) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
    if not connection.in_transaction:
        raise ValueError("publication readback requires an active transaction")
    if not isinstance(command, PhysicalPublicationCommand):
        raise ValueError("publication readback requires an exact physical command")
    if type(command.records) is not tuple or not command.records:
        raise ValueError("publication records must be a nonempty tuple")
    if any(not isinstance(record, PhysicalRecord) for record in command.records):
        raise ValueError("publication record identity fields must be nonempty strings")
    return inspect_expectation(
        connection,
        PublicationExpectation(
            command.tenant_id,
            command.operation_kind,
            command.idempotency_key,
            command.request_fingerprint,
            command.expected_head,
            tuple(
                ExpectedPublicationRecord(
                    record.record_id,
                    record.owner,
                    record.schema_id,
                    record.canonical_bytes,
                    record.fingerprint,
                )
                for record in command.records
            ),
        ),
        selected_sequence,
    )
