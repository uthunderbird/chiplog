"""Completion request and result DTOs shared by their direct consumers."""

from __future__ import annotations

import hashlib
from base64 import b64decode, b64encode
from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    make_prepared_delivery_acceptance_member,
    make_terminal_manifest_member,
    make_terminal_run_member,
)
from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    completion_terminal_work_source_bytes,
)
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead as LoopHead,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ModelSelection as LoopModelSelection,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    OriginSelection as LoopOriginSelection,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ProviderRecipient as LoopRecipient,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    prepare_completion as prepare_delivery_completion,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_history_contracts import (
    EXECUTION_TOOLS_V3,
    ExecutionContinueV3,
    ExecutionModelAttemptV3,
    ExecutionPromptArtifactV3,
    ExecutionRunRecordV3,
    ExecutionTurnV3,
    ExecutionVisibilityManifestV3,
    execution_response_schema_v3,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    ExecutionTerminalManifest,
    RecoverySourceRecord,
)
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PrepareTerminalWork,
    WorkCommandIdentity,
)
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    FrozenRunBindings,
    RecoveryFrontier,
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    PreparedRejectedCompletionTerminalizationV1,
    PrepareRejectedCompletionTerminalizationV1,
    manifest_ref,
    rejected_terminalization_request_fingerprint,
    run_ref,
)
from tests.support.delivery_completion import _captured as captured_delivery
from tests.support.execution_fan_out import fixture as captured_execution


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _loop_head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name, revision=Present(head=f"{name}/head", fingerprint=_sha(name.encode()))
    )


def _terminal_run(
    run: ExecutionRun, state: Literal["SUCCEEDED", "ABORTED"], event: str
) -> ExecutionRun:
    pending = run.model_copy(
        update={"state": state, "event": event, "predecessor": run.head, "head": "pending"}
    )
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _selected_attempt_ref(run: ExecutionRun) -> CallSubjectHead:
    turn = run.turns[-1]
    attempt = turn.attempts[turn.selector]
    return CallSubjectHead(
        subject_id=attempt.attempt_id,
        revision=Present(head=attempt.head, fingerprint=attempt.digest()),
    )


def _selected_attempt_response(run: ExecutionRun) -> bytes:
    turn = run.turns[-1]
    encoded = turn.attempts[turn.selector].response_base64
    if encoded is None:
        raise ValueError("fixture selected attempt must retain a response")
    return b64decode(encoded, validate=True)


