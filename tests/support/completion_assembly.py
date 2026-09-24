"""Real, closed completion-publication fixtures shared by composition consumers.

The module deliberately builds owner exchanges before it wraps them as broker
records.  It contains no assertions and imports no test module.
"""

from __future__ import annotations

import hashlib
from base64 import b64decode, b64encode
from dataclasses import dataclass
from typing import Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    CompletionCanonicalMember,
    make_completion_rejection_member,
    make_prepared_delivery_acceptance_member,
    make_terminal_manifest_member,
    make_terminal_run_member,
)
from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    RejectedCompletionWorkSourceV1,
    completion_terminal_work_source_bytes,
)
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    AcceptedDelivery,
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
    DeliveryAcceptanceProposal,
    DeliveryCompletion,
    DeliveryObservation,
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
    PostTerminalWorkView,
    PreparedPostTerminalWork,
    PrepareTerminalWork,
    UnleasedWork,
    WorkCanonicalMember,
    WorkCommandIdentity,
)
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    PostTerminalEpochRecord,
    PostTerminalLeaseRecord,
    PostTerminalSelectorRecord,
    PostTerminalSubjectRecord,
    WorkDurableRecord,
)
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    OriginalObligationBinding,
    Present,
    WorkEpochBinding,
    WorkSubjectBinding,
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
from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    DeliverySendBinding,
    ExactHead,
    ModelSelection,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.fences import Absent as EffectsAbsent
from chiplog.capabilities.effects.scoped_intent_contracts import (
    DispatchMandateV3,
    ExternalActionIntentV3,
    PreparedDeliveryAuthority,
    PreparedDeliveryBasisV3,
    PreparedDeliveryOriginV3,
    PreparedScopedIntentPublication,
    PrepareScopedIntentPublication,
    ScopedAuthorityRecord,
    ScopedDispatchAcquisition,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    ScopedIntentCanonicalMemberV3,
    make_scoped_intent_member,
    make_scoped_intent_retained_exchange,
    scoped_intent_complete_owner_commitment,
    scoped_intent_fingerprint,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ConversationCanonicalMemberV2,
    ConversationCompletionEntryV1,
    ConversationCompletionSourceV1,
    ConversationSourceCutV1,
    PrepareConversationCompletionRejectedV1,
    PrepareConversationCompletionV1,
    PreparedConversationCompletionRejectedV1,
    PreparedConversationCompletionV1,
    conversation_complete_owner_commitment,
    conversation_no_change_commitment,
    conversation_recipient_binding_json,
    conversation_source_heads_json,
    conversation_source_request_fingerprint,
    make_conversation_canonical_member,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.composition.completion_publication_contracts import (
    DeliveryEffectsExchangeV1,
    PrepareCompleteAcceptanceAssemblyV1,
    PrepareRejectedCompletionAssemblyV1,
    acceptance_commands,
    completion_batch_fingerprint,
    rejected_commands,
)
from chiplog.platform._owner_publication_contracts import (
    CompleteDeliveryBatchV2,
    OwnerCommandBytes,
    OwnerRecordBytes,
    RejectedCompletionBatchV1,
)
from tests.support.acceptance_v2 import prepared_acceptance
from tests.support.delivery_completion import _captured as captured_delivery
from tests.support.execution_fan_out import fixture as captured_execution
from tests.support.workspace import envelope


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _loop_head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name, revision=Present(head=f"{name}/head", fingerprint=_sha(name.encode()))
    )


def _effects_head(name: str) -> ExactHead:
    return reference(name, name.encode())


def _effects_source(name: str) -> ScopedAuthorityRecord:
    return ScopedAuthorityRecord(
        owner="effects",
        head=_effects_head(name),
        schema_id=f"{name}.v1",
        canonical_record_bytes=name.encode(),
        selected_decision=_effects_head(f"selected:{name}"),
    )


def _effect_head(value: LoopHead) -> ExactHead:
    return ExactHead(subject_id=value.identity, head=value.head, fingerprint=value.fingerprint)


