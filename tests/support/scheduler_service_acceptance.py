"""Native v2/v3 scheduled service-acceptance fixtures."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallDispatchSemantics,
    CallInventorySnapshot,
    CallPreparationCut,
    CallSubjectHead,
    InitializedCallRecord,
    OriginalCallKey,
    SealedCallInput,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    call_subject_id,
)
from chiplog.capabilities.agent_loop.contracts import SchedulerRootReference, ToolCall
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionContinueV3
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    PreparedScheduledExecutionInitializationV2,
    ProposedScheduledMandateBudgetV1,
    ProposedScheduledMandateConsumptionV1,
    ScheduledExecutionBindingV2,
    ScheduledMandateBudgetV1,
    ScheduledMandateConsumptionBasisV1,
    ScheduledToolPermissionV2,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_service_acceptance_contracts import (
    InitializedScheduledToolV1,
    PreparedScheduledServiceAcceptancePrimitiveV1,
    PrepareScheduledServiceAcceptancePrimitiveV1,
    RetainedScheduledCallOriginProvenanceV1,
    RetainedScheduledInitializationV2,
    RetainedScheduledInitializedCallV1,
    RetainedScheduledRunV1,
    ScheduledCallOriginProvenanceV1,
    ScheduledExecutionInitializationRecordV2,
    ScheduledServiceAcceptancePrimitiveV1,
    scheduled_execution_initialization_reference,
    scheduled_service_primitive_reference,
    scheduled_service_request_fingerprint,
)
from tests.support.execution_fan_out import fixture as captured_fan_out_v2
from tests.support.execution_versions import execution_run_v3
from tests.support.scheduler_execution import call_head, mandate, ordinary_seed_batch
from tests.support.successor_records import scheduler_successor


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(subject_id: str, head: str, raw: bytes) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id,
        revision=Present(head=head, fingerprint=_sha(raw)),
    )


def _source() -> CallAuthorityObservation:
    return CallAuthorityObservation(
        source_id="source",
        family="MANDATE",
        source=call_head("source"),
        generation="generation",
        frontier="frontier",
        canonical_value_base64=base64.b64encode(b"source").decode(),
        observed_at_ns=1,
        valid_until_ns=2,
    )


def _selected_mandate(
    max_consequential_calls: int, beneficiary_principal: str
) -> SelectedScheduledSystemMandateV2:
    old = mandate()
    scope = old.mandate.scope.model_copy(
        update={
            "beneficiary_principal": beneficiary_principal,
            "operations": ("scheduler.execute_interval", "INITIALIZED_CONSEQUENTIAL_CALL"),
            "permitted_tools": (
                ScheduledToolPermissionV2(
                    tool_name="request_self_effect",
                    tool_version="2",
                    schema_id="chiplog.request-self-effect.v2",
                    consequential=True,
                ),
            ),
            "horizon": old.mandate.scope.horizon.model_copy(
                update={
                    "max_cycles": 2,
                    "max_runs": 2,
                    "max_consequential_calls": max_consequential_calls,
                }
            ),
        }
    )
    body = old.mandate.model_copy(update={"scope": scope})
    raw = body.canonical_bytes()
    return SelectedScheduledSystemMandateV2(
        mandate_head=Present(head="mandate/head", fingerprint=_sha(raw)),
        canonical_mandate_bytes=raw,
        mandate=body,
        issuance_observation=old.issuance_observation,
    )


def _selected_budget(
    selected: SelectedScheduledSystemMandateV2, generation: int = 0
) -> SelectedScheduledMandateBudgetV1:
    basis = ScheduledMandateConsumptionBasisV1(
        mandate_id=selected.mandate.scope.mandate_id,
        selected_predecessor=Absent(),
        primitive_command=call_head("genesis-primitive"),
        primitive_fingerprint="a" * 64,
        delta_cycles=0,
        delta_runs=0,
        delta_consequential_calls=0,
        accounting_policy_version="scheduler-accounting.v1",
    )
    body = ScheduledMandateBudgetV1(
        mandate_id=selected.mandate.scope.mandate_id,
        generation=generation,
        predecessor=Absent(),
        cumulative_cycles=0,
        cumulative_runs=0,
        cumulative_consequential_calls=0,
        consumption_basis=basis,
    )
    raw = body.canonical_bytes()
    return SelectedScheduledMandateBudgetV1(
        budget_head=Present(head="budget/head", fingerprint=_sha(raw)),
        canonical_budget_bytes=raw,
        budget=body,
    )


@dataclass(frozen=True)
class ServiceAcceptanceFixture:
    request: PrepareScheduledServiceAcceptancePrimitiveV1
    prepared: PreparedScheduledServiceAcceptancePrimitiveV1


async def _native_capture(
    version: Literal["v2", "v3"],
    *,
    run_id: str,
    root_binding: SchedulerRootReference,
) -> tuple[ExecutionRun, tuple[SealedCallInput, ...]]:
    if version == "v2":
        captured = await captured_fan_out_v2(complete=False)
        pending = captured.captured_run.model_copy(
            update={"head": "pending", "run_id": run_id, "root_binding": root_binding}
        )
        run = pending.model_copy(update={"head": "loop:" + pending.digest()})
        reference = _head(run.run_id, run.head, run.canonical_bytes())
        calls = tuple(
            item.model_copy(
                update={
                    "original": item.original.model_copy(
                        update={
                            "original_run_id": run.run_id,
                            "original_turn_id": run.turns[-1].turn_id,
                            "captured_response": reference,
                        }
                    )
                }
            )
            for item in captured.request.ordered_calls
        )
        return run, calls

    response = ExecutionContinueV3(
        kind="Continue",
        tool_calls=(
            ToolCall(call_id="proposal", tool="propose_intent", text="idea"),
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"\x00", bundle_members=("member",)),
            ),
            ReadOnlyHistoryToolCall(
                call_id="history",
                tool="read_conversation_history",
                arguments=ConversationHistoryQuery(limit=1, after_cursor=None),
            ),
        ),
    )
    base = execution_run_v3()
    turn = base.turns[-1]
    attempt = turn.attempts[turn.selector].model_copy(
        update={"response_base64": base64.b64encode(response.canonical_bytes()).decode()}
    )
    turn = turn.model_copy(update={"attempts": (attempt,)})
    v3_pending = base.model_copy(
        update={
            "head": "pending",
            "run_id": run_id,
            "root_binding": root_binding,
            "turns": (turn,),
        }
    )
    v3_run = v3_pending.model_copy(update={"head": "loop:" + v3_pending.digest()})
    reference = _head(v3_run.run_id, v3_run.head, v3_run.canonical_bytes())
    calls = tuple(
        SealedCallInput(
            original=OriginalCallKey(
                tenant_id=v3_run.tenant,
                original_run_id=v3_run.run_id,
                original_turn_id=v3_run.turns[-1].turn_id,
                captured_response=reference,
                ordinal=ordinal,
                model_call_label=call.call_id,
            ),
            classification=(
                "CONSEQUENTIAL"
                if isinstance(call, ConsequentialToolCall)
                else "READ_ONLY"
                if isinstance(call, ReadOnlyHistoryToolCall)
                else "PROPOSAL_ONLY"
            ),
            tool_schema=call_head("tool-schema:" + call.tool),
            tool_policy=call_head("tool-policy:" + call.tool),
            canonical_call_base64=base64.b64encode(call.canonical_bytes()).decode(),
            retry_lineage=NotApplicable(),
        )
        for ordinal, call in enumerate(response.tool_calls)
    )
    return v3_run, calls


async def fixture(
    version: Literal["v2", "v3"],
    *,
    history: Literal["GENESIS", "BORN_B", "INHERITED_A_B", "INHERITED_B_C"] = "GENESIS",
    max_consequential_calls: int = 2,
    predecessor_generation: int = 0,
) -> ServiceAcceptanceFixture:
    scheduled_successor = await scheduler_successor(version)
    fence = scheduled_successor.request.fence
    assert fence.kind == "EXECUTION_ROOT_LIVE_LEASE"
    original_pending = type(scheduled_successor.request.run).model_validate(
        scheduled_successor.request.run.model_dump()
        | {"state": "CREATED", "head": "pending", "predecessor": None}
    )
    original_body = original_pending.model_copy(
        update={"head": "loop:" + original_pending.digest()}
    )
    original_raw = original_body.canonical_bytes()
    original = RetainedScheduledRunV1(
        schema_id=original_body.schema_id,
        reference=_head(original_body.run_id, original_body.head, original_raw),
        selected_decision=call_head("scheduled-selection"),
        canonical_run_bytes=original_raw,
        run=original_body,
    )
    origin_id = original_body.run_id if history in {"GENESIS", "INHERITED_A_B"} else "run-b"
    current_id = {
        "GENESIS": origin_id,
        "BORN_B": origin_id,
        "INHERITED_A_B": "run-b",
        "INHERITED_B_C": "run-c",
    }[history]
    assert isinstance(original_body.root_binding, SchedulerRootReference)
    origin_body, native_calls = await _native_capture(
        version,
        run_id=origin_id,
        root_binding=original_body.root_binding,
    )
    origin_raw = origin_body.canonical_bytes()
    origin = RetainedScheduledRunV1(
        schema_id=origin_body.schema_id,
        reference=_head(origin_body.run_id, origin_body.head, origin_raw),
        selected_decision=call_head("capture-selection"),
        canonical_run_bytes=origin_raw,
        run=origin_body,
    )
    current_values = origin_body.model_dump() | {
        "worker_session": "current-session",
        "head": "pending",
        "run_id": current_id,
    }
    if "current_run_id" in current_values:
        current_values["current_run_id"] = current_id
    current_pending = type(origin_body).model_validate(current_values)
    current_body = current_pending.model_copy(update={"head": "loop:" + current_pending.digest()})
    current_raw = current_body.canonical_bytes()
    current_source = RetainedScheduledRunV1(
        schema_id=current_body.schema_id,
        reference=_head(current_body.run_id, current_body.head, current_raw),
        selected_decision=call_head("current-selection"),
        canonical_run_bytes=current_raw,
        run=current_body,
    )
    fence = fence.model_copy(
        update={
            "run_head": current_body.head,
            "lineage": fence.lineage.model_copy(update={"current_run_id": current_body.run_id}),
        }
    )
    selected = _selected_mandate(max_consequential_calls, origin_body.principal)
    binding = PreparedScheduledExecutionInitializationV2(
        source_request_fingerprint="b" * 64,
        run=original_body,
        scheduled_binding=ScheduledExecutionBindingV2(
            service_identity=selected.mandate.scope.service_identity,
            mandate_head=selected.mandate_head,
            primitive_parent=Present(head="parent", fingerprint="c" * 64),
            materialization=ordinary_seed_batch(1).occurrence_seeds[0].materialization,
            original_configuration=call_head("configuration"),
        ),
        proposal_fingerprint="d" * 64,
    )
    record = ScheduledExecutionInitializationRecordV2(
        source_request_fingerprint=binding.source_request_fingerprint,
        run=original.reference,
        scheduled_binding=binding.scheduled_binding,
    )
    initialization = RetainedScheduledInitializationV2(
        reference=scheduled_execution_initialization_reference(record),
        selected_decision=original.selected_decision,
        canonical_initialization_bytes=record.canonical_bytes(),
        record=record,
        preparation=binding,
        canonical_preparation_bytes=binding.canonical_bytes(),
    )
    target_ordinal = 2 if version == "v2" else 1
    records = tuple(
        InitializedCallRecord(
            original_call_id=call_subject_id(sealed_call.original),
            call=sealed_call,
            predecessor=Absent(),
        )
        for sealed_call in native_calls
    )
    initialized_records = tuple(
        call_record_reference(record.original_call_id, record) for record in records
    )
    initialized_record = records[target_ordinal]
    initialized = initialized_records[target_ordinal]
    call = initialized_record.call
    original_key = call.original
    initialized_tool = InitializedScheduledToolV1(
        tool_schema=call.tool_schema,
        tool_name="request_self_effect",
        tool_version="2",
        schema_id="chiplog.request-self-effect.v2",
    )
    selected_tool_permission = selected.mandate.scope.permitted_tools[0]
    seal = SealedResponseRecord(
        response_seal_id="response-seal",
        tenant_id=origin_body.tenant,
        original_run_id=origin_body.run_id,
        original_turn_id=origin_body.turns[-1].turn_id,
        captured_response=origin.reference,
        complete_ordered_initialized=initialized_records,
        bound=FanOutBound(
            max_call_count=8,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=131072,
            canonicalization_version="chiplog.recovery.frontier.v1",
        ),
    )
    seal_ref = CallSubjectHead(
        subject_id=seal.response_seal_id,
        revision=Present(
            head="record:" + _sha(seal.canonical_bytes()), fingerprint=_sha(seal.canonical_bytes())
        ),
    )
    initialized_source = RetainedScheduledInitializedCallV1(
        reference=initialized,
        selected_decision=call_head("fanout-selection"),
        canonical_initialized_bytes=initialized_record.canonical_bytes(),
        record=initialized_record,
        complete_ordered_initialized_records=records,
        sealed_response_reference=seal_ref,
        canonical_sealed_response_bytes=seal.canonical_bytes(),
        sealed_response=seal,
    )
    current = current_source.reference
    cut = CallPreparationCut(
        tenant_id="tenant",
        current_run=current,
        run_state="ACTIVE",
        tenant_commit_sequence=1,
        materialization_commitment="f" * 64,
        complete_call_inventory=call_head("inventory"),
        predecessor_inventory=CallInventorySnapshot(
            tenant_id="tenant", tenant_commit_sequence=1, ordered_calls=()
        ),
        authority_registry=call_head("authority-registry"),
        sources=(_source(),),
        fence=fence,
    )
    semantics = CallDispatchSemantics(
        normative_manifest=call_head("normative"),
        reducer=call_head("reducer"),
        transition_registry=call_head("transitions"),
        canonicalization=call_head("canonicalization"),
        adapter_contract=call_head("adapter"),
    )
    provenance_body = ScheduledCallOriginProvenanceV1(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="f" * 64,
        scheduler_genesis_run=original.reference,
        scheduler_initialization=initialization.reference,
        genesis_selected_decision=original.selected_decision,
        call_origin_run=origin.reference,
        capture_selected_decision=origin.selected_decision,
        initialized_call=initialized,
        sealed_response=seal_ref,
        initialization_selected_decision=initialized_source.selected_decision,
        current_run=current,
        current_run_selected_decision=current_source.selected_decision,
        current_lineage=fence.lineage,
        physical_root=fence.physical_root,
    )
    provenance = RetainedScheduledCallOriginProvenanceV1(
        source_reference=CallSubjectHead(
            subject_id="service-command",
            revision=Present(
                head="scheduler-call-origin-v1:" + _sha(provenance_body.canonical_bytes()),
                fingerprint=_sha(provenance_body.canonical_bytes()),
            ),
        ),
        canonical_source_bytes=provenance_body.canonical_bytes(),
        observation=provenance_body,
    )
    request = PrepareScheduledServiceAcceptancePrimitiveV1(
        command_id="service-command",
        database_id="database",
        initialized=initialized,
        initialized_record=initialized_record,
        scheduler_genesis_run=original,
        scheduler_initialization=initialization,
        call_origin_run=origin,
        initialized_source=initialized_source,
        origin_provenance=provenance,
        current_run=current_source,
        selected_mandate=selected,
        selected_budget_predecessor=_selected_budget(selected, predecessor_generation),
        tool_schema=call.tool_schema,
        tool_policy=call.tool_policy,
        dispatch_semantics=semantics,
        cut=cut,
        canonical_current_observation_bytes=b"current-observation",
        current_observation=CallSubjectHead(
            subject_id="service-command",
            revision=Present(
                head="scheduler-service-current-v1:" + _sha(b"current-observation"),
                fingerprint=_sha(b"current-observation"),
            ),
        ),
        initialized_tool=initialized_tool,
        selected_tool_permission=selected_tool_permission,
    )
    primitive = ScheduledServiceAcceptancePrimitiveV1(
        command_id=request.command_id,
        database_id=request.database_id,
        original_call_id=initialized_record.original_call_id,
        original=original_key,
        initialized=initialized,
        initialized_record=initialized_record,
        tool_schema=call.tool_schema,
        tool_policy=call.tool_policy,
        initialized_tool=initialized_tool,
        selected_tool_permission=selected_tool_permission,
        dispatch_semantics=semantics,
        cut=cut,
        selected_mandate=selected,
        selected_budget_predecessor=request.selected_budget_predecessor,
        current_observation=request.current_observation,
        scheduler_genesis_run=original.reference,
        scheduler_initialization=initialization.reference,
        call_origin_run=origin.reference,
        origin_provenance=provenance.source_reference,
    )
    reference = scheduled_service_primitive_reference(primitive)
    basis = ScheduledMandateConsumptionBasisV1(
        mandate_id=selected.mandate.scope.mandate_id,
        selected_predecessor=request.selected_budget_predecessor,
        primitive_command=reference,
        primitive_fingerprint=reference.revision.fingerprint,
        delta_cycles=0,
        delta_runs=0,
        delta_consequential_calls=1,
        accounting_policy_version="scheduler-accounting.v1",
    )
    predecessor = request.selected_budget_predecessor.budget
    successor_budget = ScheduledMandateBudgetV1(
        mandate_id=predecessor.mandate_id,
        generation=1,
        predecessor=request.selected_budget_predecessor.budget_head,
        cumulative_cycles=0,
        cumulative_runs=0,
        cumulative_consequential_calls=1,
        consumption_basis=basis,
    )
    proposed = ProposedScheduledMandateConsumptionV1(
        selected_predecessor=request.selected_budget_predecessor,
        successor=ProposedScheduledMandateBudgetV1(
            proposed_record_id="proposed-budget",
            canonical_budget_bytes=successor_budget.canonical_bytes(),
            budget=successor_budget,
        ),
        basis=basis,
    )
    prepared = PreparedScheduledServiceAcceptancePrimitiveV1(
        source_request_fingerprint=scheduled_service_request_fingerprint(request),
        primitive=primitive,
        canonical_primitive_bytes=primitive.canonical_bytes(),
        primitive_reference=reference,
        proposed_call_debit=proposed,
    )
    return ServiceAcceptanceFixture(request=request, prepared=prepared)
