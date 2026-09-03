"""Planning-owned construction and the narrow R5 command path."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NamedTuple, Protocol, TypeVar

from chiplog.domain_primitives.codec import canonical_record_bytes, fingerprint
from chiplog.domain_primitives.identity import RecordId, RecordTypeId, SchemaId
from chiplog.domain_primitives.principal import PermissionScope, PrincipalId
from chiplog.domain_primitives.tenant import TenantId
from chiplog.domain_primitives.versions import (
    CanonicalizationVersion,
    CodecVersion,
    OwnerTag,
    ProducingVersions,
)

CREATE_INTENTION_LINE = "CREATE_INTENTION_LINE"
CREATE_SCOPE = "planning.create_intention_line"
DIRECT_CONTOURS = frozenset({"CLI", "TELEGRAM"})
PLANNING_SCHEMA_ID = "chiplog.planning.record.v1"
PLANNING_RECORD_OWNER = "planning"
PLANNING_COMMIT_BOUNDARY = "planning.commit"
PLANNING_RECORD_TYPES = (
    "chiplog.planning.authorization_evidence",
    "chiplog.planning.committed_result",
    "chiplog.planning.intention_line",
    "chiplog.planning.intention_line_revision",
    "chiplog.planning.planning_command",
)
_VERSIONS = ProducingVersions(
    SchemaId("chiplog.planning", "record", 1), CodecVersion(1), CanonicalizationVersion(1)
)
_OWNER = OwnerTag("planning")


@dataclass(frozen=True)
class PlanningTrustReference:
    tenant_id: TenantId
    principal_id: PrincipalId
    contour: str
    credential_head: str
    session_head: str
    source_head: str
    trust_head: str
    materialization_head: str
    freshness_sequence: int
    peer_credential: str


@dataclass(frozen=True)
class InvocationContext:
    tenant_id: TenantId
    principal_id: PrincipalId
    permission_scope: PermissionScope
    trust_reference: PlanningTrustReference


@dataclass(frozen=True)
class CreateIntentionLine:
    command_id: RecordId
    intention_line_id: RecordId
    revision_id: RecordId
    purpose: str
    authority_act_id: str


@dataclass(frozen=True)
class PlanningTrustDecision:
    disposition: Literal["VALID", "DENIED", "STALE", "INDETERMINATE"]
    reason: str | None


class TrustRevalidationPort(Protocol):
    def revalidate(
        self, reference: PlanningTrustReference, operation: str, subject_id: RecordId
    ) -> PlanningTrustDecision: ...


class PlanningCommands(Protocol):
    def execute(
        self,
        context: InvocationContext,
        command: CreateIntentionLine,
    ) -> PlanningOutcome: ...


@dataclass(frozen=True)
class _OwnedRecord:
    record_id: RecordId
    record_type_id: str
    fields: Mapping[str, object]
    canonical_bytes: bytes
    fingerprint: str


@dataclass(frozen=True)
class PlanningCommittedResult:
    command_id: RecordId
    result_id: RecordId
    intention_line_id: RecordId
    revision_id: RecordId
    authorization_evidence_id: RecordId
    commit_sequence: int
    allocation_manifest: tuple[tuple[int, str, RecordId], ...]
    record_manifest: tuple[tuple[RecordId, str, str], ...]
    batch_fingerprint: str
    result_fingerprint: str
    publication_fingerprint: str


class _PlanningPublication(NamedTuple):
    tenant_id: TenantId
    command_id: RecordId
    request_fingerprint: str
    commit_sequence: int
    records: tuple[_OwnedRecord, ...]
    result: PlanningCommittedResult
    record_manifest: tuple[tuple[RecordId, str, str], ...]
    batch_fingerprint: str


@dataclass(frozen=True)
class PlanningOutcome:
    disposition: Literal["COMMITTED", "REPLAY", "CONFLICT", "STALE", "DENIED", "INDETERMINATE"]
    result: PlanningCommittedResult | None
    reason: str | None


# The concrete use case and its driven adapter retain these compatibility aliases.
_PlanningCommittedResult = PlanningCommittedResult
_PlanningOutcome = PlanningOutcome


class _PlanningState:
    def __init__(self, tenant_id: TenantId) -> None:
        self.tenant_id = tenant_id
        self.head = 0
        self.version = 0
        self.publications: list[_PlanningPublication] = []
        self.by_command: dict[str, _PlanningPublication] = {}
        self.record_ids: set[str] = set()


_T = TypeVar("_T")


class _PlanningRepositoryPort(Protocol):
    def transact(self, tenant_id: TenantId, body: Callable[[_PlanningState], _T]) -> _T: ...

    def committed_publications(self, tenant_id: TenantId) -> tuple[_PlanningPublication, ...]: ...


class _InMemoryPlanningRepository:
    """Deterministic lane adapter; R6 supplies the SQLite driven adapter."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, _PlanningState] = {}

    def transact(self, tenant_id: TenantId, body: Callable[[_PlanningState], _T]) -> _T:
        with self._lock:
            state = self._states.setdefault(tenant_id.value, _PlanningState(tenant_id))
            return body(state)

    def committed_publications(self, tenant_id: TenantId) -> tuple[_PlanningPublication, ...]:
        with self._lock:
            state = self._states.get(tenant_id.value)
            return () if state is None else tuple(state.publications)

    def invalidate_head(self, tenant_id: TenantId) -> None:
        """Test contour for a concurrent authoritative head advance."""
        with self._lock:
            state = self._states.setdefault(tenant_id.value, _PlanningState(tenant_id))
            state.version += 1


