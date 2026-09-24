"""Owner-local Phase-C shapes for immutable terminal recovery fault records.

This module validates retained representations only.  Broker capture/proof issuance,
current-cut selection, classification, and publication remain outside this owner
contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .contracts import Frozen
from .execution_recovery_observations import (
    ExecutionRecoveryCut,
    ExecutionRecoveryDTO,
    RecoverySourceRecord,
)
from .execution_run_versions import ExecutionRun
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    NonSchedulerFence,
    Present,
    RunExecutionFence,
    SchedulerExecutionFence,
)
from .recovery_fault_rule_contracts import (
    FaultFindingCodeV1,
    FaultRuleRegistryIntegrityError,
    SelectedFaultRuleRegistryV1,
    decode_fault_rule_registry,
    lookup_fault_rule,
)
from .recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    RecoveryFrontierRegistryIntegrityError,
    decode_frontier_registry,
)
from .recovery_wire_contracts import (
    decode_execution_recovery_request,
    decode_execution_recovery_result,
    validate_execution_recovery_exchange,
)
from .terminal_fault_observer_sources import (
    FaultObserverRetainedInputsV1,
    validate_fault_observer_sources,
)

AGENT_LOOP_OWNER: Literal["agent_loop"] = "agent_loop"
TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA: Literal["chiplog.execution.terminal-recovery-fault.v1"] = (
    "chiplog.execution.terminal-recovery-fault.v1"
)
TERMINAL_RECOVERY_FAULT_RECORD_KIND: Literal["TERMINAL_RECOVERY_FAULT"] = "TERMINAL_RECOVERY_FAULT"
RECOVERY_FAULT_DIAGNOSTIC_CUT_SCHEMA = "chiplog.execution.recovery-fault-diagnostic-cut.v1"
FAULT_CLASSIFICATION_PROVENANCE_SCHEMA = "chiplog.execution.fault-classification-provenance.v1"


class FaultRecoveryRecordIntegrityError(ValueError):
    """A supplied terminal-fault physical member is not exact and canonical."""


class FaultBrokerSourceRefV1(ExecutionRecoveryDTO):
    """Inert broker-source reference; it cannot impersonate an owner record head."""

    source_owner: Literal["broker"] = "broker"
    source_kind: Literal[
        "FAULT_CAPTURE", "FAULT_OBSERVATION", "FAULT_OBSERVER_REGISTRY", "FAULT_ISSUANCE"
    ]
    schema_id: Identity
    source_id: Identity
    fingerprint: Digest


class RecoveryFaultObservedMemberV1(ExecutionRecoveryDTO):
    """Authenticated envelope metadata around bytes that may be semantically invalid."""

    role: Identity
    ordinal: int = Field(strict=True, ge=0, le=2**64 - 1)
    owner: Identity
    record_kind: Identity
    declared_schema_id: Identity
    logical_subject_id: Identity
    physical_record_id: Identity
    selected_decision: CallSubjectHead
    claimed_fingerprint: Digest
    observed_bytes: bytes
    observed_bytes_fingerprint: Digest
    capture: FaultBrokerSourceRefV1

    @model_validator(mode="after")
    def exact_observed_bytes_fingerprint(self) -> RecoveryFaultObservedMemberV1:
        if hashlib.sha256(self.observed_bytes).hexdigest() != self.observed_bytes_fingerprint:
            raise ValueError("observed bytes fingerprint differs from retained bytes")
        return self


class RecoveryFaultFindingV1(ExecutionRecoveryDTO):
    """A typed finding; registry membership is checked by the exchange validator."""

    code: FaultFindingCodeV1
    invariant: CallSubjectHead
    affected_observation_ordinals: tuple[int, ...] = Field(min_length=1)
    expected_relation: Identity
    detail: str = Field(max_length=4096)

    @model_validator(mode="after")
    def canonical_affected_ordinals(self) -> RecoveryFaultFindingV1:
        if any(value < 0 or value > 2**64 - 1 for value in self.affected_observation_ordinals):
            raise ValueError("finding ordinal is outside UInt64")
        if (
            tuple(sorted(set(self.affected_observation_ordinals)))
            != self.affected_observation_ordinals
        ):
            raise ValueError("finding ordinals must be unique and canonical ordered")
        return self


class DecodedRecoveryFaultFrontierV1(ExecutionRecoveryDTO):
    kind: Literal["DECODED_FRONTIER"] = "DECODED_FRONTIER"
    cut: ExecutionRecoveryCut
    canonical_cut_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def exact_cut_bytes(self) -> DecodedRecoveryFaultFrontierV1:
        if self.cut.canonical_bytes() != self.canonical_cut_bytes:
            raise ValueError("decoded frontier bytes differ from exact canonical cut")
        return self


class UndecodableRecoveryFaultFrontierV1(ExecutionRecoveryDTO):
    kind: Literal["UNDECODABLE_FRONTIER"] = "UNDECODABLE_FRONTIER"
    expected_schema_id: Identity
    raw_bytes: bytes
    capture: FaultBrokerSourceRefV1


RecoveryFaultFrontierObservationV1 = Annotated[
    DecodedRecoveryFaultFrontierV1 | UndecodableRecoveryFaultFrontierV1,
    Field(discriminator="kind"),
]


class RecoveryFaultInventoryV1(ExecutionRecoveryDTO):
    schema_id: Literal["chiplog.execution.recovery-fault-inventory.v1"] = (
        "chiplog.execution.recovery-fault-inventory.v1"
    )
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: int = Field(strict=True, ge=0, le=2**64 - 1)
    materialization_commitment: Digest
    current_run: CallSubjectHead
    suspension_baseline: CallSubjectHead
    suspension_pair: CallSubjectHead
    frontier_registry: CallSubjectHead
    canonical_registry_bytes: bytes = Field(min_length=1)
    classifier: CallSubjectHead
    disposition_version: Identity
    fault_rule_registry: CallSubjectHead
    fault_rule_registry_selection: CallSubjectHead
    ordered_observations: tuple[RecoveryFaultObservedMemberV1, ...] = Field(min_length=1)


class RecoveryFaultDiagnosticCutV1(ExecutionRecoveryDTO):
    """Well-formed diagnostic context which may retain malformed nested evidence."""

    schema_id: Literal["chiplog.execution.recovery-fault-diagnostic-cut.v1"] = (
        "chiplog.execution.recovery-fault-diagnostic-cut.v1"
    )
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: int = Field(strict=True, ge=0, le=2**64 - 1)
    materialization_commitment: Digest
    current_run: CallSubjectHead
    suspension_baseline: CallSubjectHead
    suspension_pair: CallSubjectHead
    frontier_registry: CallSubjectHead
    canonical_registry_bytes: bytes = Field(min_length=1)
    classifier: CallSubjectHead
    disposition_version: Identity
    fault_rule_registry: CallSubjectHead
    fault_rule_registry_selection: CallSubjectHead
    ordered_observations: tuple[RecoveryFaultObservedMemberV1, ...] = Field(min_length=1)
    complete_inventory_fingerprint: Digest
    frontier_observation: RecoveryFaultFrontierObservationV1
    expected_fault: Absent

    @model_validator(mode="after")
    def canonical_observation_ordinals(self) -> RecoveryFaultDiagnosticCutV1:
        if tuple(member.ordinal for member in self.ordered_observations) != tuple(
            range(len(self.ordered_observations))
        ):
            raise ValueError(
                "diagnostic observation ordinals must be exact ordered inventory slots"
            )
        if (
            diagnostic_inventory_fingerprint(recovery_fault_inventory(self))
            != self.complete_inventory_fingerprint
        ):
            raise ValueError("diagnostic inventory fingerprint differs from fixed projection")
        return self


def recovery_fault_inventory(cut: RecoveryFaultDiagnosticCutV1) -> RecoveryFaultInventoryV1:
    return RecoveryFaultInventoryV1(
        **{
            name: getattr(cut, name)
            for name in RecoveryFaultInventoryV1.model_fields
            if name != "schema_id"
        }
    )


def diagnostic_inventory_preimage(inventory: RecoveryFaultInventoryV1) -> bytes:
    return inventory.canonical_bytes()


def diagnostic_inventory_fingerprint(inventory: RecoveryFaultInventoryV1) -> str:
    return hashlib.sha256(diagnostic_inventory_preimage(inventory)).hexdigest()


class PriorClassifiedExchangeV1(ExecutionRecoveryDTO):
    kind: Literal["PRIOR_CLASSIFIED_EXCHANGE"] = "PRIOR_CLASSIFIED_EXCHANGE"
    request_schema_id: Literal["chiplog.execution.recovery-request.v1"] = (
        "chiplog.execution.recovery-request.v1"
    )
    canonical_request_bytes: bytes = Field(min_length=1)
    result_schema_id: Literal["chiplog.execution.recovery-result.v1"] = (
        "chiplog.execution.recovery-result.v1"
    )
    canonical_result_bytes: bytes = Field(min_length=1)


class DirectDiagnosticProvenanceV1(ExecutionRecoveryDTO):
    kind: Literal["DIRECT_DIAGNOSTIC"] = "DIRECT_DIAGNOSTIC"
    trigger_capture: FaultBrokerSourceRefV1
    reason: Literal["RECOVERY_INPUT_UNDECODABLE", "FAULT_OBSERVED_BEFORE_CLASSIFICATION"]


FaultClassificationProvenanceV1 = Annotated[
    PriorClassifiedExchangeV1 | DirectDiagnosticProvenanceV1, Field(discriminator="kind")
]
_PROVENANCE_ADAPTER: TypeAdapter[FaultClassificationProvenanceV1] = TypeAdapter(
    FaultClassificationProvenanceV1
)


def decode_recovery_fault_diagnostic_cut(raw: bytes) -> RecoveryFaultDiagnosticCutV1:
    value = RecoveryFaultDiagnosticCutV1.model_validate_json(raw)
    if value.canonical_bytes() != raw:
        raise FaultRecoveryRecordIntegrityError("diagnostic cut bytes are not canonical")
    return value


def decode_fault_classification_provenance(raw: bytes) -> FaultClassificationProvenanceV1:
    value = _PROVENANCE_ADAPTER.validate_json(raw)
    if value.canonical_bytes() != raw:
        raise FaultRecoveryRecordIntegrityError(
            "fault classification provenance bytes are not canonical"
        )
    return value


class WorkerFaultPublicationAuthorityV1(ExecutionRecoveryDTO):
    kind: Literal["WORKER_FAULT_PUBLICATION"] = "WORKER_FAULT_PUBLICATION"
    fence: RunExecutionFence
    invocation: CallSubjectHead


class RegisteredRecoveryFaultObserverAuthorityV1(ExecutionRecoveryDTO):
    kind: Literal["REGISTERED_RECOVERY_FAULT_OBSERVER"] = "REGISTERED_RECOVERY_FAULT_OBSERVER"
    observer_registry: FaultBrokerSourceRefV1
    diagnostic_observation: FaultBrokerSourceRefV1
    issuance: FaultBrokerSourceRefV1
    observation_subject_fingerprint: Digest


FaultPublicationAuthorityV1 = Annotated[
    WorkerFaultPublicationAuthorityV1 | RegisteredRecoveryFaultObserverAuthorityV1,
    Field(discriminator="kind"),
]


class PrepareTerminalRecoveryFaultV1(ExecutionRecoveryDTO):
    kind: Literal["PREPARE_TERMINAL_RECOVERY_FAULT_V1"] = "PREPARE_TERMINAL_RECOVERY_FAULT_V1"
    schema_id: Literal["chiplog.execution.prepare-terminal-recovery-fault.v1"] = (
        "chiplog.execution.prepare-terminal-recovery-fault.v1"
    )
    command_id: Identity
    run: ExecutionRun
    cut: RecoveryFaultDiagnosticCutV1
    provenance: FaultClassificationProvenanceV1
    authority: FaultPublicationAuthorityV1

    @model_validator(mode="after")
    def native_suspended_run_and_cut_join(self) -> PrepareTerminalRecoveryFaultV1:
        pending = self.run.model_copy(update={"head": "pending"})
        if self.run.state != "SUSPENDED" or self.run.head != "loop:" + pending.digest():
            raise ValueError("terminal fault requires a native SUSPENDED Run")
        expected = CallSubjectHead(
            subject_id=self.run.run_id,
            revision=Present(head=self.run.head, fingerprint=self.run.digest()),
        )
        if self.cut.current_run != expected:
            raise ValueError("diagnostic cut current Run differs from selected native Run")
        if self.cut.tenant_id != self.run.tenant:
            raise ValueError("diagnostic cut tenant differs from selected Run tenant")
        if self.run.suspension_baseline != self.cut.suspension_baseline:
            raise ValueError("selected Run suspension baseline differs from diagnostic cut")
        return self


class TerminalRecoveryFaultRecordV1(ExecutionRecoveryDTO):
    """One fault body, deliberately free of its future physical ID and commitment."""

    schema_id: Literal["chiplog.execution.terminal-recovery-fault.v1"] = (
        TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA
    )
    kind: Literal["TERMINAL_RECOVERY_FAULT_V1"] = "TERMINAL_RECOVERY_FAULT_V1"
    tenant_id: Identity
    database_id: Identity
    command_id: Identity
    original_run: CallSubjectHead
    suspension_baseline: CallSubjectHead
    suspension_pair: CallSubjectHead
    source_request_fingerprint: Digest
    diagnostic_cut: RecoveryFaultDiagnosticCutV1
    provenance: FaultClassificationProvenanceV1
    authority: FaultPublicationAuthorityV1
    disposition: Literal["TERMINAL_RECOVERY_FAULT"]
    findings: tuple[RecoveryFaultFindingV1, ...] = Field(min_length=1)
    continuation: Literal["FORBIDDEN"]
    predecessor_fault: Absent


class TerminalRecoveryFaultCanonicalMemberV1(Frozen):
    """The single fixed physical owner/kind/schema envelope."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    owner: Literal["agent_loop"] = AGENT_LOOP_OWNER
    record_kind: Literal["TERMINAL_RECOVERY_FAULT"] = TERMINAL_RECOVERY_FAULT_RECORD_KIND
    schema_id: Literal["chiplog.execution.terminal-recovery-fault.v1"] = (
        TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA
    )
    record_id: Identity
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


