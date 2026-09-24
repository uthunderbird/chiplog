"""Owner-local mirrors of the closed broker diagnostic-source wire rows.

The rows are retained evidence inputs.  Decoding them checks their representation;
it neither authenticates a broker read nor executes a classifier.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING, Literal, cast

from pydantic import ConfigDict, Field, ValidationError, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .execution_recovery_observations import ExecutionRecoveryCut, ExecutionRecoveryDTO
from .recovery_contracts import Digest, Identity, Present, UInt64

if TYPE_CHECKING:
    from .terminal_recovery_fault_contracts import PrepareTerminalRecoveryFaultV1


FAULT_CAPTURE_SCHEMA: Literal["chiplog.broker.fault-capture.v1"] = "chiplog.broker.fault-capture.v1"
FAULT_OBSERVATION_SCHEMA: Literal["chiplog.broker.fault-observation.v1"] = (
    "chiplog.broker.fault-observation.v1"
)
FAULT_OBSERVER_REGISTRY_SCHEMA: Literal["chiplog.broker.fault-observer-registry.v1"] = (
    "chiplog.broker.fault-observer-registry.v1"
)
FAULT_ISSUANCE_SCHEMA: Literal["chiplog.broker.fault-diagnostic-issuance.v1"] = (
    "chiplog.broker.fault-diagnostic-issuance.v1"
)
FAULT_OPERATION: Literal["recovery.publish_terminal_fault"] = "recovery.publish_terminal_fault"
DIAGNOSTIC_CUT_SCHEMA: Literal["chiplog.execution.recovery-fault-diagnostic-cut.v1"] = (
    "chiplog.execution.recovery-fault-diagnostic-cut.v1"
)
FAULT_PROVENANCE_SCHEMA: Literal["chiplog.execution.fault-classification-provenance.v1"] = (
    "chiplog.execution.fault-classification-provenance.v1"
)


class FaultObserverSourceIntegrityError(ValueError):
    """A retained broker row is not the exact source selected by an owner request."""


class FaultObserverSourceDTO(ExecutionRecoveryDTO):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    def canonical_bytes(self) -> bytes:
        return _canonical(self.model_dump(mode="json"))


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class FaultBrokerSourceRefV1(FaultObserverSourceDTO):
    """Owner-local inert reference to immutable broker diagnostic content."""

    source_owner: Literal["broker"] = "broker"
    source_kind: Literal[
        "FAULT_CAPTURE", "FAULT_OBSERVATION", "FAULT_OBSERVER_REGISTRY", "FAULT_ISSUANCE"
    ]
    schema_id: Identity
    source_id: Identity
    fingerprint: Digest


class ExactRecordHead(FaultObserverSourceDTO):
    owner: Literal[
        "agent_loop", "effects", "planning", "broker_ingress", "broker_dispatch", "conversation"
    ]
    record_kind: Identity
    subject_id: Identity
    record_id: Identity
    fingerprint: Digest


class BrokerSelectedSourceDecisionV1(FaultObserverSourceDTO):
    source: FaultBrokerSourceRefV1
    selection_id: Identity
    selection_fingerprint: Digest


type SelectedSourceDecisionV1 = ExactRecordHead | BrokerSelectedSourceDecisionV1


class FaultCaptureSubjectV1(FaultObserverSourceDTO):
    schema_id: Literal["chiplog.broker.fault-capture-subject.v1"] = (
        "chiplog.broker.fault-capture-subject.v1"
    )
    tenant_id: Identity
    database_id: Identity
    role: Identity
    ordinal: UInt64
    observed_owner: Identity
    observed_record_kind: Identity
    declared_schema_id: Identity
    logical_subject_id: Identity
    physical_record_id: Identity
    selected_source_decision: SelectedSourceDecisionV1
    claimed_fingerprint: Digest
    observed_bytes: bytes
    observed_bytes_fingerprint: Digest
    reader_contract: Identity
    reader_version: Identity
    authority_snapshot_id: Identity
    authority_snapshot_fingerprint: Digest
    observed_tenant_frontier: UInt64
    observed_materialization_commitment: Digest

    @model_validator(mode="after")
    def exact_observed_bytes_fingerprint(self) -> FaultCaptureSubjectV1:
        if _digest(self.observed_bytes) != self.observed_bytes_fingerprint:
            raise ValueError("observed bytes fingerprint differs from retained bytes")
        return self


class FaultCaptureRecordV1(FaultObserverSourceDTO):
    schema_id: Literal["chiplog.broker.fault-capture.v1"] = FAULT_CAPTURE_SCHEMA
    subject: FaultCaptureSubjectV1
    capture_command_id: Identity
    originating_broker_epoch: Identity
    originating_broker_session: Identity
    runtime_generation: Identity


class FaultObserverReaderV1(FaultObserverSourceDTO):
    reader_id: Identity
    version: Identity


class FaultObserverRegistryV1(FaultObserverSourceDTO):
    schema_id: Literal["chiplog.broker.fault-observer-registry.v1"] = FAULT_OBSERVER_REGISTRY_SCHEMA
    registry_id: Identity
    version: Identity
    permitted_operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    permitted_owner: Literal["agent_loop"] = "agent_loop"
    permitted_record_kind: Literal["TERMINAL_RECOVERY_FAULT"] = "TERMINAL_RECOVERY_FAULT"
    permitted_record_schema: Literal["chiplog.execution.terminal-recovery-fault.v1"] = (
        "chiplog.execution.terminal-recovery-fault.v1"
    )
    max_output_records: Literal[1] = 1
    observer_id: Identity
    observer_contract_version: Identity
    allowed_reader_contracts: tuple[FaultObserverReaderV1, ...] = Field(min_length=1)
    fault_rule_registry_reference: CallSubjectHead
    fault_rule_registry_selection: CallSubjectHead
    issuer_authority: Identity

    @model_validator(mode="after")
    def ordered_unique_readers(self) -> FaultObserverRegistryV1:
        readers = tuple((row.reader_id, row.version) for row in self.allowed_reader_contracts)
        if readers != tuple(sorted(readers)) or len(set(readers)) != len(readers):
            raise ValueError("observer reader contracts must be uniquely lexical ordered")
        return self


class FaultObservationRecordV1(FaultObserverSourceDTO):
    schema_id: Literal["chiplog.broker.fault-observation.v1"] = FAULT_OBSERVATION_SCHEMA
    tenant_id: Identity
    database_id: Identity
    original_run: ExactRecordHead
    ordered_capture_refs: tuple[FaultBrokerSourceRefV1, ...] = Field(min_length=1)
    ordered_capture_inventory_fingerprint: Digest
    observer_registry: FaultBrokerSourceRefV1
    fault_rule_registry_reference: CallSubjectHead
    fault_rule_registry_selection: CallSubjectHead
    authority_snapshot_id: Identity
    authority_snapshot_fingerprint: Digest
    tenant_frontier: UInt64
    materialization_commitment: Digest
    broker_epoch: Identity
    broker_session: Identity
    runtime_generation: Identity

    @model_validator(mode="after")
    def fixed_observation_refs(self) -> FaultObservationRecordV1:
        if self.original_run.owner != "agent_loop" or self.original_run.record_kind != "Run":
            raise ValueError("fault observation original Run is not an agent_loop Run")
        if any(ref.source_kind != "FAULT_CAPTURE" for ref in self.ordered_capture_refs):
            raise ValueError("fault observation includes a non-capture source")
        if self.observer_registry.source_kind != "FAULT_OBSERVER_REGISTRY":
            raise ValueError("fault observation registry is not an observer registry")
        return self


class FaultDiagnosticSubjectV1(FaultObserverSourceDTO):
    schema_id: Literal["chiplog.broker.fault-diagnostic-subject.v1"] = (
        "chiplog.broker.fault-diagnostic-subject.v1"
    )
    operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    request_schema: Literal["chiplog.execution.prepare-terminal-recovery-fault.v1"] = (
        "chiplog.execution.prepare-terminal-recovery-fault.v1"
    )
    tenant_id: Identity
    database_id: Identity
    command_id: Identity
    original_run: ExactRecordHead
    diagnostic_cut_schema: Literal["chiplog.execution.recovery-fault-diagnostic-cut.v1"] = (
        DIAGNOSTIC_CUT_SCHEMA
    )
    canonical_diagnostic_cut_bytes: bytes = Field(min_length=1)
    provenance_schema: Literal["chiplog.execution.fault-classification-provenance.v1"] = (
        FAULT_PROVENANCE_SCHEMA
    )
    canonical_provenance_bytes: bytes = Field(min_length=1)
    observer_registry: FaultBrokerSourceRefV1
    diagnostic_observation: FaultBrokerSourceRefV1
    source_capture_inventory_fingerprint: Digest
    fault_rule_registry_reference: CallSubjectHead
    fault_rule_registry_selection: CallSubjectHead
    broker_epoch: Identity
    broker_session: Identity
    runtime_generation: Identity

    @model_validator(mode="after")
    def no_untyped_source_roles(self) -> FaultDiagnosticSubjectV1:
        if self.original_run.owner != "agent_loop" or self.original_run.record_kind != "Run":
            raise ValueError("diagnostic subject original Run is not an agent_loop Run")
        if (
            self.observer_registry.source_kind != "FAULT_OBSERVER_REGISTRY"
            or self.diagnostic_observation.source_kind != "FAULT_OBSERVATION"
        ):
            raise ValueError("diagnostic subject source roles differ from fixed roles")
        return self


class FaultDiagnosticIssuanceRecordV1(FaultObserverSourceDTO):
    schema_id: Literal["chiplog.broker.fault-diagnostic-issuance.v1"] = FAULT_ISSUANCE_SCHEMA
    subject: FaultDiagnosticSubjectV1
    observation_subject_fingerprint: Digest
    issuance_id: Identity
    issuer_authority: Identity
    observer_contract_version: Identity

    @model_validator(mode="after")
    def exact_subject_fingerprint(self) -> FaultDiagnosticIssuanceRecordV1:
        if self.observation_subject_fingerprint != _digest(self.subject.canonical_bytes()):
            raise ValueError("issuance observation subject fingerprint differs from subject bytes")
        return self


type BrokerDiagnosticBodyV1 = (
    FaultCaptureRecordV1
    | FaultObserverRegistryV1
    | FaultObservationRecordV1
    | FaultDiagnosticIssuanceRecordV1
)
type BrokerDiagnosticSourceKindV1 = Literal[
    "FAULT_CAPTURE", "FAULT_OBSERVATION", "FAULT_OBSERVER_REGISTRY", "FAULT_ISSUANCE"
]


def diagnostic_source_ref(body: BrokerDiagnosticBodyV1) -> FaultBrokerSourceRefV1:
    if isinstance(body, FaultCaptureRecordV1):
        kind: BrokerDiagnosticSourceKindV1 = "FAULT_CAPTURE"
    elif isinstance(body, FaultObserverRegistryV1):
        kind = "FAULT_OBSERVER_REGISTRY"
    elif isinstance(body, FaultObservationRecordV1):
        kind = "FAULT_OBSERVATION"
    elif isinstance(body, FaultDiagnosticIssuanceRecordV1):
        kind = "FAULT_ISSUANCE"
    else:  # pragma: no cover - closed public union
        raise TypeError("unknown broker diagnostic body")
    raw = body.canonical_bytes()
    digest = _digest(raw)
    return FaultBrokerSourceRefV1(
        source_kind=kind,
        schema_id=body.schema_id,
        source_id=body.schema_id + ":" + digest,
        fingerprint=digest,
    )


def decode_fault_capture(canonical_bytes: bytes) -> FaultCaptureRecordV1:
    return cast(FaultCaptureRecordV1, _decode_diagnostic_source("FAULT_CAPTURE", canonical_bytes))


def decode_fault_observer_registry(canonical_bytes: bytes) -> FaultObserverRegistryV1:
    return cast(
        FaultObserverRegistryV1,
        _decode_diagnostic_source("FAULT_OBSERVER_REGISTRY", canonical_bytes),
    )


def decode_fault_observation(canonical_bytes: bytes) -> FaultObservationRecordV1:
    return cast(
        FaultObservationRecordV1, _decode_diagnostic_source("FAULT_OBSERVATION", canonical_bytes)
    )


def decode_fault_issuance(canonical_bytes: bytes) -> FaultDiagnosticIssuanceRecordV1:
    return cast(
        FaultDiagnosticIssuanceRecordV1,
        _decode_diagnostic_source("FAULT_ISSUANCE", canonical_bytes),
    )


def _decode_diagnostic_source(
    kind: BrokerDiagnosticSourceKindV1, raw: bytes
) -> BrokerDiagnosticBodyV1:
    body_type: type[FaultObserverSourceDTO]
    schema: str
    if kind == "FAULT_CAPTURE":
        body_type, schema = FaultCaptureRecordV1, FAULT_CAPTURE_SCHEMA
    elif kind == "FAULT_OBSERVER_REGISTRY":
        body_type, schema = FaultObserverRegistryV1, FAULT_OBSERVER_REGISTRY_SCHEMA
    elif kind == "FAULT_OBSERVATION":
        body_type, schema = FaultObservationRecordV1, FAULT_OBSERVATION_SCHEMA
    else:
        body_type, schema = FaultDiagnosticIssuanceRecordV1, FAULT_ISSUANCE_SCHEMA
    try:
        decoded = body_type.model_validate_json(raw)
    except ValidationError as error:
        raise FaultObserverSourceIntegrityError("invalid broker diagnostic source bytes") from error
    if decoded.schema_id != schema or decoded.canonical_bytes() != raw:
        raise FaultObserverSourceIntegrityError(
            "broker diagnostic source bytes are not canonical fixed-row bytes"
        )
    return decoded


class FaultRetainedBrokerSourceV1(FaultObserverSourceDTO):
    reference: FaultBrokerSourceRefV1
    canonical_source_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_fixed_source(self) -> FaultRetainedBrokerSourceV1:
        decoded = _decode_diagnostic_source(self.reference.source_kind, self.canonical_source_bytes)
        if diagnostic_source_ref(decoded) != self.reference:
            raise ValueError("broker diagnostic source reference differs from fixed bytes")
        return self


class FaultObserverRetainedInputsV1(FaultObserverSourceDTO):
    observer_registry: FaultRetainedBrokerSourceV1
    observation: FaultRetainedBrokerSourceV1
    issuance: FaultRetainedBrokerSourceV1
    ordered_captures: tuple[FaultRetainedBrokerSourceV1, ...] = Field(min_length=1)


def _ref_tuple(value: object) -> tuple[object, ...]:
    fields = ("source_owner", "source_kind", "schema_id", "source_id", "fingerprint")
    return tuple(getattr(value, field) for field in fields)


def _head_tuple(value: object) -> tuple[object, ...]:
    fields = ("owner", "record_kind", "subject_id", "record_id", "fingerprint")
    return tuple(getattr(value, field) for field in fields)


def _same_ref(left: object, right: object) -> bool:
    return _ref_tuple(left) == _ref_tuple(right)


def _capture_inventory_fingerprint(captures: tuple[FaultRetainedBrokerSourceV1, ...]) -> str:
    # This is the broker's independent retained-capture domain, not the owner cut inventory.
    preimage = _canonical(
        {
            "schema_id": "chiplog.broker.fault-capture-inventory.v1",
            "ordered_captures": [
                {
                    "reference": item.reference.model_dump(mode="json"),
                    "canonical_source_bytes": base64.b64encode(
                        item.canonical_source_bytes
                    ).decode(),
                }
                for item in captures
            ],
        }
    )
    return _digest(preimage)


def _project_selected_decision(decision: SelectedSourceDecisionV1) -> CallSubjectHead:
    if isinstance(decision, ExactRecordHead):
        return CallSubjectHead(
            subject_id=decision.subject_id,
            revision=Present(head=decision.record_id, fingerprint=decision.fingerprint),
        )
    return CallSubjectHead(
        subject_id=decision.source.source_id,
        revision=Present(head=decision.selection_id, fingerprint=decision.selection_fingerprint),
    )


def _exact_run_head(head: CallSubjectHead) -> tuple[object, ...]:
    return ("agent_loop", "Run", head.subject_id, head.revision.head, head.revision.fingerprint)


def _raise(message: str) -> None:
    raise FaultObserverSourceIntegrityError(message)


def validate_fault_observer_sources(
    request: PrepareTerminalRecoveryFaultV1, retained: FaultObserverRetainedInputsV1
) -> None:
    """Join the retained broker bytes to one exact observer-authorized owner request."""
    # A local import keeps the leaf -> mirror integration acyclic.
    from .terminal_recovery_fault_contracts import (
        DirectDiagnosticProvenanceV1,
        FaultRecoveryRecordIntegrityError,
        PrepareTerminalRecoveryFaultV1,
        RegisteredRecoveryFaultObserverAuthorityV1,
        UndecodableRecoveryFaultFrontierV1,
        decode_fault_classification_provenance,
        decode_recovery_fault_diagnostic_cut,
    )

    try:
        request = PrepareTerminalRecoveryFaultV1.model_validate_json(request.canonical_bytes())
    except ValidationError as error:
        raise FaultObserverSourceIntegrityError(
            "terminal fault request is not canonical"
        ) from error
    try:
        retained = FaultObserverRetainedInputsV1.model_validate_json(retained.canonical_bytes())
    except ValidationError as error:
        raise FaultObserverSourceIntegrityError(
            "retained observer sources are not canonical fixed rows"
        ) from error
    if not isinstance(request.authority, RegisteredRecoveryFaultObserverAuthorityV1):
        _raise("observer retained sources require registered observer authority")
    authority = cast(RegisteredRecoveryFaultObserverAuthorityV1, request.authority)
    registry = decode_fault_observer_registry(retained.observer_registry.canonical_source_bytes)
    observation = decode_fault_observation(retained.observation.canonical_source_bytes)
    issuance = decode_fault_issuance(retained.issuance.canonical_source_bytes)
    captures = tuple(
        decode_fault_capture(item.canonical_source_bytes) for item in retained.ordered_captures
    )

    if (
        retained.observer_registry.reference.source_kind != "FAULT_OBSERVER_REGISTRY"
        or retained.observation.reference.source_kind != "FAULT_OBSERVATION"
        or retained.issuance.reference.source_kind != "FAULT_ISSUANCE"
    ):
        _raise("retained observer source roles differ from fixed roles")
    if (
        not _same_ref(retained.observer_registry.reference, authority.observer_registry)
        or not _same_ref(retained.observation.reference, authority.diagnostic_observation)
        or not _same_ref(retained.issuance.reference, authority.issuance)
    ):
        _raise("retained observer sources differ from request authority")
    if (
        registry.fault_rule_registry_reference != request.cut.fault_rule_registry
        or registry.fault_rule_registry_selection != request.cut.fault_rule_registry_selection
        or observation.fault_rule_registry_reference != request.cut.fault_rule_registry
        or observation.fault_rule_registry_selection != request.cut.fault_rule_registry_selection
    ):
        _raise("retained observer rules differ from request cut")
    if (
        observation.tenant_id != request.cut.tenant_id
        or observation.database_id != request.cut.database_id
        or observation.tenant_frontier != request.cut.tenant_commit_sequence
        or observation.materialization_commitment != request.cut.materialization_commitment
        or _head_tuple(observation.original_run) != _exact_run_head(request.cut.current_run)
        or not _same_ref(observation.observer_registry, retained.observer_registry.reference)
    ):
        _raise("retained observation differs from request cut")
    capture_refs = tuple(item.reference for item in retained.ordered_captures)
    if (
        len(capture_refs) != len(set(_ref_tuple(ref) for ref in capture_refs))
        or tuple(_ref_tuple(ref) for ref in capture_refs)
        != tuple(_ref_tuple(ref) for ref in observation.ordered_capture_refs)
        or len(captures) != len(request.cut.ordered_observations)
    ):
        _raise("retained capture inventory differs from observation or request")
    allowed_readers = {(row.reader_id, row.version) for row in registry.allowed_reader_contracts}
    for index, (retained_capture, capture, member) in enumerate(
        zip(retained.ordered_captures, captures, request.cut.ordered_observations, strict=True)
    ):
        capture_subject = capture.subject
        if (
            retained_capture.reference.source_kind != "FAULT_CAPTURE"
            or capture_subject.ordinal != index
            or member.ordinal != index
            or capture_subject.role != member.role
            or capture_subject.observed_owner != member.owner
            or capture_subject.observed_record_kind != member.record_kind
            or capture_subject.declared_schema_id != member.declared_schema_id
            or capture_subject.logical_subject_id != member.logical_subject_id
            or capture_subject.physical_record_id != member.physical_record_id
            or capture_subject.claimed_fingerprint != member.claimed_fingerprint
            or capture_subject.observed_bytes != member.observed_bytes
            or capture_subject.observed_bytes_fingerprint != member.observed_bytes_fingerprint
            or not _same_ref(retained_capture.reference, member.capture)
            or _project_selected_decision(capture_subject.selected_source_decision)
            != member.selected_decision
        ):
            _raise("retained capture does not project to its request observation slot")
        if (
            capture_subject.tenant_id != observation.tenant_id
            or capture_subject.database_id != observation.database_id
            or capture_subject.authority_snapshot_id != observation.authority_snapshot_id
            or capture_subject.authority_snapshot_fingerprint
            != observation.authority_snapshot_fingerprint
            or capture_subject.observed_tenant_frontier != observation.tenant_frontier
            or capture_subject.observed_materialization_commitment
            != observation.materialization_commitment
            or capture.originating_broker_epoch != observation.broker_epoch
            or capture.originating_broker_session != observation.broker_session
            or capture.runtime_generation != observation.runtime_generation
            or (capture_subject.reader_contract, capture_subject.reader_version)
            not in allowed_readers
        ):
            _raise("retained capture does not join its observation or reader registry")
    inventory = _capture_inventory_fingerprint(retained.ordered_captures)
    issuance_subject = issuance.subject
    if (
        inventory != observation.ordered_capture_inventory_fingerprint
        or inventory != issuance_subject.source_capture_inventory_fingerprint
        or issuance_subject.tenant_id != request.cut.tenant_id
        or issuance_subject.database_id != request.cut.database_id
        or issuance_subject.command_id != request.command_id
        or _head_tuple(issuance_subject.original_run) != _exact_run_head(request.cut.current_run)
        or not _same_ref(issuance_subject.observer_registry, retained.observer_registry.reference)
        or not _same_ref(issuance_subject.diagnostic_observation, retained.observation.reference)
        or issuance_subject.fault_rule_registry_reference != request.cut.fault_rule_registry
        or issuance_subject.fault_rule_registry_selection
        != request.cut.fault_rule_registry_selection
        or issuance_subject.broker_epoch != observation.broker_epoch
        or issuance_subject.broker_session != observation.broker_session
        or issuance_subject.runtime_generation != observation.runtime_generation
        or issuance.observation_subject_fingerprint != authority.observation_subject_fingerprint
        or issuance.issuer_authority != registry.issuer_authority
        or issuance.observer_contract_version != registry.observer_contract_version
    ):
        _raise("retained issuance does not join request, observation, or registry")
    try:
        decoded_cut = decode_recovery_fault_diagnostic_cut(
            issuance_subject.canonical_diagnostic_cut_bytes
        )
        decoded_provenance = decode_fault_classification_provenance(
            issuance_subject.canonical_provenance_bytes
        )
    except FaultRecoveryRecordIntegrityError as error:
        raise FaultObserverSourceIntegrityError(
            "issuance owner payload is not canonical"
        ) from error
    if (
        decoded_cut.canonical_bytes() != request.cut.canonical_bytes()
        or decoded_provenance.canonical_bytes() != request.provenance.canonical_bytes()
    ):
        _raise("issuance owner payload differs from terminal fault request")
    frontier = request.cut.frontier_observation
    if isinstance(frontier, UndecodableRecoveryFaultFrontierV1):
        matches = [
            item
            for item, capture in zip(request.cut.ordered_observations, captures, strict=True)
            if _same_ref(item.capture, frontier.capture)
            and item.observed_bytes == frontier.raw_bytes
            and capture.subject.declared_schema_id == frontier.expected_schema_id
        ]
        if frontier.expected_schema_id != "chiplog.execution.recovery-cut.v1" or len(matches) != 1:
            _raise("undecodable frontier is not an exact captured recovery cut slot")
        try:
            candidate = ExecutionRecoveryCut.model_validate_json(frontier.raw_bytes)
            if candidate.canonical_bytes() == frontier.raw_bytes:
                _raise("parseable recovery cut is mislabeled undecodable")
        except ValidationError:
            pass
    if isinstance(request.provenance, DirectDiagnosticProvenanceV1):
        matches = [
            item
            for item in request.cut.ordered_observations
            if _same_ref(item.capture, request.provenance.trigger_capture)
        ]
        if len(matches) != 1:
            _raise("direct diagnostic trigger is not an exact captured slot")
        if request.provenance.reason == "RECOVERY_INPUT_UNDECODABLE" and (
            not isinstance(frontier, UndecodableRecoveryFaultFrontierV1)
            or not _same_ref(frontier.capture, request.provenance.trigger_capture)
        ):
            _raise("undecodable direct diagnostic trigger differs from frontier capture")
