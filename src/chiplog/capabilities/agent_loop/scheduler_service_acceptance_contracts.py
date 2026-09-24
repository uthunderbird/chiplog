"""Inert, pre-intent service-acceptance preparation for scheduled calls.

This leaf deliberately stops before effects acquisition and final call acceptance.
It retains the loop-owned primitive and its one-call mandate debit only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Annotated, Final, Literal, Protocol, Self

from pydantic import Field, model_validator

from .call_acceptance_contracts import (
    CallDispatchSemantics,
    CallPreparationCut,
    CallPreparationRejected,
    CallSubjectHead,
    InitializedCallRecord,
    OriginalCallKey,
    SealedResponseRecord,
)
from .call_acceptance_preparation import call_record_reference, call_subject_id
from .contracts import SchedulerRootReference, ToolCall
from .execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    ExecutionPromptArtifact,
)
from .execution_history_contracts import (
    ExecutionContinueV3,
    ExecutionPromptArtifactV3,
    execution_response_adapter_v3,
)
from .execution_parsing import parse_execution_response
from .execution_run_versions import ExecutionRun
from .readonly_history_tool_contracts import ReadOnlyHistoryToolCall
from .recovery_contracts import (
    Digest,
    ExecutionLineageBinding,
    Identity,
    PhysicalRootBinding,
    Present,
    SchedulerExecutionFence,
    UInt64,
)
from .scheduler_execution_contracts import (
    PreparedScheduledExecutionInitializationV2,
    ProposedScheduledMandateConsumptionV1,
    ScheduledExecutionBindingV2,
    ScheduledToolPermissionV2,
    SchedulerExecutionDTO,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
)

SERVICE_PRIMITIVE_SCHEMA: Final = "chiplog.scheduler.service-acceptance-primitive.v1"
CURRENT_OBSERVATION_SCHEMA: Final = "chiplog.scheduler.service-current-observation.v1"
INITIALIZATION_SCHEMA: Final = "chiplog.scheduler.execution-initialization.v2"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise ValueError(reason)


def _selected_mandate_bytes(selected: SelectedScheduledSystemMandateV2) -> None:
    _require(
        selected.canonical_mandate_bytes,
        selected.mandate.canonical_bytes(),
        "selected mandate bytes differ from canonical mandate body",
    )
    _require(
        selected.mandate_head.fingerprint,
        _sha(selected.canonical_mandate_bytes),
        "selected mandate fingerprint differs from canonical mandate bytes",
    )


def _selected_budget_bytes(selected: SelectedScheduledMandateBudgetV1) -> None:
    _require(
        selected.canonical_budget_bytes,
        selected.budget.canonical_bytes(),
        "selected budget bytes differ from canonical budget body",
    )
    _require(
        selected.budget_head.fingerprint,
        _sha(selected.canonical_budget_bytes),
        "selected budget fingerprint differs from canonical budget bytes",
    )


def _canonical_base64(raw: str, reason: str) -> bytes:
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError(reason) from error
    if base64.b64encode(decoded).decode() != raw:
        raise ValueError(reason)
    return decoded


type CapturedToolCall = ToolCall | ConsequentialToolCall | ReadOnlyHistoryToolCall


def _captured_tool_calls(origin: ExecutionRun) -> tuple[CapturedToolCall, ...]:
    """Decode the selected native response without mounting a scheduler parser."""

    turn = origin.turns[-1]
    attempt = turn.attempts[turn.selector]
    if attempt.response_base64 is None:
        raise ValueError("call origin selected attempt has no captured response")
    raw = _canonical_base64(
        attempt.response_base64,
        "call origin response base64 is not strict canonical base64",
    )
    if origin.schema_id == "chiplog.agent-loop.execution-record.v2":
        if not isinstance(attempt.manifest.artifact, ExecutionPromptArtifact):
            raise ValueError("v2 origin artifact has the wrong schema")
        v2_response = parse_execution_response(raw, attempt.manifest.artifact)
        if not isinstance(v2_response, ExecutionContinue):
            raise ValueError("call origin response is not Continue")
        calls: tuple[CapturedToolCall, ...] = v2_response.tool_calls
    else:
        if not isinstance(attempt.manifest.artifact, ExecutionPromptArtifactV3):
            raise ValueError("v3 origin artifact has the wrong schema")
        ExecutionPromptArtifactV3.model_validate_json(attempt.manifest.artifact.canonical_bytes())
        v3_response = execution_response_adapter_v3().validate_json(raw)
        if v3_response.canonical_bytes() != raw:
            raise ValueError("v3 captured response bytes are not canonical")
        if not isinstance(v3_response, ExecutionContinueV3):
            raise ValueError("call origin response is not Continue")
        calls = v3_response.tool_calls
    if len(calls) > origin.policy.max_tool_calls:
        raise ValueError("call origin response exceeds tool-call bound")
    call_ids = tuple(call.call_id for call in calls)
    if len(set(call_ids)) != len(call_ids):
        raise ValueError("call origin response has duplicate call IDs")
    for call in calls:
        if isinstance(call, ConsequentialToolCall) and len(
            set(call.arguments.bundle_members)
        ) != len(call.arguments.bundle_members):
            raise ValueError("call origin response has duplicate consequential bundle members")
    return tuple(calls)


class RetainedScheduledRunV1(SchedulerExecutionDTO):
    """Exact selected native v2/v3 Run; selection remains broker-owned."""

    owner: Literal["agent_loop"] = "agent_loop"
    schema_id: Literal[
        "chiplog.agent-loop.execution-record.v2", "chiplog.agent-loop.execution-record.v3"
    ]
    reference: CallSubjectHead
    selected_decision: CallSubjectHead
    canonical_run_bytes: bytes = Field(min_length=1)
    run: ExecutionRun

    @model_validator(mode="after")
    def native_run_is_exact(self) -> Self:
        _require(self.schema_id, self.run.schema_id, "retained Run schema differs from native Run")
        _require(
            self.canonical_run_bytes,
            self.run.canonical_bytes(),
            "retained Run bytes differ from native Run",
        )
        _require(self.reference.subject_id, self.run.run_id, "retained Run subject differs")
        _require(self.reference.revision.head, self.run.head, "retained Run head differs")
        _require(
            self.run.head,
            "loop:" + self.run.model_copy(update={"head": "pending"}).digest(),
            "retained Run head is not native-derived",
        )
        _require(
            self.reference.revision.fingerprint,
            _sha(self.canonical_run_bytes),
            "retained Run fingerprint differs from native bytes",
        )
        return self


class ScheduledExecutionInitializationRecordV2(SchedulerExecutionDTO):
    """Physical scheduled-initialization body; its reference is outside the body."""

    schema_id: Literal["chiplog.scheduler.execution-initialization.v2"] = INITIALIZATION_SCHEMA
    source_request_fingerprint: Digest
    run: CallSubjectHead
    scheduled_binding: ScheduledExecutionBindingV2


def scheduled_execution_initialization_reference(
    record: ScheduledExecutionInitializationRecordV2,
) -> CallSubjectHead:
    raw = record.canonical_bytes()
    fingerprint = _sha(raw)
    return CallSubjectHead(
        subject_id=record.run.subject_id,
        revision=Present(
            head="scheduler-execution-initialization-v2:" + fingerprint,
            fingerprint=fingerprint,
        ),
    )


class RetainedScheduledInitializationV2(SchedulerExecutionDTO):
    """Selected physical row and its distinct original preparation exchange."""

    owner: Literal["agent_loop"] = "agent_loop"
    record_kind: Literal["scheduler.execution-initialization"] = (
        "scheduler.execution-initialization"
    )
    schema_id: Literal["chiplog.scheduler.execution-initialization.v2"] = INITIALIZATION_SCHEMA
    reference: CallSubjectHead
    selected_decision: CallSubjectHead
    canonical_initialization_bytes: bytes = Field(min_length=1)
    record: ScheduledExecutionInitializationRecordV2
    preparation: PreparedScheduledExecutionInitializationV2
    canonical_preparation_bytes: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def physical_and_preparation_bytes_are_distinct_exact_inputs(self) -> Self:
        _require(
            self.canonical_initialization_bytes,
            self.record.canonical_bytes(),
            "initialization bytes differ from physical record",
        )
        _require(
            self.reference,
            scheduled_execution_initialization_reference(self.record),
            "initialization reference differs from physical record",
        )
        _require(
            self.canonical_preparation_bytes,
            self.preparation.canonical_bytes(),
            "initialization preparation bytes differ from preparation",
        )
        _require(
            self.record.source_request_fingerprint,
            self.preparation.source_request_fingerprint,
            "initialization source request differs from preparation",
        )
        _require(
            self.record.scheduled_binding,
            self.preparation.scheduled_binding,
            "initialization binding differs from preparation",
        )
        return self


class InitializedScheduledToolV1(SchedulerExecutionDTO):
    """Registered tool identity/schema metadata retained with the initialized call."""

    tool_schema: CallSubjectHead
    tool_name: Identity
    tool_version: Identity
    schema_id: Identity


class RetainedScheduledInitializedCallV1(SchedulerExecutionDTO):
    owner: Literal["agent_loop"] = "agent_loop"
    reference: CallSubjectHead
    selected_decision: CallSubjectHead
    canonical_initialized_bytes: bytes = Field(min_length=1)
    record: InitializedCallRecord
    complete_ordered_initialized_records: tuple[InitializedCallRecord, ...] = Field(min_length=1)
    sealed_response_reference: CallSubjectHead
    canonical_sealed_response_bytes: bytes = Field(min_length=1)
    sealed_response: SealedResponseRecord

    @model_validator(mode="after")
    def selected_initialized_and_seal_are_exact(self) -> Self:
        initialized_hash = _sha(self.canonical_initialized_bytes)
        sealed_hash = _sha(self.canonical_sealed_response_bytes)
        _require(
            self.record.original_call_id,
            "call:" + self.record.call.original.digest(),
            "initialized call ID differs",
        )
        _require(
            self.canonical_initialized_bytes,
            self.record.canonical_bytes(),
            "initialized bytes differ",
        )
        _require(
            self.reference.subject_id, self.record.original_call_id, "initialized source differs"
        )
        _require(
            self.reference.revision,
            Present(head="record:" + initialized_hash, fingerprint=initialized_hash),
            "initialized reference differs",
        )
        _require(
            self.canonical_sealed_response_bytes,
            self.sealed_response.canonical_bytes(),
            "sealed response bytes differ",
        )
        _require(
            self.sealed_response_reference.subject_id,
            self.sealed_response.response_seal_id,
            "sealed response source differs",
        )
        _require(
            self.sealed_response_reference.revision,
            Present(head="record:" + sealed_hash, fingerprint=sealed_hash),
            "sealed response reference differs",
        )
        records = self.complete_ordered_initialized_records
        record_ids = tuple(record.original_call_id for record in records)
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("sealed initialized record identities duplicate")
        inventory = tuple(
            call_record_reference(record.original_call_id, record) for record in records
        )
        if inventory != self.sealed_response.complete_ordered_initialized:
            raise ValueError("sealed initialized inventory differs from retained records")
        if len(set(inventory)) != len(inventory):
            raise ValueError("sealed initialized inventory duplicates")
        ordinal = self.record.call.original.ordinal
        if (
            ordinal >= len(inventory)
            or records[ordinal] != self.record
            or inventory[ordinal] != self.reference
        ):
            raise ValueError("sealed response does not retain initialized call at ordinal")
        return self


class ScheduledCallOriginProvenanceV1(SchedulerExecutionDTO):
    schema_id: Literal["chiplog.scheduler.call-origin-provenance.v1"] = (
        "chiplog.scheduler.call-origin-provenance.v1"
    )
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: UInt64
    materialization_commitment: Digest
    scheduler_genesis_run: CallSubjectHead
    scheduler_initialization: CallSubjectHead
    genesis_selected_decision: CallSubjectHead
    call_origin_run: CallSubjectHead
    capture_selected_decision: CallSubjectHead
    initialized_call: CallSubjectHead
    sealed_response: CallSubjectHead
    initialization_selected_decision: CallSubjectHead
    current_run: CallSubjectHead
    current_run_selected_decision: CallSubjectHead
    current_lineage: ExecutionLineageBinding
    physical_root: PhysicalRootBinding


class RetainedScheduledCallOriginProvenanceV1(SchedulerExecutionDTO):
    source_owner: Literal["broker"] = "broker"
    reader_id: Literal["scheduled-call-origin-v1"] = "scheduled-call-origin-v1"
    schema_id: Literal["chiplog.scheduler.call-origin-provenance.v1"] = (
        "chiplog.scheduler.call-origin-provenance.v1"
    )
    source_reference: CallSubjectHead
    canonical_source_bytes: bytes = Field(min_length=1)
    observation: ScheduledCallOriginProvenanceV1

    @model_validator(mode="after")
    def source_reference_is_exact(self) -> Self:
        _require(
            self.canonical_source_bytes,
            self.observation.canonical_bytes(),
            "origin provenance bytes differ",
        )
        fingerprint = _sha(self.canonical_source_bytes)
        expected = Present(head="scheduler-call-origin-v1:" + fingerprint, fingerprint=fingerprint)
        _require(self.source_reference.revision, expected, "origin provenance reference differs")
        return self


def validate_scheduled_call_origin_sources(
    *,
    command_id: Identity,
    scheduler_genesis_run: RetainedScheduledRunV1,
    scheduler_initialization: RetainedScheduledInitializationV2,
    call_origin_run: RetainedScheduledRunV1,
    initialized_source: RetainedScheduledInitializedCallV1,
    origin_provenance: RetainedScheduledCallOriginProvenanceV1,
    current_run: RetainedScheduledRunV1,
    selected_mandate: SelectedScheduledSystemMandateV2,
    initialized_tool: InitializedScheduledToolV1,
    cut: CallPreparationCut,
) -> None:
    """Compare closed retained source representations; broker authentication is external."""

    if not isinstance(cut.fence, SchedulerExecutionFence):
        raise ValueError("scheduled call origin requires scheduler fence")
    fence = cut.fence
    genesis = scheduler_genesis_run.run
    origin = call_origin_run.run
    current = current_run.run
    if genesis.state != "CREATED":
        raise ValueError("scheduler genesis Run is not CREATED")
    if origin.state != "ACTIVE" or origin.event != "ModelResponseCaptured":
        raise ValueError("call origin Run is not an active response capture")
    if current.state != "ACTIVE":
        raise ValueError("current scheduled Run is not ACTIVE")
    genesis_root = genesis.root_binding
    origin_root = origin.root_binding
    current_root = current.root_binding
    if not all(
        isinstance(value, SchedulerRootReference)
        for value in (genesis_root, origin_root, current_root)
    ):
        raise ValueError("scheduled source Run lacks scheduler root binding")
    assert isinstance(genesis_root, SchedulerRootReference)
    assert isinstance(origin_root, SchedulerRootReference)
    assert isinstance(current_root, SchedulerRootReference)
    _require(
        genesis.run_id, genesis_root.initial_run_id, "genesis Run differs from root initial Run"
    )
    _require(genesis_root, origin_root, "genesis and call origin roots differ")
    _require(genesis_root, current_root, "genesis and current roots differ")
    scope = selected_mandate.mandate.scope
    for run in (genesis, origin, current):
        _require(run.tenant, scope.tenant_id, "scheduled source Run tenant differs from mandate")
        _require(
            run.principal,
            scope.beneficiary_principal,
            "scheduled source Run principal differs from mandate",
        )
    _require(
        scheduler_initialization.record.run,
        scheduler_genesis_run.reference,
        "initialization genesis Run differs",
    )
    _require(scheduler_initialization.preparation.run, genesis, "initialization body Run differs")
    _require(
        scheduler_initialization.selected_decision,
        scheduler_genesis_run.selected_decision,
        "genesis selection differs",
    )
    _require(
        scheduler_initialization.record.scheduled_binding.mandate_head,
        selected_mandate.mandate_head,
        "initialization mandate differs",
    )
    _require(
        scheduler_initialization.record.scheduled_binding.service_identity,
        scope.service_identity,
        "initialization service differs",
    )
    if not origin.turns:
        raise ValueError("call origin has no captured turn")
    turn = origin.turns[-1]
    if (
        turn.state != "RESPONSE_AVAILABLE"
        or turn.response_seal is not None
        or turn.initialized_calls is not None
    ):
        raise ValueError("call origin turn is not pre-seal response available")
    if not turn.attempts or turn.selector != len(turn.attempts) - 1:
        raise ValueError("call origin has no selected captured attempt")
    attempt = turn.attempts[turn.selector]
    if attempt.state != "RESPONSE_CAPTURED" or attempt.generation != turn.selector:
        raise ValueError("call origin selected attempt is not captured")
    original = initialized_source.record.call.original
    _require(original.original_run_id, origin.run_id, "initialized call origin Run differs")
    _require(original.original_turn_id, turn.turn_id, "initialized call origin turn differs")
    _require(
        original.captured_response,
        call_origin_run.reference,
        "initialized call captured response differs",
    )
    _require(
        initialized_source.record.original_call_id,
        call_subject_id(original),
        "initialized call ID differs from original key",
    )
    calls = _captured_tool_calls(origin)
    records = initialized_source.complete_ordered_initialized_records
    _require(
        initialized_source.canonical_initialized_bytes,
        initialized_source.record.canonical_bytes(),
        "initialized source bytes differ from retained record",
    )
    _require(
        initialized_source.reference,
        call_record_reference(
            initialized_source.record.original_call_id,
            initialized_source.record,
        ),
        "initialized source reference differs from retained record",
    )
    _require(
        initialized_source.canonical_sealed_response_bytes,
        initialized_source.sealed_response.canonical_bytes(),
        "sealed response bytes differ from retained record",
    )
    _require(
        initialized_source.sealed_response_reference,
        call_record_reference(
            initialized_source.sealed_response.response_seal_id,
            initialized_source.sealed_response,
        ),
        "sealed response reference differs from retained record",
    )
    _require(
        len(records),
        len(calls),
        "retained initialized record count differs from captured response",
    )
    expected_inventory = tuple(
        call_record_reference(record.original_call_id, record) for record in records
    )
    _require(
        initialized_source.sealed_response.complete_ordered_initialized,
        expected_inventory,
        "sealed response inventory differs from retained initialized records",
    )
    if original.ordinal >= len(records):
        raise ValueError("initialized call ordinal is outside retained inventory")
    _require(
        records[original.ordinal],
        initialized_source.record,
        "target initialized record differs from retained inventory",
    )
    _require(
        expected_inventory[original.ordinal],
        initialized_source.reference,
        "target initialized reference differs from retained inventory",
    )
    for ordinal, (parsed, record) in enumerate(zip(calls, records, strict=True)):
        parsed_original = record.call.original
        _require(
            record.original_call_id,
            call_subject_id(parsed_original),
            "initialized call ID differs from original key",
        )
        _require(
            parsed_original.tenant_id,
            origin.tenant,
            "initialized call tenant differs from captured response",
        )
        _require(
            parsed_original.original_run_id,
            origin.run_id,
            "initialized call origin Run differs from captured response",
        )
        _require(
            parsed_original.original_turn_id,
            turn.turn_id,
            "initialized call origin turn differs from captured response",
        )
        _require(
            parsed_original.captured_response,
            call_origin_run.reference,
            "initialized call captured response differs from captured response",
        )
        _require(
            parsed_original.ordinal,
            ordinal,
            "initialized call ordinal differs from captured response",
        )
        _require(
            parsed_original.model_call_label,
            parsed.call_id,
            "initialized call label differs from captured response",
        )
        _require(
            record.call.canonical_call_base64,
            base64.b64encode(parsed.canonical_bytes()).decode(),
            "initialized call bytes differ from captured response",
        )
        expected_classification = (
            "CONSEQUENTIAL"
            if isinstance(parsed, ConsequentialToolCall)
            else "READ_ONLY"
            if parsed.tool == "read_conversation_history"
            else "PROPOSAL_ONLY"
        )
        _require(
            record.call.classification,
            expected_classification,
            "initialized call classification differs from captured tool",
        )
    if original.ordinal >= len(calls):
        raise ValueError("initialized call ordinal is outside captured response")
    parsed_call = calls[original.ordinal]
    _require(
        original.model_call_label,
        parsed_call.call_id,
        "initialized call label differs from captured response",
    )
    if not isinstance(parsed_call, ConsequentialToolCall):
        raise ValueError("initialized call is not a captured consequential tool call")
    sealed_call = initialized_source.record.call
    _require(
        parsed_call.tool,
        initialized_tool.tool_name,
        "initialized tool name differs from captured tool",
    )
    _require(
        initialized_tool.tool_version,
        "2",
        "initialized tool version differs from captured tool",
    )
    _require(
        initialized_tool.schema_id,
        "chiplog.request-self-effect.v2",
        "initialized tool schema differs from captured tool",
    )
    _require(
        sealed_call.tool_schema,
        initialized_tool.tool_schema,
        "initialized call schema reference differs from tool metadata",
    )
    _require(
        sealed_call.classification,
        "CONSEQUENTIAL",
        "initialized call classification differs from captured tool",
    )
    _require(
        sealed_call.canonical_call_base64,
        base64.b64encode(parsed_call.canonical_bytes()).decode(),
        "initialized call bytes differ from captured response",
    )
    _canonical_base64(
        sealed_call.canonical_call_base64,
        "initialized call bytes are not strict canonical base64",
    )
    seal = initialized_source.sealed_response
    _require(seal.tenant_id, origin.tenant, "sealed response tenant differs")
    _require(seal.original_run_id, origin.run_id, "sealed response origin Run differs")
    _require(seal.original_turn_id, turn.turn_id, "sealed response origin turn differs")
    _require(seal.captured_response, call_origin_run.reference, "sealed response capture differs")
    _require(
        len(seal.complete_ordered_initialized),
        len(calls),
        "sealed response inventory length differs from captured response",
    )
    provenance = origin_provenance.observation
    _require(provenance.tenant_id, cut.tenant_id, "provenance tenant differs from cut")
    _require(provenance.tenant_id, scope.tenant_id, "provenance tenant differs from mandate")
    _require(provenance.tenant_id, genesis.tenant, "provenance tenant differs from genesis")
    _require(provenance.tenant_id, origin.tenant, "provenance tenant differs from origin")
    _require(provenance.tenant_id, current.tenant, "provenance tenant differs from current")
    _require(
        provenance.tenant_commit_sequence,
        cut.tenant_commit_sequence,
        "provenance sequence differs from cut",
    )
    _require(
        provenance.materialization_commitment,
        cut.materialization_commitment,
        "provenance materialization differs from cut",
    )
    _require(
        origin_provenance.source_reference.subject_id, command_id, "provenance command differs"
    )
    _require(
        provenance.scheduler_genesis_run,
        scheduler_genesis_run.reference,
        "provenance genesis differs",
    )
    _require(
        provenance.scheduler_initialization,
        scheduler_initialization.reference,
        "provenance initialization differs",
    )
    _require(
        provenance.genesis_selected_decision,
        scheduler_genesis_run.selected_decision,
        "provenance genesis decision differs",
    )
    _require(provenance.call_origin_run, call_origin_run.reference, "provenance origin differs")
    _require(
        provenance.capture_selected_decision,
        call_origin_run.selected_decision,
        "provenance capture decision differs",
    )
    _require(
        provenance.initialized_call, initialized_source.reference, "provenance initialized differs"
    )
    _require(
        provenance.sealed_response,
        initialized_source.sealed_response_reference,
        "provenance seal differs",
    )
    _require(
        provenance.initialization_selected_decision,
        initialized_source.selected_decision,
        "provenance initialized decision differs",
    )
    _require(provenance.current_run, current_run.reference, "provenance current Run differs")
    _require(
        provenance.current_run_selected_decision,
        current_run.selected_decision,
        "provenance current decision differs",
    )
    _require(cut.current_run, current_run.reference, "cut current Run differs")
    _require(fence.run_head, current_run.reference.revision.head, "fence current Run head differs")
    _require(
        fence.lineage.current_run_id,
        current_run.reference.subject_id,
        "fence current Run ID differs",
    )
    _require(provenance.current_lineage, fence.lineage, "provenance current lineage differs")
    _require(provenance.physical_root, fence.physical_root, "provenance physical root differs")
    _require(genesis_root.root_id, fence.lineage.root_id, "scheduler root ID differs")
    _require(
        genesis_root.root_fingerprint,
        fence.lineage.root_fingerprint,
        "scheduler root fingerprint differs",
    )
    _require(
        genesis_root.initial_run_id,
        fence.lineage.initial_run_id,
        "scheduler root initial Run differs",
    )


class ScheduledServiceAcceptancePrimitiveV1(SchedulerExecutionDTO):
    """Acyclic loop-owned pre-intent primitive; no effects/final outputs occur here."""

    schema_id: Literal["chiplog.scheduler.service-acceptance-primitive.v1"] = (
        SERVICE_PRIMITIVE_SCHEMA
    )
    command_id: Identity
    database_id: Identity
    original_call_id: Identity
    original: OriginalCallKey
    initialized: CallSubjectHead
    initialized_record: InitializedCallRecord
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    initialized_tool: InitializedScheduledToolV1
    selected_tool_permission: ScheduledToolPermissionV2
    dispatch_semantics: CallDispatchSemantics
    cut: CallPreparationCut
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget_predecessor: SelectedScheduledMandateBudgetV1
    current_observation: CallSubjectHead
    scheduler_genesis_run: CallSubjectHead
    scheduler_initialization: CallSubjectHead
    call_origin_run: CallSubjectHead
    origin_provenance: CallSubjectHead

    @model_validator(mode="after")
    def stays_on_scheduled_pre_intent_branch(self) -> Self:
        if not isinstance(self.cut.fence, SchedulerExecutionFence):
            raise ValueError("service acceptance primitive requires scheduler execution fence")
        _require(
            self.original_call_id,
            self.initialized_record.original_call_id,
            "primitive call differs",
        )
        _require(self.original, self.initialized_record.call.original, "primitive original differs")
        _require(
            self.tool_schema,
            self.initialized_record.call.tool_schema,
            "primitive tool schema differs",
        )
        _require(
            self.tool_policy,
            self.initialized_record.call.tool_policy,
            "primitive tool policy differs",
        )
        _require(
            self.initialized_tool.tool_schema,
            self.tool_schema,
            "initialized tool metadata schema reference differs",
        )
        permission = self.selected_tool_permission
        _require(
            permission.tool_name,
            self.initialized_tool.tool_name,
            "tool permission name differs",
        )
        _require(
            permission.tool_version,
            self.initialized_tool.tool_version,
            "tool permission version differs",
        )
        _require(
            permission.schema_id,
            self.initialized_tool.schema_id,
            "tool permission schema differs",
        )
        if not permission.consequential:
            raise ValueError("service acceptance tool permission is not consequential")
        if self.selected_mandate.mandate.scope.permitted_tools.count(permission) != 1:
            raise ValueError("service acceptance tool permission is not uniquely mandated")
        if self.initialized_record.call.classification != "CONSEQUENTIAL":
            raise ValueError("service acceptance primitive requires consequential initialized call")
        _require(
            self.selected_budget_predecessor.budget.mandate_id,
            self.selected_mandate.mandate.scope.mandate_id,
            "primitive budget belongs to another mandate",
        )
        return self


def scheduled_service_primitive_reference(
    primitive: ScheduledServiceAcceptancePrimitiveV1,
) -> CallSubjectHead:
    raw = primitive.canonical_bytes()
    fingerprint = _sha(raw)
    return CallSubjectHead(
        subject_id=primitive.original_call_id,
        revision=Present(
            head="scheduler-service-acceptance-primitive-v1:" + fingerprint,
            fingerprint=fingerprint,
        ),
    )


class PrepareScheduledServiceAcceptancePrimitiveV1(SchedulerExecutionDTO):
    kind: Literal["PREPARE_SCHEDULED_SERVICE_ACCEPTANCE_PRIMITIVE_V1"] = (
        "PREPARE_SCHEDULED_SERVICE_ACCEPTANCE_PRIMITIVE_V1"
    )
    command_id: Identity
    database_id: Identity
    initialized: CallSubjectHead
    initialized_record: InitializedCallRecord
    scheduler_genesis_run: RetainedScheduledRunV1
    scheduler_initialization: RetainedScheduledInitializationV2
    call_origin_run: RetainedScheduledRunV1
    initialized_source: RetainedScheduledInitializedCallV1
    origin_provenance: RetainedScheduledCallOriginProvenanceV1
    current_run: RetainedScheduledRunV1
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget_predecessor: SelectedScheduledMandateBudgetV1
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    initialized_tool: InitializedScheduledToolV1
    selected_tool_permission: ScheduledToolPermissionV2
    dispatch_semantics: CallDispatchSemantics
    cut: CallPreparationCut
    current_observation_schema: Literal["chiplog.scheduler.service-current-observation.v1"] = (
        CURRENT_OBSERVATION_SCHEMA
    )
    canonical_current_observation_bytes: bytes = Field(min_length=1)
    current_observation: CallSubjectHead

    @model_validator(mode="after")
    def retains_exact_scheduled_origin_and_current_cut(self) -> Self:
        primitive = ScheduledServiceAcceptancePrimitiveV1(
            command_id=self.command_id,
            database_id=self.database_id,
            original_call_id=self.initialized_record.original_call_id,
            original=self.initialized_record.call.original,
            initialized=self.initialized,
            initialized_record=self.initialized_record,
            tool_schema=self.tool_schema,
            tool_policy=self.tool_policy,
            initialized_tool=self.initialized_tool,
            selected_tool_permission=self.selected_tool_permission,
            dispatch_semantics=self.dispatch_semantics,
            cut=self.cut,
            selected_mandate=self.selected_mandate,
            selected_budget_predecessor=self.selected_budget_predecessor,
            current_observation=self.current_observation,
            scheduler_genesis_run=self.scheduler_genesis_run.reference,
            scheduler_initialization=self.scheduler_initialization.reference,
            call_origin_run=self.call_origin_run.reference,
            origin_provenance=self.origin_provenance.source_reference,
        )
        expected_initialized = CallSubjectHead(
            subject_id=self.initialized_record.original_call_id,
            revision=Present(
                head="record:" + _sha(self.initialized_record.canonical_bytes()),
                fingerprint=_sha(self.initialized_record.canonical_bytes()),
            ),
        )
        _require(self.initialized, expected_initialized, "selected initialized head differs")
        validate_scheduled_call_origin_sources(
            command_id=self.command_id,
            scheduler_genesis_run=self.scheduler_genesis_run,
            scheduler_initialization=self.scheduler_initialization,
            call_origin_run=self.call_origin_run,
            initialized_source=self.initialized_source,
            origin_provenance=self.origin_provenance,
            current_run=self.current_run,
            selected_mandate=self.selected_mandate,
            initialized_tool=self.initialized_tool,
            cut=self.cut,
        )
        _require(
            self.origin_provenance.observation.database_id,
            self.database_id,
            "provenance database differs from request",
        )
        _require(self.initialized, self.initialized_source.reference, "initialized source differs")
        _require(
            self.initialized_record, self.initialized_source.record, "initialized record differs"
        )
        _selected_mandate_bytes(self.selected_mandate)
        _selected_budget_bytes(self.selected_budget_predecessor)
        fence = primitive.cut.fence
        assert isinstance(fence, SchedulerExecutionFence)
        observation_fingerprint = _sha(self.canonical_current_observation_bytes)
        _require(
            self.current_observation.subject_id,
            self.command_id,
            "current observation command differs",
        )
        _require(
            self.current_observation.revision.head,
            "scheduler-service-current-v1:" + observation_fingerprint,
            "current observation head differs from bytes",
        )
        _require(
            self.current_observation.revision.fingerprint,
            observation_fingerprint,
            "current observation fingerprint differs from bytes",
        )
        _require(
            primitive.cut.current_run.subject_id,
            fence.lineage.current_run_id,
            "cut current Run differs from scheduler lineage",
        )
        _require(
            primitive.cut.current_run.revision.head,
            fence.run_head,
            "cut current Run differs from scheduler fence",
        )
        return self


def scheduled_service_request_fingerprint(
    request: PrepareScheduledServiceAcceptancePrimitiveV1,
) -> Digest:
    return _sha(SERVICE_PRIMITIVE_SCHEMA.encode() + b"\x00" + request.canonical_bytes())


def validate_scheduled_service_request_context(
    request: PrepareScheduledServiceAcceptancePrimitiveV1,
    *,
    expected_tenant_id: Identity,
    expected_database_id: Identity,
) -> None:
    """Broker-admission context join; expected values must come from the caller's source."""

    request = PrepareScheduledServiceAcceptancePrimitiveV1.model_validate_json(
        request.canonical_bytes()
    )
    _require(
        request.cut.tenant_id, expected_tenant_id, "request tenant differs from expected tenant"
    )
    _require(
        request.database_id,
        expected_database_id,
        "request database differs from expected database",
    )


