"""Shared builders extracted without changing their behavior."""

import hashlib

from tests.support.execution_fan_out import fixture

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    InitializedCallRecord,
    NotExecutedCallResultRecord,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.call_acceptance_preparation import call_record_reference
from chiplog.capabilities.agent_loop.contracts import Frozen
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareExecutionResume,
    PrepareExecutionSuspension,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    RecoverySourceRecord,
    SealedResponseRecoveryView,
    SelectedExecutionSuspension,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    _expected_suspended_frontier,
    prepare_execution_suspension,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    FrontierMember,
    FrozenRunBindings,
    InitializedCall,
    RecoveryFrontier,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    execution_native_one_call_cancellation_frontier_registry_v1,
)


def _run_ref(run: ExecutionRunRecord | ExecutionRunRecordV3) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )


def _source(schema: str, ref: CallSubjectHead, value: Frozen | RecoveryDTO) -> RecoverySourceRecord:
    return RecoverySourceRecord(
        owner="agent_loop",
        subject=ref,
        schema_id=schema,
        canonical_record_bytes=value.canonical_bytes(),
        selected_decision=ref,
        physical_record=ref,
    )


async def _cut() -> ExecutionRecoveryCut:
    f = await fixture()
    captured = f.captured_run
    original = f.request.ordered_calls[-1]
    initialized = InitializedCallRecord(
        original_call_id="call:" + original.original.digest(),
        call=original,
        predecessor=Absent(),
    )
    init_ref = call_record_reference(initialized.original_call_id, initialized)
    seal = SealedResponseRecord(
        response_seal_id="seal",
        tenant_id="tenant",
        original_run_id=captured.run_id,
        original_turn_id=captured.turns[-1].turn_id,
        captured_response=_run_ref(captured),
        complete_ordered_initialized=(init_ref,),
        bound=f.request.bound,
    )
    seal_ref = call_record_reference(seal.response_seal_id, seal)
    turn = captured.turns[-1].model_copy(
        update={"response_seal": seal_ref, "initialized_calls": (init_ref,)}
    )
    sealed = captured.model_copy(
        update={
            "turns": (turn,),
            "predecessor": captured.head,
            "head": "sealed-head",
            "event": "ModelResponseSealed",
        }
    )
    terminal = CancelledBeforeAcceptRecord(
        terminal_id="terminal",
        source_command_id="cancel",
        original_call_id=initialized.original_call_id,
        original=original.original,
        initialized=init_ref,
        cancellation_act=init_ref,
        cut=f.request.cut,
        not_executed_result_id="result",
    )
    terminal_ref = call_record_reference(terminal.terminal_id, terminal)
    result = NotExecutedCallResultRecord(
        result_id="result",
        original_call_id=initialized.original_call_id,
        initialized=init_ref,
        terminal=terminal_ref,
        outcome="NOT_EXECUTED",
    )
    result_ref = call_record_reference(result.result_id, result)
    call = TerminalCallFrontier(
        original_call_id=initialized.original_call_id,
        response_id=seal.response_seal_id,
        acceptance=InitializedCall(initialized=init_ref.revision),
        disposition=CancelledBeforeAcceptResult(
            terminal=terminal_ref.revision,
            not_executed_result=result_ref.revision,
            initialized_predecessor=init_ref.revision,
            recovered_outcome=NotApplicable(),
        ),
    )
    registry = execution_native_one_call_cancellation_frontier_registry_v1()
    frontier = RecoveryFrontier(
        tenant_id="tenant",
        run_id=captured.run_id,
        tenant_commit_sequence=1,
        registry=registry,
        ordered_members=(
            FrontierMember(
                family="RUN",
                subject=captured.run_id,
                branch="ACTIVE",
                ordered_heads=(_run_ref(sealed).revision,),
                fingerprint="a" * 64,
            ),
        ),
        ordered_calls=(call,),
        canonicalization_version="chiplog.recovery.frontier.v1",
        fingerprint="b" * 64,
    )
    view = SealedResponseRecoveryView(
        original_run=_run_ref(captured),
        original_turn=CallSubjectHead(
            subject_id=captured.turns[-1].turn_id,
            revision=Present(
                head=captured.turns[-1].head,
                fingerprint=captured.turns[-1].digest(),
            ),
        ),
        selected_seal=seal_ref,
        seal=seal,
        ordered_calls=(call,),
        frontier_head=call_record_reference("recovery-frontier:" + captured.run_id, frontier),
    )
    artifact = turn.attempts[turn.selector].manifest.artifact

    def _present(raw: bytes) -> Present:
        digest = hashlib.sha256(raw).hexdigest()
        return Present(head="record:" + digest, fingerprint=digest)

    cut = ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="db",
        tenant_commit_sequence=1,
        materialization_commitment="c" * 64,
        current_run=_run_ref(sealed),
        complete_ordered_run_lineage=(captured, sealed),
        complete_ordered_responses=(view,),
        frontier=frontier,
        current_bindings=FrozenRunBindings(
            objective=sealed.prompt,
            requested_work=turn.attempts[turn.selector].request,
            prompt_artifact=_present(artifact.canonical_bytes()),
            ordered_tool_specs=tuple(_present(tool.canonical_bytes()) for tool in artifact.tools),
            generated_schema=_present(artifact.response_schema_json.encode()),
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=init_ref.revision,
            authority_mandate_heads=(),
            policy=_present(sealed.policy.canonical_bytes()),
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            _source("chiplog.agent-loop.execution-record.v2", _run_ref(captured), captured),
            _source("chiplog.agent-loop.execution-record.v2", _run_ref(sealed), sealed),
            _source("chiplog.call.response-seal.v1", seal_ref, seal),
            _source("chiplog.call.initialized.v1", init_ref, initialized),
            _source("chiplog.call.cancelled-before-accept.v1", terminal_ref, terminal),
            _source("chiplog.call.not-executed-result.v1", result_ref, result),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="d" * 64,
    )
    return cut