def _effect_recipient(value: LoopRecipient) -> ProviderRecipient:
    return ProviderRecipient(
        provider=value.provider_id,
        account=value.account_id,
        recipient=value.recipient_id,
        endpoint=_effect_head(value.endpoint),
        canonical_address=value.canonical_address,
        credential_binding=_effect_head(value.credential_binding),
    )


def _owner(
    owner: Literal["agent_loop", "effects", "conversation"],
    member: CompletionCanonicalMember
    | ConversationCanonicalMemberV2
    | ScopedIntentCanonicalMemberV3
    | WorkCanonicalMember,
) -> OwnerRecordBytes:
    raw = (
        member.canonical_bytes
        if isinstance(member, ConversationCanonicalMemberV2)
        else member.canonical_record_bytes
    )
    return OwnerRecordBytes(
        owner=owner,
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=raw,
        fingerprint=member.fingerprint,
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


def _work_member(record: WorkDurableRecord) -> WorkCanonicalMember:
    raw = record.canonical_bytes()
    return WorkCanonicalMember(
        record_kind=record.record_kind,
        record_id=record.record_id,
        schema_id=record.schema_id,
        canonical_record_bytes=raw,
        fingerprint=_sha(raw),
    )


def _work(request: PrepareTerminalWork) -> PreparedPostTerminalWork:
    if not request.ordered_open_obligations:
        return PreparedPostTerminalWork(
            source_request_fingerprint=_sha(request.canonical_bytes()),
            ordered_work=(),
            complete_records=(),
            complete_commitment=_sha(b"empty-work"),
        )
    views: list[PostTerminalWorkView] = []
    members: list[WorkCanonicalMember] = []
    for ordinal, source in enumerate(request.ordered_open_obligations):
        suffix = str(ordinal + 1)
        work_id = f"completion-work-{suffix}"
        subject = _work_member(
            PostTerminalSubjectRecord(
                tenant_id="tenant",
                command_id=request.identity.command_id,
                record_id=f"completion-subject-{suffix}",
                work_id=work_id,
                terminal_run_head=request.terminal_run.head,
                terminal_manifest_head=request.terminal_manifest.revision.head,
                terminal_manifest_member_fingerprint=request.terminal_manifest.revision.fingerprint,
                original_obligation=source,
            )
        )
        epoch = _work_member(
            PostTerminalEpochRecord(
                tenant_id="tenant",
                command_id=request.identity.command_id,
                record_id=f"completion-epoch-{suffix}",
                work_id=work_id,
                epoch_id=f"completion-epoch-{suffix}",
                subject=Present(head=subject.record_id, fingerprint=subject.fingerprint),
                predecessor_epoch=Absent(),
            )
        )
        selector = _work_member(
            PostTerminalSelectorRecord(
                tenant_id="tenant",
                command_id=request.identity.command_id,
                record_id=f"completion-selector-{suffix}",
                work_id=work_id,
                selector_id=f"completion-selector-{suffix}",
                selector_version=0,
                selected_epoch_id=epoch.record_id,
                selected_epoch=Present(head=epoch.record_id, fingerprint=epoch.fingerprint),
                predecessor_selector=Absent(),
            )
        )
        lease = _work_member(
            PostTerminalLeaseRecord(
                tenant_id="tenant",
                command_id=request.identity.command_id,
                record_id=f"completion-lease-{suffix}",
                work_id=work_id,
                epoch=Present(head=epoch.record_id, fingerprint=epoch.fingerprint),
                selector=Present(head=selector.record_id, fingerprint=selector.fingerprint),
                predecessor_lease=Absent(),
                lease=UnleasedWork(lease_head=f"completion-lease-{suffix}"),
                original_obligation=Present(
                    head=source.obligation_head, fingerprint=_sha(source.obligation_head.encode())
                ),
                original_evidence=source.evidence_head,
                resolver_batch=Absent(),
            )
        )
        views.append(
            PostTerminalWorkView(
                subject=WorkSubjectBinding(
                    work_id=work_id,
                    work_subject_head=subject.record_id,
                    work_subject_fingerprint=subject.fingerprint,
                    terminal_manifest_head=request.terminal_manifest.revision.head,
                    terminal_manifest_member_fingerprint=request.terminal_manifest.revision.fingerprint,
                    original_obligation=source,
                ),
                work_epoch=WorkEpochBinding(
                    selector_id=selector.record_id,
                    selector_head=selector.record_id,
                    selector_version=0,
                    current_epoch_id=epoch.record_id,
                    current_epoch_head=epoch.record_id,
                ),
                work_state_head=Present(head=lease.record_id, fingerprint=lease.fingerprint),
                lease=UnleasedWork(lease_head=lease.record_id),
                predecessor_rollover=Absent(),
            )
        )
        members.extend((subject, epoch, selector, lease))
    records = tuple(members)
    return PreparedPostTerminalWork(
        source_request_fingerprint=_sha(request.canonical_bytes()),
        ordered_work=tuple(views),
        complete_records=records,
        complete_commitment=_sha(b"".join(item.canonical_record_bytes for item in records)),
    )


def _effects_exchange(
    index: int,
    completion: DeliveryCompletion,
    observation: DeliveryObservation,
    proposal: DeliveryAcceptanceProposal,
    delivery: AcceptedDelivery,
) -> DeliveryEffectsExchangeV1:
    # The support delivery DTOs are exact owner bytes; the effects DTOs deliberately
    # use their separate head/recipient vocabulary.
    accepted = delivery
    selection = (
        OriginSelection(
            kind="ORIGIN_EXACT",
            ingress_binding=_effect_head(accepted.selection.ingress_binding),
            recipient=_effect_recipient(accepted.selection.recipient),
        )
        if accepted.selection.kind == "ORIGIN_EXACT"
        else ModelSelection(
            kind="MODEL_SELECTED_EXACT", recipient=_effect_recipient(accepted.selection.recipient)
        )
    )
    binding = DeliverySendBinding(
        delivery_id=accepted.delivery_id,
        acceptance=_effect_head(accepted.acceptance),
        render_digest=accepted.render_digest,
        manifest_digest=accepted.manifest_digest,
        selection=selection,
        visibility=tuple(_effect_head(item) for item in accepted.visibility),
        provenance=tuple(_effect_head(item) for item in accepted.provenance),
        disclosure=tuple(_effect_head(item) for item in accepted.disclosure),
        narrowing=tuple(_effect_head(item) for item in accepted.narrowing),
        policy=_effect_head(accepted.policy),
    )
    basis = PreparedDeliveryBasisV3(
        source_cut=_effects_head(f"completion-cut-{index}"),
        acceptance=_effect_head(proposal.acceptance),
        completion_command_bytes=completion.canonical_bytes(),
        delivery_observation_bytes=observation.canonical_bytes(),
        loop_proposal_bytes=proposal.canonical_bytes(),
    )
    origin = PreparedDeliveryOriginV3(
        original_run=_effects_head(f"run-{index}"),
        captured_attempt=_effects_head(f"attempt-{index}"),
        binding=binding,
        preparation_basis=_effects_head(f"basis-{index}"),
    )
    baseline = prepared_acceptance().effects_proposal.snapshot.intent.mandate.model_dump()
    baseline.update(
        schema_id="chiplog.effects.dispatch-mandate.v3",
        mandate_id=f"delivery-mandate-{index}",
        origin=origin,
        recipient=_effect_recipient(accepted.selection.recipient),
        payload=accepted.rendered_bytes,
        effect_fingerprint=_sha(accepted.rendered_bytes),
        idempotency_fence_key=f"delivery-fence-{index}",
    )
    mandate = DispatchMandateV3.model_validate(baseline)
    precursor = ScopedPrecursorRequest(
        request_id=f"delivery-precursor-{index}",
        mandate=mandate,
        interpretation_policy=_effects_source(f"policy-{index}"),
        preexisting_sources=(_effects_source(f"authority-{index}"),),
    )
    precursor_result = ScopedPrecursorResult(
        source_request_fingerprint=_sha(precursor.canonical_bytes()),
        mandate_fingerprint=_sha(mandate.canonical_bytes()),
        interpretation_policy=precursor.interpretation_policy.head,
        complete_evaluation_evidence=(_effects_head(f"evaluation-{index}"),),
    )
    acquisition = ScopedDispatchAcquisition(
        authority=PreparedDeliveryAuthority(
            basis=basis,
            preexisting_communication_authority=_effects_source(f"communication-{index}"),
            current_disclosure_authority=_effects_source(f"disclosure-{index}"),
            exact_mandate_bytes=mandate.canonical_bytes(),
        ),
        precursor_request=precursor,
        precursor_result=precursor_result,
        original_sources=prepared_acceptance().effects_proposal.snapshot.intent.acquisition.original_sources,
    )
    unsigned = ExternalActionIntentV3(
        intent_id=f"{accepted.delivery_id}/intent",
        fingerprint="0" * 64,
        mandate=mandate,
        acquisition=acquisition,
    )
    intent = unsigned.model_copy(update={"fingerprint": scoped_intent_fingerprint(unsigned)})
    request = PrepareScopedIntentPublication(
        identity=CommandIdentity(
            command_id=f"publish-delivery-{index}",
            fingerprint=_sha(f"publish-delivery-{index}".encode()),
            expected_tenant_head=index,
        ),
        intent=intent,
        expected_intent=EffectsAbsent(),
        current=prepared_acceptance().effects_request.current,
        complete_current_origin_sources=(_effects_source(f"current-origin-{index}"),),
        fence=prepared_acceptance().effects_request.command.fence,
    )
    retained = make_scoped_intent_retained_exchange(request)
    references = retained.original_references
    member = make_scoped_intent_member(intent)
    result = PreparedScopedIntentPublication(
        source_request_fingerprint=_sha(request.canonical_bytes()),
        intent=intent,
        original_references=references,
        complete_owner_commitment=scoped_intent_complete_owner_commitment(
            member, retained.original_sources
        ),
    )
    return DeliveryEffectsExchangeV1(
        delivery_id=accepted.delivery_id,
        basis=basis,
        precursor_request=precursor,
        precursor_result=precursor_result,
        intent_request=request,
        intent_result=result,
        acquisition_bytes=acquisition.canonical_bytes(),
        mandate_bytes=mandate.canonical_bytes(),
        retained_sources=retained.original_sources,
    )


def _conversation_entry(delivery_id: str, sequence: int, rendered: bytes) -> ConversationEntry:
    return ConversationEntry(
        tenant_id="tenant",
        conversation_id="conversation",
        entry_id=f"assistant/{delivery_id}",
        sequence=sequence,
        origin_channel_id="channel",
        visible_channels=("channel",),
        role="assistant",
        accepted_bytes=rendered,
        envelope=envelope(rendered),
    )


def _batch_values(command_id: str, records: tuple[OwnerRecordBytes, ...]) -> dict[str, object]:
    digest = _sha(command_id.encode())
    return {
        "identity": {
            "tenant_id": "tenant",
            "command_id": command_id,
            "command_fingerprint": digest,
            "canonicalization_version": "chiplog.owner-publication.v1",
        },
        "authentication": {
            "invocation": {
                "issuance_id": "fixture-issued",
                "issuance_fingerprint": _sha(b"fixture-issued"),
                "broker_epoch": "fixture-epoch",
                "broker_session": "fixture-session",
                "runtime_generation": "fixture-generation",
                "operation_subject": command_id,
            },
            "applicability_schema": "fixture.v1",
            "applicability_bytes": b"fixture-applicability",
            "applicability_fingerprint": _sha(b"fixture-applicability"),
        },
        "expected": {
            "tenant_id": "tenant",
            "tenant_frontier": 0,
            "expected_materialization_commitment": _sha(b"fixture-materialization"),
            "registry_head": "fixture-registry",
            "registry_fingerprint": _sha(b"fixture-registry"),
            "ordered_heads": (),
            "complete_manifest_fingerprint": _sha(b"fixture-manifest"),
        },
        "complete_records": records,
        "complete_batch_fingerprint": "0" * 64,
    }


def _complete_batch(
    commands: tuple[OwnerCommandBytes, ...], records: tuple[OwnerRecordBytes, ...]
) -> CompleteDeliveryBatchV2:
    values = _batch_values("completion-accepted", records)
    values.update(
        loop_command=commands[0],
        conversation_command=commands[1],
        prepared_effects_commands=commands[2:-1],
        terminal_work_command=commands[-1],
    )
    placeholder = CompleteDeliveryBatchV2.model_validate(values)
    return placeholder.model_copy(
        update={"complete_batch_fingerprint": completion_batch_fingerprint(placeholder)}
    )


def _rejected_batch(
    commands: tuple[OwnerCommandBytes, ...], records: tuple[OwnerRecordBytes, ...]
) -> RejectedCompletionBatchV1:
    values = _batch_values("completion-rejected", records)
    values.update(
        loop_rejection_command=commands[0],
        rejected_terminalization_command=commands[1],
        conversation_no_change_command=commands[2],
        terminal_work_command=commands[3],
    )
    placeholder = RejectedCompletionBatchV1.model_validate(values)
    return placeholder.model_copy(
        update={"complete_batch_fingerprint": completion_batch_fingerprint(placeholder)}
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
    source_cut = ConversationSourceCutV1(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment=_sha(b"conversation-materialization"),
        expected_previous_entry=Present(head="principal/1", fingerprint=_sha(b"principal/1")),
        source_selected_decision=_loop_head("decision"),
        source_physical_record=_loop_head("physical"),
        source_schema_id=run.schema_id,
        source_bytes=run.canonical_bytes(),
        source=ConversationCompletionSourceV1(
            captured_run=original_run, selected_attempt=selected_attempt
        ),
    )
    entries = tuple(
        ConversationCompletionEntryV1(
            delivery_id=item.delivery_id,
            recipient_binding=item.selection,
            entry=_conversation_entry(item.delivery_id, index + 2, item.rendered_bytes),
        )
        for index, item in enumerate(proposal.manifest.ordered_deliveries)
    )
    conversation_request = PrepareConversationCompletionV1(
        command_id="conversation-complete",
        source_cut=source_cut,
        original_completion_request_bytes=original.canonical_bytes(),
        loop_preparation_bytes=prepared.canonical_bytes(),
        original_delivery_proposal_bytes=proposal.canonical_bytes(),
        proposed_terminal_run=terminal,
        proposed_terminal_run_head=run_ref(terminal),
        proposed_acceptance_head=proposal.acceptance,
        proposed_terminal_manifest=manifest,
        proposed_terminal_manifest_head=manifest_ref(manifest),
        proposed_accepted_delivery_manifest_bytes=proposal.manifest.canonical_bytes(),
        ordered_assistant_entries=entries,
    )
    conversation_fp = conversation_source_request_fingerprint(conversation_request)
    conversation_members = tuple(
        make_conversation_canonical_member(
            source_kind="COMPLETION",
            source_request_fingerprint=conversation_fp,
            entry=item.entry,
            delivery_id=item.delivery_id,
            recipient_binding_json=conversation_recipient_binding_json(item.recipient_binding),
            source_heads_json=conversation_source_heads_json(
                source_cut.source_selected_decision,
                source_cut.source_physical_record,
                source_cut.expected_previous_entry,
            ),
        )
        for item in entries
    )
    conversation_result = PreparedConversationCompletionV1(
        source_request_fingerprint=conversation_fp,
        ordered_members=conversation_members,
        complete_owner_commitment=conversation_complete_owner_commitment(conversation_members),
    )
    effects = tuple(
        _effects_exchange(index, completion, observation, proposal, delivery)
        for index, delivery in enumerate(proposal.manifest.ordered_deliveries)
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
    work_result = _work(work_request)
    assembly = PrepareCompleteAcceptanceAssemblyV1(
        original_completion_request=original,
        prepared_completion=prepared,
        conversation_request=conversation_request,
        conversation_result=conversation_result,
        ordered_effects=effects,
        work_source=work_source,
        terminal_work_request=work_request,
        terminal_work_result=work_result,
    )
    effect_members = tuple(make_scoped_intent_member(item.intent_result.intent) for item in effects)
    records = (
        _owner("agent_loop", delivery_member),
        _owner("agent_loop", manifest_member),
        _owner("agent_loop", run_member),
        *(_owner("conversation", item) for item in conversation_members),
        *(_owner("effects", item) for item in effect_members),
        *(_owner("agent_loop", item) for item in work_result.complete_records),
    )
    commands = acceptance_commands(assembly)
    return AcceptedAssemblyFixture(
        assembly=assembly,
        batch=_complete_batch(commands, records),
        expected_commands=commands,
        expected_records=records,
    )


async def rejected_completion_fixture(
    run_schema: Literal["v2", "v3"] = "v3",
) -> RejectedAssemblyFixture:
    """Return the semantic-rejection graph: aborted Run, no conversation/effects rows."""
    accepted = await accepted_completion_fixture(run_schema, "empty")
    original = accepted.assembly.original_completion_request
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
    accepted_cut = accepted.assembly.conversation_request.source_cut
    conversation_request = PrepareConversationCompletionRejectedV1(
        command_id="conversation-reject",
        source_cut=accepted_cut,
        original_completion_request_bytes=original.canonical_bytes(),
        loop_rejection_bytes=reject.canonical_bytes(),
        proposed_terminal_run=aborted,
        proposed_terminal_run_head=terminalization_result.terminal_run_head,
        preserved_trace=reject.preserved_trace,
    )
    conversation_result = PreparedConversationCompletionRejectedV1(
        source_request_fingerprint=conversation_source_request_fingerprint(conversation_request),
        no_conversation_change_commitment=conversation_no_change_commitment(),
    )
    source = RejectedCompletionWorkSourceV1(
        original_completion_request_bytes=original.canonical_bytes(),
        original_completion_reject_bytes=reject.canonical_bytes(),
        rejected_terminalization_request_bytes=terminalization_request.canonical_bytes(),
        rejected_terminalization_result_bytes=terminalization_result.canonical_bytes(),
        terminal_manifest=manifest,
        terminal_manifest_head=terminalization_result.terminal_manifest_head,
        terminal_run=aborted,
        terminal_run_head=terminalization_result.terminal_run_head,
        ordered_open_obligations=(),
    )
    work_request = PrepareTerminalWork(
        identity=WorkCommandIdentity(tenant_id="tenant", command_id="rejected-terminal-work"),
        terminal_run=aborted,
        original_terminalization_request=completion_terminal_work_source_bytes(source),
        terminal_manifest=terminalization_result.terminal_manifest_head,
        ordered_open_obligations=(),
    )
    work_result = _work(work_request)
    assembly = PrepareRejectedCompletionAssemblyV1(
        original_completion_request=original,
        completion_reject=reject,
        rejected_terminalization_request=terminalization_request,
        rejected_terminalization_result=terminalization_result,
        conversation_request=conversation_request,
        conversation_result=conversation_result,
        work_source=source,
        terminal_work_request=work_request,
        terminal_work_result=work_result,
    )
    rejection_member = make_completion_rejection_member(original, reject)
    records = (
        _owner("agent_loop", rejection_member),
        _owner("agent_loop", manifest_member),
        _owner("agent_loop", run_member),
        *(_owner("agent_loop", item) for item in work_result.complete_records),
    )
    commands = rejected_commands(assembly)
    return RejectedAssemblyFixture(
        assembly=assembly,
        batch=_rejected_batch(commands, records),
        expected_commands=commands,
        expected_records=records,
    )


@dataclass(frozen=True)
class AcceptedAssemblyFixture:
    assembly: PrepareCompleteAcceptanceAssemblyV1
    batch: CompleteDeliveryBatchV2
    expected_commands: tuple[OwnerCommandBytes, ...]
    expected_records: tuple[OwnerRecordBytes, ...]


@dataclass(frozen=True)
class RejectedAssemblyFixture:
    assembly: PrepareRejectedCompletionAssemblyV1
    batch: RejectedCompletionBatchV1
    expected_commands: tuple[OwnerCommandBytes, ...]
    expected_records: tuple[OwnerRecordBytes, ...]
