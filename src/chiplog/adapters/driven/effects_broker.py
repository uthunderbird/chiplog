"""Mechanical effects adapter to the closed broker; no raw SQLite or second writer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import TypeAdapter

from chiplog.capabilities.effects.contracts import (
    EffectCommand,
    EffectCommitted,
    EffectDenied,
    EffectPreparationRequest,
    EffectRecord,
    EffectResult,
    EffectStoreSnapshot,
    ExactHead,
    PreparedEffectPublication,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    CallEffectBatch,
    ExactReplayQuery,
    JournalSelectedPublication,
    NoPlanningParticipant,
    NoSelectedDecision,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PlanEffectBatch,
    PlanningParticipant,
    PublicationAuthentication,
    PublicationIdentity,
    PublicationRejected,
    RegisteredOwnerCommitter,
    SingleOwnerBatch,
    WorkerAuthentication,
)

RECORD_SCHEMA = "chiplog.effects.record.v1"


class EffectsIntegrityError(ValueError):
    """Authoritative broker output is corrupted or unverifiable; no partial result."""


@dataclass(frozen=True)
class EffectsBatchContext:
    """Broker-issued exact immutable participant bytes, never inferred by this adapter."""

    identity: PublicationIdentity
    preparation: EffectPreparationRequest
    prepared: PreparedEffectPublication
    expected: AuthoritativeReadManifest
    authentication: PublicationAuthentication
    planning_command: OwnerCommandBytes | None
    loop_command: OwnerCommandBytes | None
    companion_records: tuple[OwnerRecordBytes, ...]


@dataclass(frozen=True)
class EffectsReplayAbsent:
    """Composition authenticated the lookup; construction itself grants nothing."""

    command_bytes: bytes
    observation: NoSelectedDecision


@dataclass(frozen=True)
class EffectsReplaySelected:
    query: ExactReplayQuery
    preparation: EffectPreparationRequest
    prepared: PreparedEffectPublication


EffectsReplayObservation = EffectsReplayAbsent | EffectsReplaySelected
_COMMAND: TypeAdapter[EffectCommand] = TypeAdapter(EffectCommand)
_OPERATIONS = {
    "AcceptEffectCommand": "effects.accept_call",
    "PublishPlanEffectCommand": "effects.publish_plan_effect",
    "PublishDeliveryIntentCommand": "effects.prepare_delivery",
    "PublishRecoveryIntentCommand": "effects.publish_recovery_intent",
    "AuthorizeDispatchCommand": "effects.authorize",
    "BeforeSendDispositionCommand": "effects.before_send",
    "CommitSendCommand": "effects.commit_send",
    "RecordEvidenceCommand": "effects.record_evidence",
    "ReconcileEffectCommand": "effects.reconcile",
}


def validate_preparation_pair(
    preparation: EffectPreparationRequest,
    prepared: PreparedEffectPublication,
) -> EffectCommand:
    """Mechanical equality only; the broker independently verifies owner semantics."""
    command = _COMMAND.validate_json(preparation.command_bytes)
    if (
        command.canonical_bytes() != preparation.command_bytes
        or preparation.operation != _OPERATIONS[type(command).__name__]
        or prepared.record.source_command != preparation.command_bytes
        or prepared.record.command != command.identity
        or prepared.expected_store != preparation.expected
        or preparation.current.command_id != command.identity.command_id
        or preparation.current.command_fingerprint != command.identity.fingerprint
        or preparation.current.store_frontier != preparation.expected.tenant_head
        or preparation.current.authority.tenant_id != preparation.expected.tenant_id
    ):
        raise ValueError("full preparation and issued owner result differ")
    return command


def validate_replay_observation(
    tenant_id: str,
    command: EffectCommand,
    observation: EffectsReplayObservation,
) -> None:
    if isinstance(observation, EffectsReplayAbsent):
        if (
            observation.command_bytes != command.canonical_bytes()
            or observation.observation.command_id != command.identity.command_id
            or observation.observation.tenant_id != tenant_id
        ):
            raise ValueError("no-selection observation substituted requested command")
        return
    if not isinstance(observation, EffectsReplaySelected):
        raise ValueError("unknown effects replay observation")
    historical = validate_preparation_pair(observation.preparation, observation.prepared)
    query = observation.query
    own = tuple(item for item in query.original_commands if item.owner == "effects")
    raw = observation.preparation.canonical_bytes()
    if (
        historical.canonical_bytes() != command.canonical_bytes()
        or query.identity.tenant_id != tenant_id
        or observation.preparation.expected.tenant_id != tenant_id
        or query.identity.command_id != command.identity.command_id
        or query.operation != observation.preparation.operation
        or len(own) != 1
        or own[0].schema_id != "chiplog.effects.prepare.v1"
        or own[0].canonical_bytes != raw
        or own[0].fingerprint != hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("replay original command/full preparation differs")


class EffectsBrokerQueries(Protocol):
    @property
    def tenant_id(self) -> str: ...

    def effects_snapshot(self) -> EffectStoreSnapshot: ...

    def replay_observation(self, command: EffectCommand) -> EffectsReplayObservation: ...

    def publication_context(
        self, publication: PreparedEffectPublication
    ) -> EffectsBatchContext: ...


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _decode_result(
    result: BrokerPublicationResult, command_id: str, expected_record: EffectRecord
) -> EffectResult:
    if result.command_id != command_id:
        raise ValueError("broker selected another command identity")
    if isinstance(result, PublicationRejected):
        if result.kind == "HOLD":
            disposition = "RECOVERY_HOLD"
        elif result.kind == "INTEGRITY_FAULT":
            disposition = "INDETERMINATE"
        else:
            disposition = result.kind
        return EffectDenied.model_validate(
            {"disposition": disposition, "command_id": command_id, "reason": result.reason}
        )
    matches = []
    for raw in result.complete_records:
        if raw.owner != "effects":
            continue
        if raw.schema_id != RECORD_SCHEMA:
            raise ValueError("unknown selected effects schema")
        record = EffectRecord.model_validate_json(raw.canonical_bytes)
        if (
            record.canonical_bytes() != raw.canonical_bytes
            or raw.fingerprint != hashlib.sha256(raw.canonical_bytes).hexdigest()
            or raw.record_id != record.record.head
            or raw.record_kind != "effects." + record.kind
            or record.snapshot.intent.authority.tenant_id != result.tenant_id
        ):
            raise ValueError("selected effects physical/logical identity mismatch")
        if record.command.command_id == command_id:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError("selected complete batch lacks unique effects command record")
    if matches[0] != expected_record:
        raise ValueError("selected effects record differs from issued owner result")
    return EffectCommitted(
        disposition="REPLAY" if result.kind == "EXACT_REPLAY" else "COMMITTED",
        command_id=command_id,
        journal_decision=ExactHead(
            subject_id=result.decision_id,
            head=result.decision_head,
            fingerprint=result.decision_fingerprint,
        ),
        snapshot=matches[0].snapshot,
    )


def _result(
    result: BrokerPublicationResult, command_id: str, expected_record: EffectRecord
) -> EffectResult:
    try:
        return _decode_result(result, command_id, expected_record)
    except (ValueError, TypeError) as error:
        identities = (
            ",".join(item.record_id for item in result.complete_records)
            if isinstance(result, JournalSelectedPublication)
            else command_id
        )
        raise EffectsIntegrityError(
            f"operation=effects.decode tenant={result.tenant_id} record_id={identities}: {error}"
        ) from error


class BrokerEffectsStore:
    def __init__(self, broker: RegisteredOwnerCommitter, queries: EffectsBrokerQueries) -> None:
        self._broker, self._queries = broker, queries

    def snapshot(self) -> EffectStoreSnapshot:
        return self._queries.effects_snapshot()

    def replay(self, command: EffectCommand) -> EffectResult | None:
        observation = self._queries.replay_observation(command)
        validate_replay_observation(self._queries.tenant_id, command, observation)
        if isinstance(observation, EffectsReplayAbsent):
            return None
        result = self._broker.lookup_exact(observation.query)
        if isinstance(result, NoSelectedDecision):
            raise EffectsIntegrityError("previously selected effects decision disappeared")
        return _result(result, command.identity.command_id, observation.prepared.record)

    async def publish(self, publication: PreparedEffectPublication) -> EffectResult:
        record = publication.record
        if record.kind == "DELIVERY_PREPARED":
            raise ValueError("delivery bytes belong to the CompleteAcceptance coordinator")
        context = self._queries.publication_context(publication)
        command = validate_preparation_pair(context.preparation, context.prepared)
        if (
            publication != context.prepared
            or context.identity.tenant_id != self._queries.tenant_id
            or context.identity.tenant_id != context.preparation.expected.tenant_id
            or context.identity.command_id != command.identity.command_id
        ):
            raise ValueError("publication differs from issued full preparation context")
        if (
            context.expected.tenant_id != publication.expected_store.tenant_id
            or context.expected.tenant_frontier != publication.expected_store.tenant_head
        ):
            raise ValueError("broker publication context differs from exact owner snapshot")
        payload = record.canonical_bytes()
        raw = OwnerRecordBytes(
            owner="effects",
            record_kind="effects." + record.kind,
            record_id=record.record.head,
            schema_id=RECORD_SCHEMA,
            canonical_bytes=payload,
            fingerprint=hashlib.sha256(payload).hexdigest(),
        )
        complete = (*context.companion_records, raw)
        complete_fingerprint = _fingerprint([item.model_dump(mode="json") for item in complete])
        identity = context.identity
        preparation_bytes = context.preparation.canonical_bytes()
        owner_command = OwnerCommandBytes(
            owner="effects",
            schema_id="chiplog.effects.prepare.v1",
            canonical_bytes=preparation_bytes,
            fingerprint=hashlib.sha256(preparation_bytes).hexdigest(),
        )
        if record.kind == "PLAN_EFFECT_PUBLISHED":
            if (
                context.planning_command is None
                or context.loop_command is not None
                or not isinstance(context.authentication, WorkerAuthentication)
            ):
                raise ValueError(
                    "Plan/effect publication lacks exact registered Planning participant"
                )
            result = await self._broker.commit(
                PlanEffectBatch(
                    identity=identity,
                    authentication=context.authentication,
                    expected=context.expected,
                    planning_command=context.planning_command,
                    effects_command=owner_command,
                    complete_records=complete,
                    complete_batch_fingerprint=complete_fingerprint,
                )
            )
        elif record.kind == "INTENT_ACCEPTED":
            if context.loop_command is None or not isinstance(
                context.authentication, WorkerAuthentication
            ):
                raise ValueError("acceptance lacks loop participant and worker invocation")
            result = await self._broker.commit(
                CallEffectBatch(
                    identity=identity,
                    authentication=context.authentication,
                    expected=context.expected,
                    loop_command=context.loop_command,
                    effects_command=owner_command,
                    planning=NoPlanningParticipant()
                    if context.planning_command is None
                    else PlanningParticipant(command=context.planning_command),
                    complete_records=complete,
                    complete_batch_fingerprint=complete_fingerprint,
                )
            )
        else:
            operations = {
                "RECOVERY_INTENT_PUBLISHED": "effects.publish_recovery_intent",
                "DISPATCH_AUTHORIZED": "effects.authorize",
                "BEFORE_SEND_DISPOSITION": "effects.before_send",
                "SEND_COMMITTED": "effects.commit_send",
                "TRANSMISSION_ALLOCATED": "effects.commit_send",
                "EVIDENCE_RECORDED": "effects.record_evidence",
                "RECONCILED": "effects.reconcile",
            }
            if (
                record.kind not in operations
                or context.companion_records
                or context.loop_command is not None
                or context.planning_command is not None
            ):
                raise ValueError("unregistered operation or cross-owner standalone publication")
            request = SingleOwnerBatch.model_validate(
                {
                    "operation": operations[record.kind],
                    "identity": identity,
                    "authentication": context.authentication,
                    "expected": context.expected,
                    "command": owner_command,
                    "complete_records": complete,
                    "complete_batch_fingerprint": complete_fingerprint,
                }
            )
            result = await self._broker.commit(request)
        return _result(result, record.command.command_id, context.prepared.record)
