"""Closed Phase-C wire contracts for broker recovery-fault diagnostics.

These are retained representations and pure joins only.  They neither read a
source nor issue a proof, and constructing them grants no publication authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    EXECUTION_RUN_RECORD_ROWS,
    ExecutionRunCanonicalMember,
    ExecutionRunRecordIntegrityError,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, Present, UInt64
from chiplog.capabilities.agent_loop.recovery_fault_rule_contracts import (
    SelectedFaultRuleRegistryV1,
)
from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    FAULT_CLASSIFICATION_PROVENANCE_SCHEMA,
    RECOVERY_FAULT_DIAGNOSTIC_CUT_SCHEMA,
    PreparedTerminalRecoveryFaultV1,
    PrepareTerminalRecoveryFaultV1,
    RegisteredRecoveryFaultObserverAuthorityV1,
    TerminalRecoveryFaultCanonicalMemberV1,
    decode_fault_classification_provenance,
    decode_recovery_fault_diagnostic_cut,
    decode_terminal_recovery_fault_member,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerRecoveryDiagnosticAuthenticationV1,
    ExactRecordHead,
    InvocationProofRef,
    JournalSelectedPublication,
    ObservedAbsence,
    OwnerCommandBytes,
    OwnerRecordBytes,
)
from chiplog.platform.recovery_diagnostic_refs import BrokerDiagnosticSourceRefV1
from chiplog.platform.runtime_surface_contracts import RuntimeSurfaceCut

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
DIAGNOSTIC_CUT_SCHEMA: Literal["chiplog.execution.recovery-fault-diagnostic-cut.v1"] = cast(
    Literal["chiplog.execution.recovery-fault-diagnostic-cut.v1"],
    RECOVERY_FAULT_DIAGNOSTIC_CUT_SCHEMA,
)
FAULT_PROVENANCE_SCHEMA: Literal["chiplog.execution.fault-classification-provenance.v1"] = cast(
    Literal["chiplog.execution.fault-classification-provenance.v1"],
    FAULT_CLASSIFICATION_PROVENANCE_SCHEMA,
)
TERMINAL_FAULT_RESULT_SCHEMA: Literal["chiplog.execution.terminal-recovery-fault-result.v1"] = (
    "chiplog.execution.terminal-recovery-fault-result.v1"
)
FAULT_OPERATION: Literal["recovery.publish_terminal_fault"] = "recovery.publish_terminal_fault"


class RecoveryDiagnosticDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    def canonical_bytes(self) -> bytes:
        return _canonical(self.model_dump(mode="json"))


def _canonical(value: object) -> bytes:
    """The one closed JSON format used by broker diagnostic source bodies."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class BrokerSelectedSourceDecisionV1(RecoveryDiagnosticDTO):
    source: BrokerDiagnosticSourceRefV1
    selection_id: Identity
    selection_fingerprint: Digest


type SelectedSourceDecisionV1 = ExactRecordHead | BrokerSelectedSourceDecisionV1


class FaultCaptureSubjectV1(RecoveryDiagnosticDTO):
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


class CaptureFaultSourceRequestV1(RecoveryDiagnosticDTO):
    schema_id: Literal["chiplog.broker.capture-fault-source.v1"] = (
        "chiplog.broker.capture-fault-source.v1"
    )
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    reader_contract: Identity
    reader_version: Identity
    selected_source_decision: SelectedSourceDecisionV1
    expected_authority_snapshot_id: Identity
    expected_authority_snapshot_fingerprint: Digest
    role: Identity
    ordinal: UInt64


class FaultCaptureRecordV1(RecoveryDiagnosticDTO):
    schema_id: Literal["chiplog.broker.fault-capture.v1"] = FAULT_CAPTURE_SCHEMA
    subject: FaultCaptureSubjectV1
    capture_command_id: Identity
    originating_broker_epoch: Identity
    originating_broker_session: Identity
    runtime_generation: Identity


class FaultCaptureSelectedV1(RecoveryDiagnosticDTO):
    kind: Literal["CAPTURED"] = "CAPTURED"
    retained: RetainedBrokerDiagnosticSourceV1

    @model_validator(mode="after")
    def capture_only(self) -> FaultCaptureSelectedV1:
        if self.retained.reference.source_kind != "FAULT_CAPTURE":
            raise ValueError("selected capture retains a non-capture source")
        return self