@dataclass(frozen=True)
class DecodedTerminalRecoveryFaultMemberV1:
    member: TerminalRecoveryFaultCanonicalMemberV1
    record: TerminalRecoveryFaultRecordV1


class PreparedTerminalRecoveryFaultV1(ExecutionRecoveryDTO):
    kind: Literal["PREPARED_TERMINAL_RECOVERY_FAULT_V1"] = "PREPARED_TERMINAL_RECOVERY_FAULT_V1"
    source_request_fingerprint: Digest
    member: TerminalRecoveryFaultCanonicalMemberV1
    complete_commitment: Digest


TerminalRecoveryFaultPreparationResultV1 = Annotated[
    PreparedTerminalRecoveryFaultV1 | CallPreparationRejected, Field(discriminator="kind")
]


class TerminalRecoveryFaultPreparationPort(Protocol):
    async def prepare_terminal_recovery_fault(
        self,
        request: PrepareTerminalRecoveryFaultV1,
        selected_rules: SelectedFaultRuleRegistryV1,
        *,
        observer_sources: FaultObserverRetainedInputsV1 | None = None,
    ) -> TerminalRecoveryFaultPreparationResultV1: ...


def _content_id(canonical_bytes: bytes) -> str:
    return (
        TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA + ":" + hashlib.sha256(canonical_bytes).hexdigest()
    )