def _record(record_id: RecordId, record_type_id: str, fields: dict[str, object]) -> _OwnedRecord:
    namespace, name = record_type_id.rsplit(".", 1)
    canonical = canonical_record_bytes(
        record_id, RecordTypeId(namespace, name), _OWNER, _VERSIONS, fields
    )
    return _OwnedRecord(
        record_id,
        record_type_id,
        MappingProxyType({key: _immutable(value) for key, value in fields.items()}),
        canonical.payload,
        fingerprint(canonical).digest.hex(),
    )


def _immutable(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_immutable(item) for item in value)
    return value


def _rid(record_id: RecordId) -> dict[str, str]:
    return {"tenant_id": record_id.tenant_id.value, "value": record_id.value}


def _trust_fields(reference: PlanningTrustReference) -> dict[str, object]:
    return {
        "contour": reference.contour,
        "credential_head": reference.credential_head,
        "freshness_sequence": reference.freshness_sequence,
        "materialization_head": reference.materialization_head,
        "principal_id": reference.principal_id.value,
        "session_head": reference.session_head,
        "source_head": reference.source_head,
        "tenant_id": reference.tenant_id.value,
        "trust_head": reference.trust_head,
        "peer_credential": reference.peer_credential,
    }


def _request_fingerprint(
    context: InvocationContext, command: CreateIntentionLine, predecessor: RecordId | None
) -> str:
    value = {
        "command": {
            "authority_act_id": command.authority_act_id,
            "command_id": _rid(command.command_id),
            "intention_line_id": _rid(command.intention_line_id),
            "purpose": command.purpose,
            "revision_id": _rid(command.revision_id),
        },
        "context": {
            "permission_scope": context.permission_scope.value,
            "principal_id": context.principal_id.value,
            "tenant_id": context.tenant_id.value,
            "trust_reference": _trust_fields(context.trust_reference),
        },
        "operation": CREATE_INTENTION_LINE,
        "predecessor_result_id": None if predecessor is None else _rid(predecessor),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _batch_fingerprint(manifest: tuple[tuple[RecordId, str, str], ...]) -> str:
    encoded = json.dumps(
        [
            {
                "fingerprint": item_fingerprint,
                "record_id": _rid(record_id),
                "record_type_id": record_type_id,
            }
            for record_id, record_type_id, item_fingerprint in manifest
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _publication_fingerprint(
    request_fingerprint: str,
    commit_sequence: int,
    manifest: tuple[tuple[RecordId, str, str], ...],
) -> str:
    encoded = json.dumps(
        {
            "commit_sequence": commit_sequence,
            "record_manifest": [
                {
                    "fingerprint": item_fingerprint,
                    "record_id": _rid(record_id),
                    "record_type_id": record_type_id,
                }
                for record_id, record_type_id, item_fingerprint in manifest
            ],
            "request_fingerprint": request_fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _structural_error(
    context: InvocationContext, command: CreateIntentionLine, predecessor: RecordId | None
) -> str | None:
    tenant = context.tenant_id
    ids = (command.command_id, command.intention_line_id, command.revision_id)
    if any(item.tenant_id != tenant for item in ids):
        return "foreign tenant identifier"
    if len({item.value for item in ids}) != len(ids):
        return "planning identifiers must be distinct"
    if context.trust_reference.tenant_id != tenant:
        return "trust tenant mismatch"
    if context.trust_reference.principal_id != context.principal_id:
        return "trust principal mismatch"
    if context.permission_scope.value != CREATE_SCOPE:
        return "forbidden actor-operation-authority tuple"
    if context.trust_reference.contour not in DIRECT_CONTOURS:
        return "wrong direct-principal source contour"
    if predecessor is not None:
        return "CREATE_INTENTION_LINE forbids a predecessor"
    if not command.purpose or command.purpose != command.purpose.strip():
        return "purpose must be nonblank normalized text"
    if not command.authority_act_id.strip():
        return "authority act is required"
    heads = (
        context.trust_reference.credential_head,
        context.trust_reference.session_head,
        context.trust_reference.source_head,
        context.trust_reference.trust_head,
        context.trust_reference.materialization_head,
    )
    if any(not item for item in heads):
        return "complete authority heads are required"
    return None


class _PlanningOwnerFactory:
    def construct(
        self,
        context: InvocationContext,
        command: CreateIntentionLine,
        commit_sequence: int,
        request_fingerprint: str,
    ) -> tuple[tuple[_OwnedRecord, ...], _PlanningCommittedResult]:
        tenant = context.tenant_id
        evidence_id = RecordId(tenant, f"{command.command_id.value}.authorization")
        result_id = RecordId(tenant, f"{command.command_id.value}.result")
        allocation_manifest = (
            (0, "chiplog.planning.intention_line", command.intention_line_id),
            (1, "chiplog.planning.intention_line_revision", command.revision_id),
        )
        allocation_fields = [
            {"ordinal": ordinal, "record_id": _rid(item), "record_type_id": kind}
            for ordinal, kind, item in allocation_manifest
        ]
        common = {
            "authority_act_id": command.authority_act_id,
            "principal_id": context.principal_id.value,
            "tenant_id": tenant.value,
        }
        command_record = _record(
            command.command_id,
            "chiplog.planning.planning_command",
            {
                **common,
                "allocation_manifest": allocation_fields,
                "operation": CREATE_INTENTION_LINE,
                "permission_scope": context.permission_scope.value,
                "trust_reference": _trust_fields(context.trust_reference),
            },
        )
        evidence_record = _record(
            evidence_id,
            "chiplog.planning.authorization_evidence",
            {
                **common,
                "operation": CREATE_INTENTION_LINE,
                "trust_reference": _trust_fields(context.trust_reference),
            },
        )
        line_record = _record(
            command.intention_line_id,
            "chiplog.planning.intention_line",
            {**common, "initial_revision_id": _rid(command.revision_id)},
        )
        revision_record = _record(
            command.revision_id,
            "chiplog.planning.intention_line_revision",
            {
                **common,
                "activity": "ACTIVE",
                "intention_line_id": _rid(command.intention_line_id),
                "ordinal": 0,
                "personal_outcome": "OPEN",
                "purpose": command.purpose,
                "predecessor_revision_id": None,
            },
        )
        committed_records = (
            command_record,
            evidence_record,
            line_record,
            revision_record,
        )
        committed_manifest = tuple(
            (item.record_id, item.record_type_id, item.fingerprint) for item in committed_records
        )
        result_record = _record(
            result_id,
            "chiplog.planning.committed_result",
            {
                "allocation_manifest": allocation_fields,
                "authorization_evidence_id": _rid(evidence_id),
                "command_id": _rid(command.command_id),
                "commit_sequence": commit_sequence,
                "batch_fingerprint": _batch_fingerprint(committed_manifest),
                "record_manifest": [
                    {
                        "fingerprint": item_fingerprint,
                        "record_id": _rid(record_id),
                        "record_type_id": record_type_id,
                    }
                    for record_id, record_type_id, item_fingerprint in committed_manifest
                ],
                "tenant_id": tenant.value,
            },
        )
        records = (*committed_records, result_record)
        record_manifest = tuple(
            (item.record_id, item.record_type_id, item.fingerprint) for item in records
        )
        result = _PlanningCommittedResult(
            command.command_id,
            result_id,
            command.intention_line_id,
            command.revision_id,
            evidence_id,
            commit_sequence,
            allocation_manifest,
            record_manifest,
            _batch_fingerprint(record_manifest),
            result_record.fingerprint,
            _publication_fingerprint(request_fingerprint, commit_sequence, record_manifest),
        )
        return records, result


class _PlanningUseCase:
    def __init__(
        self,
        repository: _PlanningRepositoryPort,
        trust_revalidation: TrustRevalidationPort,
        owner_factory: _PlanningOwnerFactory | None = None,
    ) -> None:
        self._repository = repository
        self._trust_revalidation = trust_revalidation
        self._owner_factory = owner_factory or _PlanningOwnerFactory()

    def execute(
        self,
        context: InvocationContext,
        command: CreateIntentionLine,
    ) -> _PlanningOutcome:
        structural_error = _structural_error(context, command, None)
        if structural_error is not None:
            return _PlanningOutcome("DENIED", None, structural_error)
        request_fingerprint = _request_fingerprint(context, command, None)

        def commit(state: _PlanningState) -> _PlanningOutcome:
            prior = state.by_command.get(command.command_id.value)
            if prior is not None:
                if prior.request_fingerprint != request_fingerprint:
                    return _PlanningOutcome(
                        "CONFLICT", None, "command identity reused with changed bytes"
                    )
                replay_decision = self._trust_revalidation.revalidate(
                    context.trust_reference,
                    CREATE_INTENTION_LINE,
                    command.intention_line_id,
                )
                if replay_decision.disposition != "VALID":
                    disposition = (
                        "DENIED"
                        if replay_decision.disposition == "DENIED"
                        else replay_decision.disposition
                    )
                    return _PlanningOutcome(disposition, None, replay_decision.reason)
                return _PlanningOutcome("REPLAY", prior.result, None)
            observed_head = state.head
            observed_version = state.version
            decision = self._trust_revalidation.revalidate(
                context.trust_reference, CREATE_INTENTION_LINE, command.intention_line_id
            )
            if decision.disposition != "VALID":
                disposition = "DENIED" if decision.disposition == "DENIED" else decision.disposition
                return _PlanningOutcome(disposition, None, decision.reason)
            if state.head != observed_head or state.version != observed_version:
                return _PlanningOutcome("STALE", None, "planning head changed during revalidation")
            records, result = self._owner_factory.construct(
                context, command, observed_head + 1, request_fingerprint
            )
            record_values = {item.record_id.value for item in records}
            if len(record_values) != len(records) or record_values & state.record_ids:
                return _PlanningOutcome("CONFLICT", None, "record identity collision")
            publication = _PlanningPublication(
                state.tenant_id,
                command.command_id,
                request_fingerprint,
                observed_head + 1,
                records,
                result,
                result.record_manifest,
                result.batch_fingerprint,
            )
            state.publications.append(publication)
            state.by_command[command.command_id.value] = publication
            state.record_ids.update(record_values)
            state.head = observed_head + 1
            state.version = observed_version + 1
            return _PlanningOutcome("COMMITTED", result, None)

        return self._repository.transact(context.tenant_id, commit)