class FaultObserverReaderV1(RecoveryDiagnosticDTO):
    reader_id: Identity
    version: Identity


class FaultObserverRegistryV1(RecoveryDiagnosticDTO):
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


class FaultObservationRecordV1(RecoveryDiagnosticDTO):
    schema_id: Literal["chiplog.broker.fault-observation.v1"] = FAULT_OBSERVATION_SCHEMA
    tenant_id: Identity
    database_id: Identity
    original_run: ExactRecordHead
    ordered_capture_refs: tuple[BrokerDiagnosticSourceRefV1, ...] = Field(min_length=1)
    ordered_capture_inventory_fingerprint: Digest
    observer_registry: BrokerDiagnosticSourceRefV1
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


class PrepareFaultObservationRequestV1(RecoveryDiagnosticDTO):
    operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    command_id: Identity
    tenant_id: Identity
    database_id: Identity
    original_run: ExactRecordHead
    observer_registry: BrokerDiagnosticSourceRefV1
    requested_source_cut_locator: Identity
    requested_authority_snapshot_id: Identity
    requested_authority_snapshot_fingerprint: Digest

    @model_validator(mode="after")
    def fixed_request_registry_and_run(self) -> PrepareFaultObservationRequestV1:
        if self.original_run.owner != "agent_loop" or self.original_run.record_kind != "Run":
            raise ValueError("fault observation request original Run is not agent_loop Run")
        if self.observer_registry.source_kind != "FAULT_OBSERVER_REGISTRY":
            raise ValueError("fault observation request registry is not observer registry")
        return self


class FaultDiagnosticSubjectV1(RecoveryDiagnosticDTO):
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
    observer_registry: BrokerDiagnosticSourceRefV1
    diagnostic_observation: BrokerDiagnosticSourceRefV1
    source_capture_inventory_fingerprint: Digest
    fault_rule_registry_reference: CallSubjectHead
    fault_rule_registry_selection: CallSubjectHead
    broker_epoch: Identity
    broker_session: Identity
    runtime_generation: Identity

    @model_validator(mode="after")
    def no_untyped_source_roles(self) -> FaultDiagnosticSubjectV1:
        if self.original_run.owner != "agent_loop" or self.original_run.record_kind != "Run":
            raise ValueError("diagnostic subject original Run is not agent_loop Run")
        if (
            self.observer_registry.source_kind != "FAULT_OBSERVER_REGISTRY"
            or self.diagnostic_observation.source_kind != "FAULT_OBSERVATION"
        ):
            raise ValueError("diagnostic subject source roles differ from fixed roles")
        try:
            decode_recovery_fault_diagnostic_cut(self.canonical_diagnostic_cut_bytes)
            decode_fault_classification_provenance(self.canonical_provenance_bytes)
        except ValueError as error:
            raise ValueError(
                "diagnostic subject cut or provenance bytes are not fixed canonical owner bytes"
            ) from error
        return self


class FaultDiagnosticIssuanceRecordV1(RecoveryDiagnosticDTO):
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


def diagnostic_source_ref(body: BrokerDiagnosticBodyV1) -> BrokerDiagnosticSourceRefV1:
    """Derive the only allowed content reference for a fixed broker body type."""
    if isinstance(body, FaultCaptureRecordV1):
        kind: BrokerDiagnosticSourceKindV1 = "FAULT_CAPTURE"
    elif isinstance(body, FaultObserverRegistryV1):
        kind = "FAULT_OBSERVER_REGISTRY"
    elif isinstance(body, FaultObservationRecordV1):
        kind = "FAULT_OBSERVATION"
    elif isinstance(body, FaultDiagnosticIssuanceRecordV1):
        kind = "FAULT_ISSUANCE"
    else:  # pragma: no cover - statically closed public type
        raise TypeError("unknown broker diagnostic body")
    canonical_bytes = body.canonical_bytes()
    digest = _digest(canonical_bytes)
    return BrokerDiagnosticSourceRefV1(
        source_kind=kind,
        schema_id=body.schema_id,
        source_id=body.schema_id + ":" + digest,
        fingerprint=digest,
    )