def _with_selected_response(run: ExecutionRun, raw: bytes) -> ExecutionRun:
    """Replace the selected native attempt response and rederive the native Run head."""
    turn = run.turns[-1]
    attempts = list(turn.attempts)
    attempts[turn.selector] = attempts[turn.selector].model_copy(
        update={"response_base64": b64encode(raw).decode()}
    )
    updated_turn = turn.model_copy(update={"attempts": tuple(attempts)})
    turns = (*run.turns[:-1], updated_turn)
    pending = run.model_copy(update={"turns": turns, "head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _delivery_run_head(run: ExecutionRun) -> LoopHead:
    reference = run_ref(run)
    return LoopHead(
        identity=reference.subject_id,
        head=reference.revision.head,
        fingerprint=reference.revision.fingerprint,
    )


def _completion_cut(run: ExecutionRun, original_run: CallSubjectHead) -> ExecutionRecoveryCut:
    """A closed recovery cut whose lineage retains this exact original Run."""
    return ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment=_sha(b"completion-materialization"),
        current_run=original_run,
        complete_ordered_run_lineage=(run,),
        complete_ordered_responses=(),
        frontier=RecoveryFrontier(
            tenant_id="tenant",
            run_id=run.run_id,
            tenant_commit_sequence=4,
            registry=RecoveryFrontierRegistry(
                registry_id="fixture-registry",
                version="1",
                fingerprint=_sha(b"fixture-registry"),
                ordered_rows=(
                    RecoveryRegistryRow(
                        family="RUN",
                        ordinal=0,
                        subject_extractor_id="run",
                        cardinality_rule="exact-one",
                        terminal_conflict_rule="reject-rival",
                        serialization_rule="canonical",
                        canonicalization_version="1",
                    ),
                ),
            ),
            ordered_members=(
                FrontierMember(
                    family="RUN",
                    subject=run.run_id,
                    branch="ACTIVE",
                    ordered_heads=(original_run.revision,),
                    fingerprint=_sha(b"fixture-frontier-member"),
                ),
            ),
            ordered_calls=(),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint=_sha(b"fixture-frontier"),
        ),
        current_bindings=FrozenRunBindings(
            objective="Plan",
            requested_work="Plan",
            prompt_artifact=Present(head="prompt", fingerprint=_sha(b"prompt")),
            ordered_tool_specs=(Present(head="tool", fingerprint=_sha(b"tool")),),
            generated_schema=Present(head="schema", fingerprint=_sha(b"schema")),
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=Present(head="scope", fingerprint=_sha(b"scope")),
            authority_mandate_heads=(),
            policy=Present(head="policy", fingerprint=_sha(b"policy")),
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=_loop_head("source"),
                schema_id="fixture-source.v1",
                canonical_record_bytes=b"fixture-source",
                selected_decision=_loop_head("decision"),
                physical_record=_loop_head("physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint=_sha(b"fixture-inventory"),
    )


def _v3_run() -> ExecutionRunRecordV3:
    """Minimal history-aware v3 active Run, copied as a support builder not a test import."""
    label = DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())
    artifact = ExecutionPromptArtifactV3(
        content_hash=_sha(b"v3-prompt"),
        library_version="fixture-v3",
        tools=EXECUTION_TOOLS_V3,
        response_schema_json=execution_response_schema_v3(),
        rendered="history-aware fixture prompt",
    )
    members = tuple(
        VisibilityMember(
            record_id=surface,
            revision_head=f"revision:{surface}",
            content=content,
            provenance_head=f"provenance:{surface}",
            label_head=f"label:{surface}",
            label=label,
            producer="fixture",
            surface=surface,
        )
        for surface, content in (
            ("prompt", artifact.rendered),
            ("schema", artifact.response_schema_json),
            ("context", "fixture context"),
        )
    )
    manifest = ExecutionVisibilityManifestV3(
        tenant="tenant",
        principal="principal",
        contour_head="contour",
        run_id="run-v3",
        turn_id="turn-v3",
        generation=0,
        worker_session="worker",
        members=members,
        joined_label=label,
        artifact=artifact,
    )
    history_response = ExecutionContinueV3(
        kind="Continue",
        tool_calls=(
            ReadOnlyHistoryToolCall(
                call_id="history",
                tool="read_conversation_history",
                arguments=ConversationHistoryQuery(limit=1, after_cursor=None),
            ),
        ),
    ).canonical_bytes()
    history_manifest = manifest.model_copy(update={"turn_id": "turn-v3-history"})
    history_attempt = ExecutionModelAttemptV3(
        attempt_id="attempt-v3-history",
        lineage_id="lineage-v3-history",
        generation=0,
        state="RESPONSE_CAPTURED",
        head="attempt-history-head",
        manifest=history_manifest,
        request="history request",
        provider_contract="hermetic-model.v1",
        recipient="hermetic-model",
        live_model=None,
        worker_session="worker",
        response_base64=b64encode(history_response).decode(),
        receipt="history-receipt",
        rejection=None,
    )
    history_turn = ExecutionTurnV3(
        turn_id="turn-v3-history",
        ordinal=1,
        head="turn-history-head",
        state="ACCEPTED",
        accumulator=members,
        attempts=(history_attempt,),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    response = DeliveryCompletion(
        tenant="tenant",
        run_id="run-v3",
        turn_id="turn-v3",
        deliveries=(ProposedDelivery(payload=(Commentary(text="completed"),)),),
    ).canonical_bytes()
    attempt = ExecutionModelAttemptV3(
        attempt_id="attempt-v3",
        lineage_id="lineage-v3",
        generation=0,
        state="RESPONSE_CAPTURED",
        head="attempt-head",
        manifest=manifest,
        request="request",
        provider_contract="hermetic-model.v1",
        recipient="hermetic-model",
        live_model=None,
        worker_session="worker",
        response_base64=b64encode(response).decode(),
        receipt="receipt",
        rejection=None,
    )
    turn = ExecutionTurnV3(
        turn_id="turn-v3",
        ordinal=2,
        head="turn-head",
        state="RESPONSE_AVAILABLE",
        accumulator=members,
        attempts=(attempt,),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    recipient = LoopRecipient(
        provider_id="provider",
        account_id="account",
        recipient_id="recipient",
        endpoint=LoopHead(identity="endpoint", head="endpoint/head", fingerprint=_sha(b"endpoint")),
        canonical_address=b"recipient://exact",
        credential_binding=LoopHead(
            identity="credential", head="credential/head", fingerprint=_sha(b"credential")
        ),
    )
    pending = ExecutionRunRecordV3(
        tenant="tenant",
        principal="principal",
        run_id="run-v3",
        state="ACTIVE",
        head="pending",
        predecessor=None,
        prompt="history-aware fixture prompt",
        policy=BudgetPolicy(),
        origin=LoopOriginSelection(
            ingress_binding=LoopHead(
                identity="ingress", head="ingress/head", fingerprint=_sha(b"ingress")
            ),
            recipient=recipient,
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        root_binding="NOT_APPLICABLE",
        turns=(history_turn, turn),
        delivery_acceptance=None,
        suspension_baseline=None,
        original_obligations=(),
        no_retry_references=(),
        event="ModelResponseCaptured",
    )
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _obligation() -> OriginalObligationBinding:
    return OriginalObligationBinding(
        original_run_id="run",
        original_call_id="call",
        obligation_id="obligation",
        obligation_stream_id="obligation-stream",
        obligation_head="obligation-head",
        closure_predicate_id="predicate",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="evidence-stream",
        evidence_head=Absent(),
    )


async def accepted_completion_fixture(
    run_schema: Literal["v2", "v3"],
    work: Literal["nonempty", "empty", "nonempty2"] = "nonempty",
) -> AcceptedAssemblyFixture:
    """Return an accepted two-delivery graph with either populated or empty work."""
    # The v3 source builder is supplied below; both branches retain an active native Run.
    captured = await captured_execution(complete=True)
    run: ExecutionRun = captured.captured_run
    if run_schema == "v3":
        run = _v3_run()
    _, observed = await captured_delivery()
    selected_turn_id = run.turns[-1].turn_id
    second = LoopRecipient(
        provider_id="hermetic-second",
        account_id="account-second",
        recipient_id="recipient-second",
        endpoint=LoopHead(
            identity="second-endpoint",
            head="second-endpoint-head",
            fingerprint=_sha(b"second-endpoint"),
        ),
        canonical_address=b"local://second",
        credential_binding=LoopHead(
            identity="second-credential",
            head="second-credential-head",
            fingerprint=_sha(b"second-credential"),
        ),
    )
    completion = DeliveryCompletion(
        tenant=run.tenant,
        run_id=run.run_id,
        turn_id=selected_turn_id,
        deliveries=(
            ProposedDelivery(payload=(Commentary(text="first accepted delivery"),)),
            ProposedDelivery(
                selection=LoopModelSelection(recipient=second),
                payload=(Commentary(text="second accepted delivery"),),
            ),
        ),
    )
    run = _with_selected_response(run, completion.canonical_bytes())
    selected_attempt = _selected_attempt_ref(run)
    observation = observed.model_copy(
        update={
            "tenant": run.tenant,
            "run": _delivery_run_head(run),
            "turn_id": selected_turn_id,
            "captured_response": completion.canonical_bytes(),
            "recipients": (observed.origin.recipient, second),
        }
    )
    proposal = prepare_delivery_completion(completion, observation)
    obligations = (
        (_obligation(),)
        if work == "nonempty"
        else (
            _obligation(),
            _obligation().model_copy(
                update={"obligation_id": "obligation-2", "obligation_head": "obligation-head-2"}
            ),
        )
        if work == "nonempty2"
        else ()
    )
    original_run = run_ref(run)
    original = PrepareExecutionCompletion(
        command_id="complete",
        run=run,
        selected_attempt=selected_attempt,
        selector_generation=run.turns[-1].attempts[run.turns[-1].selector].generation,
        visibility_manifest=_loop_head("visibility"),
        exact_captured_response=_selected_attempt_response(run),
        cut=_completion_cut(run, original_run),
        complete_earlier_continuations=(),
        delivery=observation,
        fence=captured.request.cut.fence,
    )
    manifest = ExecutionTerminalManifest(
        manifest_id=f"terminal-manifest-{run_schema}-{work}",
        command_id=original.command_id,
        prior_run=original_run,
        target="SUCCEEDED",
        source_cut_fingerprint=original.cut.digest(),
        complete_accounting=(),
        complete_open_original_obligations=obligations,
    )
    terminal = _terminal_run(run, "SUCCEEDED", "ExecutionCompleted")
    prepared_seed = PreparedExecutionCompletion(
        source_request_fingerprint=_sha(original.canonical_bytes()),
        complete_earlier_continuations=(),
        delivery=proposal,
        terminal_manifest=manifest,
        run=terminal,
        complete_owner_commitment="0" * 64,
    )
    delivery_member = make_prepared_delivery_acceptance_member(original, prepared_seed)
    manifest_member = make_terminal_manifest_member(manifest)
    run_member = make_terminal_run_member(terminal)
    prepared = prepared_seed.model_copy(
        update={
            "complete_owner_commitment": _sha(
                delivery_member.canonical_record_bytes
                + manifest_member.canonical_record_bytes
                + run_member.canonical_record_bytes
            )
        }
    )
    work_source = AcceptedCompletionWorkSourceV1(
        original_completion_request_bytes=original.canonical_bytes(),
        prepared_completion_bytes=prepared.canonical_bytes(),
        terminal_run=terminal,
        terminal_run_head=run_ref(terminal),
        terminal_manifest=manifest,
        terminal_manifest_head=manifest_ref(manifest),
        ordered_open_obligations=obligations,
    )
    work_request = PrepareTerminalWork(
        identity=WorkCommandIdentity(tenant_id="tenant", command_id="terminal-work"),
        terminal_run=terminal,
        original_terminalization_request=completion_terminal_work_source_bytes(work_source),
        terminal_manifest=manifest_ref(manifest),
        ordered_open_obligations=obligations,
    )
    return AcceptedAssemblyFixture(
        original_completion_request=original,
        prepared_completion=prepared,
        work_source=work_source,
        terminal_work_request=work_request,
    )


async def rejected_completion_fixture(
    run_schema: Literal["v2", "v3"] = "v3",
) -> RejectedAssemblyFixture:
    """Return the semantic-rejection graph: aborted Run, no conversation/effects rows."""
    accepted = await accepted_completion_fixture(run_schema, "empty")
    original = accepted.original_completion_request
    assert isinstance(original, PrepareExecutionCompletion)
    aborted = _terminal_run(original.run, "ABORTED", "ExecutionRejected")
    reject = PreparedExecutionCompletionReject(
        source_request_fingerprint=_sha(original.canonical_bytes()),
        original_captured_attempt=original.selected_attempt,
        preserved_trace=_loop_head("preserved-trace"),
        visibility_manifest=original.visibility_manifest,
        reasons=("SCHEMA",),
        run=aborted,
        complete_owner_commitment=_sha(b"completion-rejection"),
    )
    manifest = ExecutionTerminalManifest(
        manifest_id=f"rejected-terminal-manifest-{run_schema}",
        command_id=original.command_id,
        prior_run=run_ref(original.run),
        target="ABORTED",
        source_cut_fingerprint=original.cut.digest(),
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    terminalization_request = PrepareRejectedCompletionTerminalizationV1(
        original_completion_request_bytes=original.canonical_bytes(),
        original_completion_reject_bytes=reject.canonical_bytes(),
        original_captured_run=run_ref(original.run),
        original_selected_attempt=original.selected_attempt,
        source_cut_fingerprint=original.cut.digest(),
        proposed_terminal_run=aborted,
        preserved_trace=reject.preserved_trace,
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    manifest_member = make_terminal_manifest_member(manifest)
    run_member = make_terminal_run_member(aborted)
    terminalization_result = PreparedRejectedCompletionTerminalizationV1(
        source_request_fingerprint=rejected_terminalization_request_fingerprint(
            terminalization_request
        ),
        terminal_manifest=manifest,
        terminal_manifest_head=manifest_ref(manifest),
        terminal_run=aborted,
        terminal_run_head=run_ref(aborted),
        complete_owner_commitment=_sha(
            manifest_member.canonical_record_bytes + run_member.canonical_record_bytes
        ),
    )
    return RejectedAssemblyFixture(
        rejected_terminalization_request=terminalization_request,
        result=terminalization_result,
    )


@dataclass(frozen=True)
class AcceptedAssemblyFixture:
    original_completion_request: PrepareExecutionCompletion
    prepared_completion: PreparedExecutionCompletion
    work_source: AcceptedCompletionWorkSourceV1
    terminal_work_request: PrepareTerminalWork


@dataclass(frozen=True)
class RejectedAssemblyFixture:
    rejected_terminalization_request: PrepareRejectedCompletionTerminalizationV1
    result: PreparedRejectedCompletionTerminalizationV1
