"""Private per-invocation effects read bridge; canonical broker owns authentication.

The source must authenticate exact replay before constructing a fresh observed
cut. It derives complete scope and reproduces the cut at the writer boundary.
These inert objects confer no authority and expose no storage or transport port.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import TypeAdapter

from chiplog.adapters.driven.effects_broker import (
    RECORD_SCHEMA,
    EffectsBatchContext,
    EffectsIntegrityError,
    EffectsReplayObservation,
    validate_preparation_pair,
    validate_replay_observation,
)
from chiplog.capabilities.effects.contracts import (
    CurrentEffectInputs,
    EffectCommand,
    EffectRecord,
    EffectStoreSnapshot,
    PreparedEffectPublication,
)
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes

_COMMAND: TypeAdapter[EffectCommand] = TypeAdapter(EffectCommand)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _decode_row(raw: OwnerRecordBytes, tenant: str) -> EffectRecord:
    if raw.owner != "effects" or raw.schema_id != RECORD_SCHEMA:
        raise ValueError("unknown effects owner/schema")
    record = EffectRecord.model_validate_json(raw.canonical_bytes)
    if (
        record.canonical_bytes() != raw.canonical_bytes
        or hashlib.sha256(raw.canonical_bytes).hexdigest() != raw.fingerprint
        or record.record.head != raw.record_id
        or raw.record_kind != "effects." + record.kind
        or record.snapshot.intent.authority.tenant_id != tenant
    ):
        raise ValueError("effects physical/logical identity or canonical bytes mismatch")
    command = _COMMAND.validate_json(record.source_command)
    if command.canonical_bytes() != record.source_command or command.identity != record.command:
        raise ValueError("effects original command bytes or identity mismatch")
    body = {
        "command": record.command.model_dump(mode="json"),
        "predecessor": None
        if record.predecessor is None
        else record.predecessor.model_dump(mode="json"),
        "kind": record.kind,
        "snapshot": record.snapshot.model_dump(mode="json"),
        "source_command": record.source_command.hex(),
    }
    digest = _digest(body)
    subject = "effects/" + record.command.command_id
    if (
        record.record.subject_id != subject
        or record.record.head != subject + "/" + digest
        or record.record.fingerprint != digest
    ):
        raise ValueError("effects logical record commitment mismatch")
    return record


@dataclass(frozen=True)
class StoredEffectRow:
    commit_sequence: int
    publication_ordinal: int
    batch_record_ids: tuple[str, ...]
    record: OwnerRecordBytes


@dataclass(frozen=True)
class EffectsObservedCut:
    command_bytes: bytes
    tenant_id: str
    tenant_frontier: int
    materialization_commitment: str
    rows: tuple[StoredEffectRow, ...]
    current: CurrentEffectInputs
    context: EffectsBatchContext


class EffectsCutSource(Protocol):
    """Composition-only atomic read/observe; caller inventory is never authoritative."""

    async def read_effect_cut(
        self, command: EffectCommand, expected: EffectStoreSnapshot
    ) -> EffectsObservedCut: ...

    def replay_observation(self, command: EffectCommand) -> EffectsReplayObservation: ...


@dataclass(frozen=True)
class ReplayEffectsQueries:
    """Replay-only binding can exist before any current cut is acquired."""

    tenant_id: str
    replay: EffectsReplayObservation

    def replay_observation(self, command: EffectCommand) -> EffectsReplayObservation:
        validate_replay_observation(self.tenant_id, command, self.replay)
        return self.replay

    def effects_snapshot(self) -> EffectStoreSnapshot:
        raise ValueError("fresh effects cut unavailable in replay-only binding")

    def observe(self, command: EffectCommand, expected: EffectStoreSnapshot) -> CurrentEffectInputs:
        raise ValueError("fresh effects cut unavailable in replay-only binding")

    def publication_context(self, publication: PreparedEffectPublication) -> EffectsBatchContext:
        raise ValueError("fresh effects cut unavailable in replay-only binding")


@dataclass(frozen=True)
class BoundEffectsQueries(ReplayEffectsQueries):
    cut: EffectsObservedCut

    def effects_snapshot(self) -> EffectStoreSnapshot:
        cut = self.cut
        identity = "snapshot"
        try:
            if cut.tenant_id != self.tenant_id:
                raise ValueError("effects cut differs from bound tenant")
            if type(cut.tenant_frontier) is not int or cut.tenant_frontier < 0:
                raise ValueError("invalid effects tenant frontier")
            context = cut.context.expected
            if (
                context.tenant_id != cut.tenant_id
                or context.tenant_frontier != cut.tenant_frontier
                or context.expected_materialization_commitment != cut.materialization_commitment
                or cut.current.store_frontier != cut.tenant_frontier
                or cut.current.authority.tenant_id != cut.tenant_id
            ):
                raise ValueError("effects observation and context differ from exact cut")
            records: list[EffectRecord] = []
            latest: dict[str, EffectRecord] = {}
            record_ids: set[str] = set()
            command_ids: set[str] = set()
            manifests: dict[int, tuple[str, ...]] = {}
            previous_order = (-1, -1)
            for row in cut.rows:
                identity = row.record.record_id
                sequence, ordinal = row.commit_sequence, row.publication_ordinal
                if (
                    type(sequence) is not int
                    or type(ordinal) is not int
                    or not 1 <= sequence <= cut.tenant_frontier
                    or not 0 <= ordinal < len(row.batch_record_ids)
                    or len(set(row.batch_record_ids)) != len(row.batch_record_ids)
                    or any(not item for item in row.batch_record_ids)
                    or row.batch_record_ids[ordinal] != identity
                    or (sequence, ordinal) <= previous_order
                    or (sequence in manifests and manifests[sequence] != row.batch_record_ids)
                ):
                    raise ValueError("effects physical publication order or membership mismatch")
                previous_order = (sequence, ordinal)
                manifests[sequence] = row.batch_record_ids
                record = _decode_row(row.record, cut.tenant_id)
                predecessor = latest.get(record.snapshot.intent.intent_id)
                if (
                    identity in record_ids
                    or record.command.command_id in command_ids
                    or record.command.expected_tenant_head != sequence - 1
                    or record.predecessor != (None if predecessor is None else predecessor.record)
                    or (
                        predecessor is not None
                        and predecessor.snapshot.intent != record.snapshot.intent
                    )
                    or (
                        predecessor is None
                        and record.kind
                        not in {
                            "INTENT_ACCEPTED",
                            "PLAN_EFFECT_PUBLISHED",
                            "DELIVERY_PREPARED",
                            "RECOVERY_INTENT_PUBLISHED",
                        }
                    )
                ):
                    raise ValueError("effects duplicate, omitted predecessor or rival intent chain")
                record_ids.add(identity)
                command_ids.add(record.command.command_id)
                latest[record.snapshot.intent.intent_id] = record
                records.append(record)
            return EffectStoreSnapshot(
                tenant_id=cut.tenant_id, tenant_head=cut.tenant_frontier, records=tuple(records)
            )
        except (ValueError, TypeError, IndexError) as error:
            raise EffectsIntegrityError(
                f"operation=effects.snapshot tenant={cut.tenant_id} record_id={identity}: {error}"
            ) from error

    def _command(self, command: EffectCommand) -> None:
        if (
            command.canonical_bytes() != self.cut.command_bytes
            or command.identity.command_id != self.cut.current.command_id
            or command.identity.fingerprint != self.cut.current.command_fingerprint
            or command.identity.expected_tenant_head != self.cut.tenant_frontier
        ):
            raise ValueError("command differs from immutable effects cut")

    def observe(self, command: EffectCommand, expected: EffectStoreSnapshot) -> CurrentEffectInputs:
        self._command(command)
        if expected != self.effects_snapshot():
            raise ValueError("requested snapshot differs from immutable effects cut")
        return self.cut.current

    def publication_context(self, publication: PreparedEffectPublication) -> EffectsBatchContext:
        context = self.cut.context
        command = validate_preparation_pair(context.preparation, context.prepared)
        self._command(command)
        if (
            publication != context.prepared
            or context.preparation.current != self.cut.current
            or context.preparation.expected != self.effects_snapshot()
            or publication.record.command != command.identity
            or publication.expected_store != self.effects_snapshot()
        ):
            raise ValueError("prepared publication differs from immutable effects cut")
        return self.cut.context