class RetainedBrokerDiagnosticSourceV1(RecoveryDiagnosticDTO):
    reference: BrokerDiagnosticSourceRefV1
    canonical_source_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_fixed_source(self) -> RetainedBrokerDiagnosticSourceV1:
        decoded = _decode_diagnostic_source(self.reference.source_kind, self.canonical_source_bytes)
        if decoded.canonical_bytes() != self.canonical_source_bytes:
            raise ValueError("broker diagnostic source bytes are not canonical")
        expected = diagnostic_source_ref(decoded)
        if self.reference != expected:
            raise ValueError("broker diagnostic source reference differs from fixed bytes")
        return self


def _decode_diagnostic_source(
    kind: BrokerDiagnosticSourceKindV1, canonical_bytes: bytes
) -> BrokerDiagnosticBodyV1:
    body_type: type[RecoveryDiagnosticDTO]
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
        decoded = body_type.model_validate_json(canonical_bytes)
    except ValidationError as error:
        raise ValueError("invalid broker diagnostic source bytes") from error
    if decoded.schema_id != schema or decoded.canonical_bytes() != canonical_bytes:
        raise ValueError("broker diagnostic source bytes are not canonical fixed-row bytes")
    return decoded


def _decode_retained(
    retained: RetainedBrokerDiagnosticSourceV1, expected_kind: BrokerDiagnosticSourceKindV1
) -> BrokerDiagnosticBodyV1:
    if retained.reference.source_kind != expected_kind:
        raise ValueError("retained source kind differs from selected role")
    return _decode_diagnostic_source(expected_kind, retained.canonical_source_bytes)


def fault_capture_inventory_preimage(
    captures: tuple[RetainedBrokerDiagnosticSourceV1, ...],
) -> bytes:
    """Return the closed, ordered retained-capture inventory representation."""
    ordered_captures = []
    for retained in captures:
        if retained.reference.source_kind != "FAULT_CAPTURE":
            raise ValueError("fault capture inventory contains a non-capture retained source")
        _decode_retained(retained, "FAULT_CAPTURE")
        ordered_captures.append(
            {
                "reference": retained.reference.model_dump(mode="json"),
                "canonical_source_bytes": base64.b64encode(
                    retained.canonical_source_bytes
                ).decode(),
            }
        )
    return _canonical(
        {
            "schema_id": "chiplog.broker.fault-capture-inventory.v1",
            "ordered_captures": ordered_captures,
        }
    )


def fault_capture_inventory_fingerprint(
    captures: tuple[RetainedBrokerDiagnosticSourceV1, ...],
) -> str:
    """Hash the exact versioned retained-capture descriptor array."""
    return _digest(fault_capture_inventory_preimage(captures))