async def _resume_request() -> PrepareExecutionResume:
    cut = await _cut()
    run = cut.complete_ordered_run_lineage[-1]
    suspension = prepare_execution_suspension(
        PrepareExecutionSuspension(
            command_id="suspend-for-resume",
            run=run,
            cut=cut,
            activation_blocking_predicates=(),
            fence=NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=run.run_id,
                run_head=run.head,
                worker_session_id=run.worker_session,
                runtime_generation="test-generation",
            ),
        )
    )
    assert suspension.kind == "PREPARED_EXECUTION_SUSPENSION_V2"
    suspended = suspension.run
    suspended_ref = _run_ref(suspended)
    suspended_source = _source("chiplog.agent-loop.execution-record.v2", suspended_ref, suspended)
    current_cut = cut.model_copy(
        update={
            "tenant_commit_sequence": cut.tenant_commit_sequence + 1,
            "current_run": suspended_ref,
            "complete_ordered_run_lineage": (*cut.complete_ordered_run_lineage, suspended),
            "frontier": _expected_suspended_frontier(
                suspension.baseline.frontier,
                suspension.pair.predecessor_run,
                suspended_ref,
                cut.tenant_commit_sequence + 1,
            ),
            "complete_sources": (
                *cut.complete_sources[:-4],
                suspended_source,
                *cut.complete_sources[-4:],
            ),
        }
    )
    pair_raw = suspension.pair.canonical_bytes()
    pair_digest = hashlib.sha256(pair_raw).hexdigest()
    pair_id = "chiplog.execution.suspension-pair.v2:" + pair_digest
    selected_pair = CallSubjectHead(
        subject_id=pair_id,
        revision=Present(head=pair_id, fingerprint=pair_digest),
    )
    selected = SelectedExecutionSuspension(
        baseline=suspension.baseline,
        pair=suspension.pair,
        selected_pair=selected_pair,
        selected_decision=CallSubjectHead(
            subject_id=suspension.pair.suspension_command_id,
            revision=Present(head="decision:1", fingerprint="e" * 64),
        ),
        canonical_pair_bytes=pair_raw,
    )
    request = PrepareExecutionResume(
        command_id=f"execution-resume:{suspended.run_id}:{suspended.head}",
        run=suspended,
        original_suspension=selected,
        cut=current_cut,
        disposition_version="r14-same-run-equality.v1",
        activation_payload=b"activate-same-run",
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=suspended.run_id,
            run_head=suspended.head,
            worker_session_id=suspended.worker_session,
            runtime_generation="test-generation",
        ),
    )

    return request