def _validate_debit(
    primitive: ScheduledServiceAcceptancePrimitiveV1,
    proposal: ProposedScheduledMandateConsumptionV1,
) -> None:
    predecessor = primitive.selected_budget_predecessor
    successor = proposal.successor.budget
    basis = proposal.basis
    reference = scheduled_service_primitive_reference(primitive)
    _selected_mandate_bytes(primitive.selected_mandate)
    _selected_budget_bytes(predecessor)
    _require(proposal.selected_predecessor, predecessor, "debit selected predecessor differs")
    _require(basis.selected_predecessor, predecessor, "debit basis predecessor differs")
    _require(basis.primitive_command, reference, "debit primitive reference differs")
    _require(
        basis.primitive_fingerprint, reference.revision.fingerprint, "debit primitive hash differs"
    )
    _require(
        proposal.successor.canonical_budget_bytes,
        successor.canonical_bytes(),
        "debit successor bytes differ",
    )
    _require(successor.consumption_basis, basis, "debit successor basis differs")
    _require(successor.mandate_id, predecessor.budget.mandate_id, "debit successor mandate differs")
    _require(successor.predecessor, predecessor.budget_head, "debit successor predecessor differs")
    values = (
        predecessor.budget.generation,
        predecessor.budget.cumulative_cycles,
        predecessor.budget.cumulative_runs,
        predecessor.budget.cumulative_consequential_calls,
        successor.generation,
        successor.cumulative_cycles,
        successor.cumulative_runs,
        successor.cumulative_consequential_calls,
        basis.delta_cycles,
        basis.delta_runs,
        basis.delta_consequential_calls,
    )
    if any(type(value) is not int for value in values):
        raise ValueError("debit counters must be actual integers")
    _require(
        (basis.delta_cycles, basis.delta_runs, basis.delta_consequential_calls),
        (0, 0, 1),
        "service acceptance debit must be exact (0, 0, 1)",
    )
    if predecessor.budget.generation == 2**64 - 1:
        raise ValueError("service acceptance debit generation overflows UInt64")
    _require(successor.generation, predecessor.budget.generation + 1, "debit generation differs")
    _require(
        successor.cumulative_cycles, predecessor.budget.cumulative_cycles, "debit cycles differ"
    )
    _require(successor.cumulative_runs, predecessor.budget.cumulative_runs, "debit runs differ")
    _require(
        successor.cumulative_consequential_calls,
        predecessor.budget.cumulative_consequential_calls + 1,
        "debit consequential calls differ",
    )
    horizon = primitive.selected_mandate.mandate.scope.horizon
    if (
        successor.cumulative_cycles > horizon.max_cycles
        or successor.cumulative_runs > horizon.max_runs
        or successor.cumulative_consequential_calls > horizon.max_consequential_calls
    ):
        raise ValueError("service acceptance debit exceeds mandate capacity")
    if "INITIALIZED_CONSEQUENTIAL_CALL" not in primitive.selected_mandate.mandate.scope.operations:
        raise ValueError("mandate does not permit initialized consequential calls")