class SelectedFaultObservationV1(RecoveryDiagnosticDTO):
    kind: Literal["SELECTED_FAULT_OBSERVATION"] = "SELECTED_FAULT_OBSERVATION"
    retained_observation: RetainedBrokerDiagnosticSourceV1
    ordered_retained_captures: tuple[RetainedBrokerDiagnosticSourceV1, ...] = Field(min_length=1)
    selected_rules: SelectedFaultRuleRegistryV1
    retained_observer_registry: RetainedBrokerDiagnosticSourceV1

    @model_validator(mode="after")
    def complete_exact_inventory(self) -> SelectedFaultObservationV1:
        observation = _decode_retained(self.retained_observation, "FAULT_OBSERVATION")
        assert isinstance(observation, FaultObservationRecordV1)
        registry = _decode_retained(self.retained_observer_registry, "FAULT_OBSERVER_REGISTRY")
        assert isinstance(registry, FaultObserverRegistryV1)
        captures = tuple(
            cast(FaultCaptureRecordV1, _decode_retained(item, "FAULT_CAPTURE"))
            for item in self.ordered_retained_captures
        )
        references = tuple(item.reference for item in self.ordered_retained_captures)
        if references != observation.ordered_capture_refs:
            raise ValueError(
                "selected fault observation capture references differ from full ordered inventory"
            )
        if (
            fault_capture_inventory_fingerprint(self.ordered_retained_captures)
            != observation.ordered_capture_inventory_fingerprint
        ):
            raise ValueError("selected fault observation capture inventory fingerprint differs")
        if self.retained_observer_registry.reference != observation.observer_registry:
            raise ValueError("selected fault observation registry differs from observation")
        if (
            self.selected_rules.registry_reference != observation.fault_rule_registry_reference
            or self.selected_rules.selected_decision != observation.fault_rule_registry_selection
        ):
            raise ValueError("selected fault rules differ from observation")
        if (
            registry.fault_rule_registry_reference != self.selected_rules.registry_reference
            or registry.fault_rule_registry_selection != self.selected_rules.selected_decision
        ):
            raise ValueError("observer registry selected rules differ from observation")
        allowed_readers = {
            (reader.reader_id, reader.version) for reader in registry.allowed_reader_contracts
        }
        if any(
            (capture.subject.reader_contract, capture.subject.reader_version) not in allowed_readers
            for capture in captures
        ):
            raise ValueError("selected fault observation includes an unregistered capture reader")
        for capture in captures:
            subject = capture.subject
            if (
                subject.tenant_id != observation.tenant_id
                or subject.database_id != observation.database_id
            ):
                raise ValueError("selected fault observation capture tenant or database differs")
            if (
                subject.authority_snapshot_id != observation.authority_snapshot_id
                or subject.authority_snapshot_fingerprint
                != observation.authority_snapshot_fingerprint
            ):
                raise ValueError("selected fault observation capture authority snapshot differs")
            if (
                subject.observed_tenant_frontier != observation.tenant_frontier
                or subject.observed_materialization_commitment
                != observation.materialization_commitment
            ):
                raise ValueError("selected fault observation capture materialization cut differs")
            if (
                capture.originating_broker_epoch != observation.broker_epoch
                or capture.originating_broker_session != observation.broker_session
                or capture.runtime_generation != observation.runtime_generation
            ):
                raise ValueError("selected fault observation capture broker runtime differs")
        if tuple(capture.subject.ordinal for capture in captures) != tuple(range(len(captures))):
            raise ValueError(
                "selected fault observation capture ordinals are not exact inventory order"
            )
        return self


class SelectedFaultDiagnosticIssuanceV1(RecoveryDiagnosticDTO):
    kind: Literal["SELECTED_FAULT_ISSUANCE"] = "SELECTED_FAULT_ISSUANCE"
    retained_issuance: RetainedBrokerDiagnosticSourceV1
    invocation: InvocationProofRef

    @model_validator(mode="after")
    def exact_issuance_invocation_join(self) -> SelectedFaultDiagnosticIssuanceV1:
        issuance = _decode_retained(self.retained_issuance, "FAULT_ISSUANCE")
        assert isinstance(issuance, FaultDiagnosticIssuanceRecordV1)
        if (
            self.invocation.issuance_id != issuance.issuance_id
            or self.invocation.issuance_fingerprint
            != _digest(self.retained_issuance.canonical_source_bytes)
            or self.invocation.operation_subject != issuance.observation_subject_fingerprint
            or self.invocation.broker_epoch != issuance.subject.broker_epoch
            or self.invocation.broker_session != issuance.subject.broker_session
            or self.invocation.runtime_generation != issuance.subject.runtime_generation
        ):
            raise ValueError("invocation differs from exact retained diagnostic issuance")
        return self


class RecoveryDiagnosticFailureV1(RecoveryDiagnosticDTO):
    kind: Literal["HOLD", "STALE", "DENIED", "CONFLICT", "INTEGRITY_FAULT"]
    operation: Identity
    tenant_id: Identity
    subject_id: Identity
    reason: Identity