def decode_terminal_recovery_fault_member(
    member: TerminalRecoveryFaultCanonicalMemberV1,
) -> DecodedTerminalRecoveryFaultMemberV1:
    """Decode the closed physical row while returning its original supplied bytes."""

    try:
        record = TerminalRecoveryFaultRecordV1.model_validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise FaultRecoveryRecordIntegrityError("invalid terminal recovery fault bytes") from error
    if record.canonical_bytes() != member.canonical_record_bytes:
        raise FaultRecoveryRecordIntegrityError("terminal recovery fault bytes are not canonical")
    fingerprint = hashlib.sha256(member.canonical_record_bytes).hexdigest()
    if member.fingerprint != fingerprint:
        raise FaultRecoveryRecordIntegrityError("terminal recovery fault fingerprint mismatch")
    if member.record_id != _content_id(member.canonical_record_bytes):
        raise FaultRecoveryRecordIntegrityError("terminal recovery fault physical ID mismatch")
    return DecodedTerminalRecoveryFaultMemberV1(member=member, record=record)


def terminal_recovery_fault_ref(
    decoded: DecodedTerminalRecoveryFaultMemberV1, selected_decision: CallSubjectHead
) -> RecoverySourceRecord:
    """Adapt an exact decoded fault into the existing generic recovery-source wrapper."""

    decoded = decode_terminal_recovery_fault_member(decoded.member)
    return RecoverySourceRecord(
        owner=AGENT_LOOP_OWNER,
        subject=decoded.record.original_run,
        schema_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA,
        canonical_record_bytes=decoded.member.canonical_record_bytes,
        selected_decision=selected_decision,
        physical_record=CallSubjectHead(
            subject_id=decoded.record.original_run.subject_id,
            revision=Present(head=decoded.member.record_id, fingerprint=decoded.member.fingerprint),
        ),
    )