class PreparedScheduledServiceAcceptancePrimitiveV1(SchedulerExecutionDTO):
    kind: Literal["PREPARED_SCHEDULED_SERVICE_ACCEPTANCE_PRIMITIVE_V1"] = (
        "PREPARED_SCHEDULED_SERVICE_ACCEPTANCE_PRIMITIVE_V1"
    )
    source_request_fingerprint: Digest
    primitive: ScheduledServiceAcceptancePrimitiveV1
    canonical_primitive_bytes: bytes = Field(min_length=1)
    primitive_reference: CallSubjectHead
    proposed_call_debit: ProposedScheduledMandateConsumptionV1

    @model_validator(mode="after")
    def primitive_and_debit_are_exact(self) -> Self:
        _require(
            self.canonical_primitive_bytes,
            self.primitive.canonical_bytes(),
            "prepared primitive bytes differ from primitive",
        )
        _require(
            self.primitive_reference,
            scheduled_service_primitive_reference(self.primitive),
            "prepared primitive reference differs from primitive",
        )
        _validate_debit(self.primitive, self.proposed_call_debit)
        return self


def validate_scheduled_service_acceptance_preparation(
    request: PrepareScheduledServiceAcceptancePrimitiveV1,
    prepared: PreparedScheduledServiceAcceptancePrimitiveV1,
    *,
    expected_tenant_id: Identity,
    expected_database_id: Identity,
) -> None:
    """Join a request and its prepared primitive without granting publication authority."""

    request = PrepareScheduledServiceAcceptancePrimitiveV1.model_validate_json(
        request.canonical_bytes()
    )
    prepared = PreparedScheduledServiceAcceptancePrimitiveV1.model_validate_json(
        prepared.canonical_bytes()
    )
    _require(
        prepared.source_request_fingerprint,
        scheduled_service_request_fingerprint(request),
        "prepared source request fingerprint differs",
    )
    primitive = prepared.primitive
    validate_scheduled_service_request_context(
        request,
        expected_tenant_id=expected_tenant_id,
        expected_database_id=expected_database_id,
    )
    _require(primitive.database_id, request.database_id, "primitive database differs from request")
    _require(primitive.command_id, request.command_id, "primitive command differs")
    _require(
        primitive.original_call_id,
        request.initialized_record.original_call_id,
        "primitive call differs",
    )
    _require(
        primitive.original, request.initialized_record.call.original, "primitive original differs"
    )
    _require(primitive.initialized, request.initialized, "primitive initialized differs")
    _require(
        primitive.initialized_record,
        request.initialized_record,
        "primitive initialized record differs",
    )
    _require(primitive.tool_schema, request.tool_schema, "primitive tool schema differs")
    _require(primitive.tool_policy, request.tool_policy, "primitive tool policy differs")
    _require(
        primitive.initialized_tool,
        request.initialized_tool,
        "primitive tool metadata differs",
    )
    _require(
        primitive.selected_tool_permission,
        request.selected_tool_permission,
        "primitive tool permission differs",
    )
    _require(primitive.dispatch_semantics, request.dispatch_semantics, "primitive semantics differ")
    _require(primitive.cut, request.cut, "primitive cut differs")
    _require(primitive.selected_mandate, request.selected_mandate, "primitive mandate differs")
    _require(
        primitive.selected_budget_predecessor,
        request.selected_budget_predecessor,
        "primitive budget predecessor differs",
    )
    _require(
        primitive.current_observation,
        request.current_observation,
        "primitive current source differs",
    )
    _require(
        primitive.scheduler_genesis_run,
        request.scheduler_genesis_run.reference,
        "primitive genesis source differs",
    )
    _require(
        primitive.scheduler_initialization,
        request.scheduler_initialization.reference,
        "primitive initialization source differs",
    )
    _require(
        primitive.call_origin_run,
        request.call_origin_run.reference,
        "primitive origin source differs",
    )
    _require(
        primitive.origin_provenance,
        request.origin_provenance.source_reference,
        "primitive provenance source differs",
    )
    _validate_debit(primitive, prepared.proposed_call_debit)


ScheduledServiceAcceptancePreparationResult = Annotated[
    PreparedScheduledServiceAcceptancePrimitiveV1 | CallPreparationRejected,
    Field(discriminator="kind"),
]


class ScheduledServiceAcceptancePreparationPort(Protocol):
    async def prepare_service_acceptance(
        self, request: PrepareScheduledServiceAcceptancePrimitiveV1
    ) -> PreparedScheduledServiceAcceptancePrimitiveV1 | CallPreparationRejected: ...