def _exact_current_suspended_run(current_run: ExactRecordHead, canonical_bytes: bytes) -> object:
    """Decode only a registered native Run member matching the supplied exact head."""
    decoded = []
    for row in EXECUTION_RUN_RECORD_ROWS:
        try:
            decoded.append(
                decode_execution_run_member(
                    ExecutionRunCanonicalMember(
                        owner="agent_loop",
                        record_kind="Run",
                        record_id=current_run.record_id,
                        schema_id=row.schema_id,
                        canonical_record_bytes=canonical_bytes,
                        fingerprint=current_run.fingerprint,
                    )
                )
            )
        except ExecutionRunRecordIntegrityError, ValidationError:
            continue
    if len(decoded) != 1:
        raise ValueError("fresh verification current Run bytes are not one fixed native Run")
    run = decoded[0].run
    if run.state != "SUSPENDED" or run.run_id != current_run.subject_id:
        raise ValueError("fresh verification current Run is not the exact SUSPENDED Run")
    return run


def _same_reference(left: object, right: object) -> bool:
    """Compare inert cross-owner source references by their complete wire fields."""
    return (
        isinstance(left, BaseModel)
        and isinstance(right, BaseModel)
        and left.model_dump(mode="json") == right.model_dump(mode="json")
    )


class FreshFaultVerificationV1(RecoveryDiagnosticDTO):
    operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    command: OwnerCommandBytes
    record: OwnerRecordBytes
    authentication: BrokerRecoveryDiagnosticAuthenticationV1
    retained_owner_result_schema: Literal["chiplog.execution.terminal-recovery-fault-result.v1"] = (
        TERMINAL_FAULT_RESULT_SCHEMA
    )
    retained_owner_result_bytes: bytes = Field(min_length=1)
    retained_issuance: SelectedFaultDiagnosticIssuanceV1
    retained_observation: SelectedFaultObservationV1
    current_authoritative_reads: AuthoritativeReadManifest
    current_runtime: RuntimeSurfaceCut
    current_run: ExactRecordHead
    current_run_canonical_bytes: bytes = Field(min_length=1)
    current_fault: ObservedAbsence
    writer_transaction_id: Identity

    @model_validator(mode="after")
    def fixed_fresh_fault_shape(self) -> FreshFaultVerificationV1:
        if (
            _digest(self.command.canonical_bytes) != self.command.fingerprint
            or _digest(self.record.canonical_bytes) != self.record.fingerprint
        ):
            raise ValueError("fresh verification owner bytes differ from owner fingerprints")
        if (
            self.record.owner != "agent_loop"
            or self.record.record_kind != "TERMINAL_RECOVERY_FAULT"
            or self.record.schema_id != "chiplog.execution.terminal-recovery-fault.v1"
        ):
            raise ValueError("fresh verification record is not the fixed terminal fault member")
        if self.command.fingerprint != self.authentication.final_request_fingerprint:
            raise ValueError("fresh verification command differs from diagnostic authentication")
        if (
            self.command.owner != "agent_loop"
            or self.command.schema_id != "chiplog.execution.prepare-terminal-recovery-fault.v1"
        ):
            raise ValueError("fresh verification command is not the fixed terminal fault request")
        try:
            request = PrepareTerminalRecoveryFaultV1.model_validate_json(
                self.command.canonical_bytes
            )
            result = PreparedTerminalRecoveryFaultV1.model_validate_json(
                self.retained_owner_result_bytes
            )
        except ValidationError as error:
            raise ValueError(
                "fresh verification request or retained result bytes are invalid"
            ) from error
        if (
            request.canonical_bytes() != self.command.canonical_bytes
            or result.canonical_bytes() != self.retained_owner_result_bytes
        ):
            raise ValueError(
                "fresh verification request or retained result bytes are not canonical"
            )
        try:
            decoded_record = decode_terminal_recovery_fault_member(
                TerminalRecoveryFaultCanonicalMemberV1(
                    owner=self.record.owner,
                    record_kind=cast(Literal["TERMINAL_RECOVERY_FAULT"], self.record.record_kind),
                    schema_id=cast(
                        Literal["chiplog.execution.terminal-recovery-fault.v1"],
                        self.record.schema_id,
                    ),
                    record_id=self.record.record_id,
                    canonical_record_bytes=self.record.canonical_bytes,
                    fingerprint=self.record.fingerprint,
                )
            )
        except (ValidationError, ValueError) as error:
            raise ValueError(
                "fresh verification owner record is not an exact terminal fault"
            ) from error
        if result.member != decoded_record.member:
            raise ValueError("fresh verification retained result differs from owner record")
        if result.source_request_fingerprint != self.command.fingerprint:
            raise ValueError("fresh verification retained result differs from final request")
        run = _exact_current_suspended_run(self.current_run, self.current_run_canonical_bytes)
        run_ref = CallSubjectHead(
            subject_id=self.current_run.subject_id,
            revision=Present(
                head=self.current_run.record_id, fingerprint=self.current_run.fingerprint
            ),
        )
        record = decoded_record.record
        if (
            request.run != run
            or request.cut.current_run != run_ref
            or request.cut.canonical_bytes() != record.diagnostic_cut.canonical_bytes()
            or record.source_request_fingerprint != self.command.fingerprint
            or record.original_run != run_ref
            or record.command_id != request.command_id
            or record.provenance != request.provenance
            or record.authority != request.authority
        ):
            raise ValueError("fresh verification request, Run, and terminal record differ")
        if not isinstance(request.authority, RegisteredRecoveryFaultObserverAuthorityV1):
            raise ValueError("fresh verification request lacks registered observer authority")
        if (
            self.authentication.original_run != self.current_run
            or self.authentication.diagnostic_cut_fingerprint
            != _digest(request.cut.canonical_bytes())
            or self.authentication.observation_subject_fingerprint
            != request.authority.observation_subject_fingerprint
        ):
            raise ValueError("fresh verification authentication differs from request cut")
        if (
            self.authentication.issuance != self.retained_issuance.retained_issuance.reference
            or self.authentication.diagnostic_observation
            != self.retained_observation.retained_observation.reference
            or self.authentication.observer_registry
            != self.retained_observation.retained_observer_registry.reference
        ):
            raise ValueError("fresh verification authentication differs from retained diagnostics")
        issuance = _decode_retained(self.retained_issuance.retained_issuance, "FAULT_ISSUANCE")
        observation = _decode_retained(
            self.retained_observation.retained_observation, "FAULT_OBSERVATION"
        )
        assert isinstance(issuance, FaultDiagnosticIssuanceRecordV1)
        assert isinstance(observation, FaultObservationRecordV1)
        if (
            self.authentication.source_capture_inventory_fingerprint
            != observation.ordered_capture_inventory_fingerprint
            or self.authentication.observation_subject_fingerprint
            != issuance.observation_subject_fingerprint
            or self.authentication.invocation != self.retained_issuance.invocation
            or self.authentication.invocation.operation_subject
            != issuance.observation_subject_fingerprint
            or not _same_reference(
                request.authority.observer_registry, self.authentication.observer_registry
            )
            or not _same_reference(
                request.authority.diagnostic_observation, self.authentication.diagnostic_observation
            )
            or not _same_reference(request.authority.issuance, self.authentication.issuance)
        ):
            raise ValueError("fresh verification authentication differs from retained issuance")
        if (
            self.current_run.owner != "agent_loop"
            or self.current_run.record_kind != "Run"
            or self.current_fault.owner != "agent_loop"
            or self.current_fault.record_kind != "TERMINAL_RECOVERY_FAULT"
            or self.current_fault.subject_id != self.current_run.subject_id
        ):
            raise ValueError(
                "fresh verification current Run or fault absence differs from fixed role"
            )
        return self