def _prepared_commitment(
    source_request_fingerprint: str, member: TerminalRecoveryFaultCanonicalMemberV1
) -> str:
    """Hash the post-envelope result domain without creating a record self-reference."""

    payload = (
        b"chiplog.execution.terminal-recovery-fault-result.v1\0"
        + source_request_fingerprint.encode()
        + b"\0"
        + member.canonical_bytes()
    )
    return hashlib.sha256(payload).hexdigest()


def validate_terminal_recovery_fault_exchange(
    request: PrepareTerminalRecoveryFaultV1,
    result: PreparedTerminalRecoveryFaultV1,
    selected_rules: SelectedFaultRuleRegistryV1,
    *,
    observer_sources: FaultObserverRetainedInputsV1 | None = None,
) -> None:
    """Check owner representation joins; broker authentication and currentness are external."""

    request = PrepareTerminalRecoveryFaultV1.model_validate_json(request.canonical_bytes())
    result = PreparedTerminalRecoveryFaultV1.model_validate_json(result.canonical_bytes())
    if result.source_request_fingerprint != hashlib.sha256(request.canonical_bytes()).hexdigest():
        raise FaultRecoveryRecordIntegrityError("fault result does not bind exact request bytes")
    decoded = decode_terminal_recovery_fault_member(result.member)
    record = decoded.record
    if result.complete_commitment != _prepared_commitment(
        result.source_request_fingerprint, decoded.member
    ):
        raise FaultRecoveryRecordIntegrityError(
            "fault result commitment differs from physical envelope"
        )
    if (
        record.source_request_fingerprint != result.source_request_fingerprint
        or record.source_request_fingerprint
        != hashlib.sha256(request.canonical_bytes()).hexdigest()
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
        raise FaultRecoveryRecordIntegrityError(
            "fault record does not retain exact request selection"
        )
    observation_ordinals = tuple(item.ordinal for item in request.cut.ordered_observations)
    try:
        frontier_registry = decode_frontier_registry(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            request.cut.canonical_registry_bytes,
            expected_reference=request.cut.frontier_registry,
        )
    except RecoveryFrontierRegistryIntegrityError as error:
        raise FaultRecoveryRecordIntegrityError("frontier registry source is invalid") from error
    if (
        isinstance(request.cut.frontier_observation, DecodedRecoveryFaultFrontierV1)
        and request.cut.frontier_observation.cut.frontier.registry != frontier_registry
    ):
        raise FaultRecoveryRecordIntegrityError(
            "decoded frontier registry differs from selected source"
        )
    if isinstance(request.cut.frontier_observation, DecodedRecoveryFaultFrontierV1):
        value = request.cut.frontier_observation.cut
        if (
            value.tenant_id,
            value.database_id,
            value.tenant_commit_sequence,
            value.materialization_commitment,
            value.current_run,
        ) != (
            request.cut.tenant_id,
            request.cut.database_id,
            request.cut.tenant_commit_sequence,
            request.cut.materialization_commitment,
            request.cut.current_run,
        ):
            raise FaultRecoveryRecordIntegrityError(
                "decoded frontier differs from diagnostic coordinates"
            )
        if (
            value.frontier.tenant_id != request.cut.tenant_id
            or value.frontier.run_id != request.run.run_id
            or value.frontier.tenant_commit_sequence != request.cut.tenant_commit_sequence
        ):
            raise FaultRecoveryRecordIntegrityError(
                "nested frontier differs from diagnostic coordinates"
            )
    else:
        raw = request.cut.frontier_observation.raw_bytes
        try:
            parsed = ExecutionRecoveryCut.model_validate_json(raw)
        except ValidationError:
            parsed = None
        if parsed is not None and parsed.canonical_bytes() == raw:
            raise FaultRecoveryRecordIntegrityError(
                "canonical valid frontier cannot be marked undecodable"
            )
        if not any(
            item.capture == request.cut.frontier_observation.capture and item.observed_bytes == raw
            for item in request.cut.ordered_observations
        ):
            raise FaultRecoveryRecordIntegrityError(
                "undecodable frontier is not an observed capture"
            )

    def capture(ref: FaultBrokerSourceRefV1) -> bool:
        return (
            ref.source_kind == "FAULT_CAPTURE"
            and ref.schema_id == "chiplog.broker.fault-capture.v1"
        )

    if any(not capture(item.capture) for item in request.cut.ordered_observations):
        raise FaultRecoveryRecordIntegrityError("observed capture is not the fixed capture source")
    if isinstance(
        request.cut.frontier_observation, UndecodableRecoveryFaultFrontierV1
    ) and not capture(request.cut.frontier_observation.capture):
        raise FaultRecoveryRecordIntegrityError("undecodable frontier capture is not fixed")
    if isinstance(request.provenance, DirectDiagnosticProvenanceV1) and not capture(
        request.provenance.trigger_capture
    ):
        raise FaultRecoveryRecordIntegrityError("direct diagnostic trigger is not fixed capture")
    if isinstance(request.authority, RegisteredRecoveryFaultObserverAuthorityV1):
        if observer_sources is None:
            raise FaultRecoveryRecordIntegrityError("observer authority requires retained sources")
        validate_fault_observer_sources(request, observer_sources)
        for ref, kind, schema in (
            (
                request.authority.observer_registry,
                "FAULT_OBSERVER_REGISTRY",
                "chiplog.broker.fault-observer-registry.v1",
            ),
            (
                request.authority.diagnostic_observation,
                "FAULT_OBSERVATION",
                "chiplog.broker.fault-observation.v1",
            ),
            (
                request.authority.issuance,
                "FAULT_ISSUANCE",
                "chiplog.broker.fault-diagnostic-issuance.v1",
            ),
        ):
            if ref.source_kind != kind or ref.schema_id != schema:
                raise FaultRecoveryRecordIntegrityError("observer authority source is not fixed")
    if isinstance(request.authority, WorkerFaultPublicationAuthorityV1):
        if observer_sources is not None:
            raise FaultRecoveryRecordIntegrityError(
                "worker authority cannot receive observer sources"
            )
        fence = request.authority.fence
        if isinstance(fence, NonSchedulerFence):
            valid_fence = fence.run_id == request.run.run_id and fence.run_head == request.run.head
        else:
            assert isinstance(fence, SchedulerExecutionFence)
            valid_fence = (
                fence.lineage.current_run_id == request.run.run_id
                and fence.run_head == request.run.head
            )
        if not valid_fence:
            raise FaultRecoveryRecordIntegrityError("worker fence differs from selected native Run")
    try:
        registry = decode_fault_rule_registry(
            selected_rules,
            expected_registry=request.cut.fault_rule_registry,
            expected_selected_decision=request.cut.fault_rule_registry_selection,
            expected_classifier_id=request.cut.classifier.subject_id,
            expected_classifier_version=request.cut.classifier.revision.head,
            expected_disposition_version=request.cut.disposition_version,
        )
    except FaultRuleRegistryIntegrityError as error:
        raise FaultRecoveryRecordIntegrityError(
            "fault rule registry selection is invalid"
        ) from error
    for finding in record.findings:
        if any(
            ordinal not in observation_ordinals for ordinal in finding.affected_observation_ordinals
        ):
            raise FaultRecoveryRecordIntegrityError(
                "fault finding references an absent observation slot"
            )
        try:
            rule = lookup_fault_rule(
                registry,
                finding.invariant,
                finding_code=finding.code,
                expected_relation=finding.expected_relation,
            )
        except FaultRuleRegistryIntegrityError as error:
            raise FaultRecoveryRecordIntegrityError(
                "fault finding is not a selected registered rule"
            ) from error
        selected = tuple(
            request.cut.ordered_observations[index]
            for index in finding.affected_observation_ordinals
        )
        if any(
            item.role not in {role.role for role in rule.ordered_observation_roles}
            for item in selected
        ):
            raise FaultRecoveryRecordIntegrityError("fault finding selects an undeclared role")
        for role in rule.ordered_observation_roles:
            matching = tuple(item for item in selected if item.role == role.role)
            if not role.min_count <= len(matching) <= role.max_count:
                raise FaultRecoveryRecordIntegrityError(
                    "fault finding violates registered role count"
                )
            if any(
                (item.owner, item.record_kind, item.declared_schema_id)
                not in {
                    (source.owner, source.record_kind, source.schema_id)
                    for source in role.allowed_owner_kinds
                }
                for item in matching
            ):
                raise FaultRecoveryRecordIntegrityError(
                    "fault finding violates registered source class"
                )
    if isinstance(request.provenance, PriorClassifiedExchangeV1):
        prior_request = decode_execution_recovery_request(
            request.provenance.request_schema_id, request.provenance.canonical_request_bytes
        )
        prior_result = decode_execution_recovery_result(
            request.provenance.result_schema_id, request.provenance.canonical_result_bytes
        )
        validate_execution_recovery_exchange(prior_request, prior_result)
        if (
            prior_result.value.kind != "EXECUTION_RECOVERY_CLASSIFIED_V1"
            or prior_result.value.disposition != "TERMINAL_RECOVERY_FAULT"
            or getattr(prior_request.value, "run", None) != request.run
            or not isinstance(request.cut.frontier_observation, DecodedRecoveryFaultFrontierV1)
            or getattr(prior_request.value, "cut", None) != request.cut.frontier_observation.cut
        ):
            raise FaultRecoveryRecordIntegrityError(
                "prior classification does not bind this faulted Run"
            )
    else:
        captures = {item.capture for item in request.cut.ordered_observations}
        if request.provenance.trigger_capture not in captures:
            raise FaultRecoveryRecordIntegrityError(
                "direct diagnostic trigger is not a selected capture"
            )
