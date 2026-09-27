"""Typed, read-only H1 projections of one verified authority checkpoint."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, cast

from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_checkpoint import VerifiedAuthoritySnapshot


class H1VerifiedSnapshotRowsError(ValueError):
    """The authenticated checkpoint cannot supply a required H1 row."""


_TABLE_COLUMNS: Final = {
    "deletion_fences": ("tenant_id", "generation", "frontier"),
    "evidence_inbox": (
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
    ),
    "publications": (
        "tenant_id",
        "operation_kind",
        "idempotency_key",
        "request_fingerprint",
        "commit_sequence",
        "record_ids",
    ),
    "records": (
        "tenant_id",
        "record_id",
        "owner",
        "schema_id",
        "canonical_bytes",
        "commit_sequence",
    ),
    "tenant_heads": ("tenant_id", "head"),
}


def _scalar(value: object) -> object:
    if isinstance(value, dict):
        encoded = value.get("base64")
        if set(value) != {"base64"} or not isinstance(encoded, str):
            raise H1VerifiedSnapshotRowsError("checkpoint bytes scalar differs")
        try:
            return base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise H1VerifiedSnapshotRowsError("checkpoint bytes scalar differs") from error
    return value


@dataclass(frozen=True, slots=True)
class H1VerifiedSnapshotRows:
    """One immutable selected post-image, never a live-SQL fallback."""

    _tables: Mapping[str, tuple[tuple[object, ...], ...]]
    commitment: str

    @classmethod
    def from_verified(cls, snapshot: VerifiedAuthoritySnapshot) -> H1VerifiedSnapshotRows:
        tables: dict[str, tuple[tuple[object, ...], ...]] = {}
        for table in snapshot.tables:
            expected = _TABLE_COLUMNS.get(table.table)
            if expected is None:
                continue
            if table.columns != expected:
                raise H1VerifiedSnapshotRowsError("checkpoint H1 table columns differ")
            rows = tuple(tuple(_scalar(value) for value in row) for row in table.rows)
            if table.table in tables:
                raise H1VerifiedSnapshotRowsError("checkpoint H1 table repeats")
            tables[table.table] = rows
        if set(_TABLE_COLUMNS) - set(tables):
            raise H1VerifiedSnapshotRowsError("checkpoint lacks H1 authority table")
        return cls(MappingProxyType(tables), snapshot.ref.blob_sha256)

    def fence(self, tenant_id: str) -> tuple[object, object] | None:
        values = [row[1:] for row in self._tables["deletion_fences"] if row[0] == tenant_id]
        if len(values) > 1:
            raise H1VerifiedSnapshotRowsError("checkpoint has competing deletion fence")
        return cast("tuple[object, object] | None", values[0] if values else None)

    def tenant_head(self, tenant_id: str) -> int:
        values = [row[1] for row in self._tables["tenant_heads"] if row[0] == tenant_id]
        if len(values) > 1 or (values and type(values[0]) is not int):
            raise H1VerifiedSnapshotRowsError("checkpoint tenant head differs")
        return 0 if not values else cast(int, values[0])

    def records(
        self, tenant_id: str, *, through_sequence: int | None = None
    ) -> tuple[tuple[object, ...], ...]:
        values = tuple(
            row[1:]
            for row in self._tables["records"]
            if row[0] == tenant_id
            and (through_sequence is None or (type(row[5]) is int and row[5] <= through_sequence))
        )
        return tuple(sorted(values, key=lambda row: (row[4], row[0])))

    def publications(
        self, tenant_id: str, *, through_sequence: int | None = None
    ) -> tuple[tuple[object, ...], ...]:
        values = tuple(
            row[1:]
            for row in self._tables["publications"]
            if row[0] == tenant_id
            and (through_sequence is None or (type(row[4]) is int and row[4] <= through_sequence))
        )
        return tuple(sorted(values, key=lambda row: (row[3], row[0], row[1])))

    def evidence(self, tenant_id: str) -> tuple[tuple[object, ...], ...]:
        values = tuple(row[1:] for row in self._tables["evidence_inbox"] if row[0] == tenant_id)
        return tuple(sorted(values, key=lambda row: (row[0], row[1])))

    def physical_record(
        self, *, tenant_id: str, record_id: str
    ) -> tuple[object, object, object, object] | None:
        values = [row[2:] for row in self._tables["records"] if row[:2] == (tenant_id, record_id)]
        if len(values) > 1:
            raise H1VerifiedSnapshotRowsError("checkpoint has competing physical record")
        return cast("tuple[object, object, object, object] | None", values[0] if values else None)

    def require_complete(self, command: PhysicalPublicationCommand) -> None:
        """Reproduce COMPLETE membership against this immutable post-image."""
        sequence = command.expected_head + 1
        if sequence > self.tenant_head(command.tenant_id):
            raise H1VerifiedSnapshotRowsError("checkpoint command is beyond tenant head")
        publication = self.publication(
            tenant_id=command.tenant_id,
            operation_kind=command.operation_kind,
            idempotency_key=command.idempotency_key,
        )
        expected_ids = tuple(row.record_id for row in command.records)
        if (
            publication is None
            or publication != (
                command.request_fingerprint,
                sequence,
                "\n".join(expected_ids),
            )
            or len(set(expected_ids)) != len(expected_ids)
        ):
            raise H1VerifiedSnapshotRowsError("checkpoint selected publication differs")
        at_sequence = [
            row
            for row in self._tables["publications"]
            if row[0] == command.tenant_id and row[4] == sequence
        ]
        stored = [
            row
            for row in self._tables["records"]
            if row[0] == command.tenant_id and row[5] == sequence
        ]
        if len(at_sequence) != 1 or {row[1] for row in stored} != set(expected_ids):
            raise H1VerifiedSnapshotRowsError("checkpoint selected membership differs")
        for member in command.records:
            row = self.physical_record(tenant_id=command.tenant_id, record_id=member.record_id)
            if row != (
                member.owner,
                member.schema_id,
                member.canonical_bytes,
                sequence,
            ):
                raise H1VerifiedSnapshotRowsError("checkpoint selected record differs")

    def physical_member(
        self, command: PhysicalPublicationCommand, member: PhysicalRecord
    ) -> PhysicalRecord:
        if member not in command.records:
            raise H1VerifiedSnapshotRowsError("checkpoint member is not selected")
        self.require_complete(command)
        row = self.physical_record(tenant_id=command.tenant_id, record_id=member.record_id)
        if row is None:
            raise H1VerifiedSnapshotRowsError("checkpoint selected record is absent")
        owner, schema_id, raw, _ = row
        if (
            not isinstance(owner, str)
            or not isinstance(schema_id, str)
            or not isinstance(raw, bytes)
        ):
            raise H1VerifiedSnapshotRowsError("checkpoint selected record differs")
        return PhysicalRecord(
            member.record_id, owner, schema_id, raw, hashlib.sha256(raw).hexdigest()
        )

    def publication(
        self, *, tenant_id: str, operation_kind: str, idempotency_key: str
    ) -> tuple[object, ...] | None:
        values = [
            row[3:]
            for row in self._tables["publications"]
            if row[:3] == (tenant_id, operation_kind, idempotency_key)
        ]
        if len(values) > 1:
            raise H1VerifiedSnapshotRowsError("checkpoint has competing publication")
        return values[0] if values else None