class VerifiedFreshFaultV1(RecoveryDiagnosticDTO):
    kind: Literal["VERIFIED_FRESH"] = "VERIFIED_FRESH"
    operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    tenant_id: Identity
    command_id: Identity
    final_request_fingerprint: Digest
    observation_subject_fingerprint: Digest
    record_fingerprint: Digest
    diagnostic_cut_fingerprint: Digest
    writer_transaction_id: Identity
    current_read_manifest_fingerprint: Digest
    runtime_generation: Identity


class HistoricalFaultVerificationV1(RecoveryDiagnosticDTO):
    operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    current_lookup_invocation: InvocationProofRef
    original_command: OwnerCommandBytes
    original_authentication: BrokerRecoveryDiagnosticAuthenticationV1
    retained_issuance: SelectedFaultDiagnosticIssuanceV1
    retained_observation: SelectedFaultObservationV1
    selected: JournalSelectedPublication
    current_authoritative_reads: AuthoritativeReadManifest

    @model_validator(mode="after")
    def historical_diagnostics_bind_original_authentication(self) -> HistoricalFaultVerificationV1:
        if _digest(self.original_command.canonical_bytes) != self.original_command.fingerprint:
            raise ValueError(
                "historical verification original command bytes differ from fingerprint"
            )
        if (
            self.original_command.owner != "agent_loop"
            or self.original_command.schema_id
            != "chiplog.execution.prepare-terminal-recovery-fault.v1"
        ):
            raise ValueError(
                "historical verification original command is not a terminal fault request"
            )
        try:
            request = PrepareTerminalRecoveryFaultV1.model_validate_json(
                self.original_command.canonical_bytes
            )
        except ValidationError as error:
            raise ValueError(
                "historical verification original request bytes are invalid"
            ) from error
        if request.canonical_bytes() != self.original_command.canonical_bytes:
            raise ValueError("historical verification original request bytes are not canonical")
        if (
            self.original_command.fingerprint
            != self.original_authentication.final_request_fingerprint
            or self.original_authentication.issuance
            != self.retained_issuance.retained_issuance.reference
            or self.original_authentication.diagnostic_observation
            != self.retained_observation.retained_observation.reference
            or self.original_authentication.observer_registry
            != self.retained_observation.retained_observer_registry.reference
        ):
            raise ValueError("historical verification differs from retained original diagnostics")
        issuance = _decode_retained(self.retained_issuance.retained_issuance, "FAULT_ISSUANCE")
        observation = _decode_retained(
            self.retained_observation.retained_observation, "FAULT_OBSERVATION"
        )
        assert isinstance(issuance, FaultDiagnosticIssuanceRecordV1)
        assert isinstance(observation, FaultObservationRecordV1)
        request_run = ExactRecordHead(
            owner="agent_loop",
            record_kind="Run",
            subject_id=request.run.run_id,
            record_id=request.run.head,
            fingerprint=request.run.digest(),
        )
        if (
            self.original_authentication.original_run != request_run
            or self.original_authentication.diagnostic_cut_fingerprint
            != _digest(request.cut.canonical_bytes())
            or self.original_authentication.source_capture_inventory_fingerprint
            != observation.ordered_capture_inventory_fingerprint
            or self.original_authentication.observation_subject_fingerprint
            != issuance.observation_subject_fingerprint
            or self.original_authentication.invocation != self.retained_issuance.invocation
        ):
            raise ValueError(
                "historical verification original authentication differs from retained proof"
            )
        subject = issuance.subject
        if not isinstance(request.authority, RegisteredRecoveryFaultObserverAuthorityV1):
            raise ValueError("historical verification request lacks registered observer authority")
        if (
            subject.tenant_id != request.cut.tenant_id
            or subject.database_id != request.cut.database_id
            or subject.command_id != request.command_id
            or subject.original_run != request_run
            or subject.canonical_diagnostic_cut_bytes != request.cut.canonical_bytes()
            or subject.canonical_provenance_bytes != request.provenance.canonical_bytes()
            or subject.source_capture_inventory_fingerprint
            != observation.ordered_capture_inventory_fingerprint
            or not _same_reference(subject.observer_registry, request.authority.observer_registry)
            or not _same_reference(
                subject.diagnostic_observation, request.authority.diagnostic_observation
            )
        ):
            raise ValueError(
                "historical verification retained issuance differs from original request"
            )
        if (
            self.selected.tenant_id != request.cut.tenant_id
            or self.selected.command_id != request.command_id
            or len(self.selected.complete_records) != 1
        ):
            raise ValueError(
                "historical verification selected decision differs from original request"
            )
        selected_record = self.selected.complete_records[0]
        if (
            selected_record.owner != "agent_loop"
            or selected_record.record_kind != "TERMINAL_RECOVERY_FAULT"
            or selected_record.schema_id != "chiplog.execution.terminal-recovery-fault.v1"
            or _digest(selected_record.canonical_bytes) != selected_record.fingerprint
        ):
            raise ValueError(
                "historical verification selected record is not a fixed terminal fault"
            )
        try:
            decoded = decode_terminal_recovery_fault_member(
                TerminalRecoveryFaultCanonicalMemberV1(
                    owner="agent_loop",
                    record_kind="TERMINAL_RECOVERY_FAULT",
                    schema_id="chiplog.execution.terminal-recovery-fault.v1",
                    record_id=selected_record.record_id,
                    canonical_record_bytes=selected_record.canonical_bytes,
                    fingerprint=selected_record.fingerprint,
                )
            )
        except (ValidationError, ValueError) as error:
            raise ValueError("historical verification selected record bytes are invalid") from error
        record = decoded.record
        if (
            record.source_request_fingerprint != self.original_command.fingerprint
            or record.tenant_id != request.cut.tenant_id
            or record.database_id != request.cut.database_id
            or record.command_id != request.command_id
            or record.original_run != request.cut.current_run
            or record.suspension_baseline != request.cut.suspension_baseline
            or record.suspension_pair != request.cut.suspension_pair
            or record.diagnostic_cut != request.cut
            or record.provenance != request.provenance
            or record.authority != request.authority
        ):
            raise ValueError(
                "historical verification selected record differs from original request"
            )
        return self


class VerifiedHistoricalFaultV1(RecoveryDiagnosticDTO):
    kind: Literal["VERIFIED_HISTORICAL"] = "VERIFIED_HISTORICAL"
    operation: Literal["recovery.publish_terminal_fault"] = FAULT_OPERATION
    tenant_id: Identity
    command_id: Identity
    decision_id: Identity
    decision_head: Identity
    decision_fingerprint: Digest
    fault_record: ExactRecordHead
    final_request_fingerprint: Digest

    @model_validator(mode="after")
    def fixed_fault_record(self) -> VerifiedHistoricalFaultV1:
        if (
            self.fault_record.owner != "agent_loop"
            or self.fault_record.record_kind != "TERMINAL_RECOVERY_FAULT"
        ):
            raise ValueError("historical verification result is not an agent_loop terminal fault")
        return self


class RecoveryDiagnosticCapturePortV1(Protocol):
    async def capture_fault_source(
        self, request: CaptureFaultSourceRequestV1
    ) -> FaultCaptureSelectedV1 | RecoveryDiagnosticFailureV1: ...
    def read_fault_capture(
        self, reference: BrokerDiagnosticSourceRefV1, *, tenant_id: str, database_id: str
    ) -> FaultCaptureSelectedV1 | RecoveryDiagnosticFailureV1: ...


class RecoveryDiagnosticObservationPortV1(Protocol):
    async def observe_fault_sources(
        self, request: PrepareFaultObservationRequestV1
    ) -> SelectedFaultObservationV1 | RecoveryDiagnosticFailureV1: ...
    def read_fault_observation(
        self, reference: BrokerDiagnosticSourceRefV1, *, tenant_id: str, database_id: str
    ) -> SelectedFaultObservationV1 | RecoveryDiagnosticFailureV1: ...


class RecoveryFaultRuleSelectionPortV1(Protocol):
    def read_selected_fault_rules(
        self,
        reference: CallSubjectHead,
        selected_decision: CallSubjectHead,
        *,
        tenant_id: str,
        database_id: str,
    ) -> SelectedFaultRuleRegistryV1 | RecoveryDiagnosticFailureV1: ...


class RecoveryDiagnosticIssuancePortV1(Protocol):
    async def issue_fault_observer(
        self, subject: FaultDiagnosticSubjectV1
    ) -> SelectedFaultDiagnosticIssuanceV1 | RecoveryDiagnosticFailureV1: ...
    def read_fault_issuance(
        self, reference: BrokerDiagnosticSourceRefV1, *, tenant_id: str, database_id: str
    ) -> SelectedFaultDiagnosticIssuanceV1 | RecoveryDiagnosticFailureV1: ...


class RecoveryDiagnosticVerificationPortV1(Protocol):
    def verify_fresh_fault(
        self, request: FreshFaultVerificationV1
    ) -> VerifiedFreshFaultV1 | RecoveryDiagnosticFailureV1: ...
    def verify_historical_fault(
        self, request: HistoricalFaultVerificationV1
    ) -> VerifiedHistoricalFaultV1 | RecoveryDiagnosticFailureV1: ...
