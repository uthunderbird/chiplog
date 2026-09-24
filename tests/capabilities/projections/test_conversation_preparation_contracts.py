"""Consumer checks for inert v2 conversation-preparation members."""

from __future__ import annotations

import hashlib
import json
from base64 import b64decode, b64encode

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    ModelSelection,
    OriginSelection,
    ProviderRecipient,
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
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    ExecutionTerminalManifest,
    RecoverySourceRecord,
)
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    FrozenRunBindings,
    RecoveryFrontier,
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
    CompletionPreparationResultV1,
    CompletionRejectedPreparationResultV1,
    ConversationAdmittedInputSourceV1,
    ConversationCanonicalMemberV2,
    ConversationCompletionEntryV1,
    ConversationCompletionSourceV1,
    ConversationPreparationIntegrityError,
    ConversationPreparationRequestV1,
    ConversationSourceCutV1,
    PrepareConversationAdmittedInputV1,
    PrepareConversationCompletionRejectedV1,
    PrepareConversationCompletionV1,
    PreparedConversationCompletionRejectedV1,
    PreparedConversationCompletionV1,
    conversation_complete_owner_commitment,
    conversation_no_change_commitment,
    conversation_recipient_binding_json,
    conversation_source_heads_json,
    conversation_source_request_fingerprint,
    decode_conversation_canonical_member,
    make_conversation_canonical_member,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from tests.support.delivery_completion import _captured as delivery_captured
from tests.support.execution_fan_out import fixture as execution_fixture
from tests.support.workspace import envelope


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _head(identity: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=identity,
        revision=Present(head=f"{identity}/head", fingerprint=_fingerprint(identity)),
    )


def _v3_source_run() -> ExecutionRunRecordV3:
    label = DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())
    artifact = ExecutionPromptArtifactV3(
        content_hash="a" * 64,
        library_version="test-v3",
        tools=EXECUTION_TOOLS_V3,
        response_schema_json=execution_response_schema_v3(),
        rendered="history-aware prompt",
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
            ("context", "history-aware prompt"),
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
    recipient = ProviderRecipient(
        provider_id="provider",
        account_id="account",
        recipient_id="recipient",
        endpoint=ExactHead(identity="endpoint", head="endpoint/head", fingerprint="a" * 64),
        canonical_address=b"recipient://exact",
        credential_binding=ExactHead(
            identity="credential", head="credential/head", fingerprint="b" * 64
        ),
    )
    pending = ExecutionRunRecordV3(
        tenant="tenant",
        principal="principal",
        run_id="run-v3",
        state="ACTIVE",
        head="pending",
        predecessor=None,
        prompt="history-aware prompt",
        policy=BudgetPolicy(),
        origin=OriginSelection(
            ingress_binding=ExactHead(
                identity="ingress", head="ingress/head", fingerprint="c" * 64
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


def _entry(*, entry_id: str, sequence: int, role: str = "principal") -> ConversationEntry:
    return ConversationEntry(
        tenant_id="tenant",
        conversation_id="conversation",
        entry_id=entry_id,
        sequence=sequence,
        origin_channel_id="channel",
        visible_channels=("channel",),
        role=role,  # type: ignore[arg-type]
        accepted_bytes=b"exact accepted bytes",
        envelope=envelope(b"exact accepted bytes"),
    )


def _recipient(kind: str = "ORIGIN_EXACT") -> OriginSelection | ModelSelection:
    recipient = ProviderRecipient(
        provider_id="provider",
        account_id="account",
        recipient_id="recipient",
        endpoint=ExactHead(identity="endpoint", head="endpoint/head", fingerprint="a" * 64),
        canonical_address=b"recipient://exact",
        credential_binding=ExactHead(
            identity="credential", head="credential/head", fingerprint="b" * 64
        ),
    )
    if kind == "ORIGIN_EXACT":
        return OriginSelection(
            ingress_binding=ExactHead(
                identity="ingress", head="ingress/head", fingerprint="c" * 64
            ),
            recipient=recipient,
        )
    return ModelSelection(recipient=recipient)


def _source_heads(previous: Absent | Present | None = None) -> str:
    if previous is None:
        previous = Absent()
    return conversation_source_heads_json(_head("decision"), _head("physical"), previous)


def _admitted_input(selected: CallSubjectHead) -> SelectedAdmittedRunInput:
    return SelectedAdmittedRunInput(
        tenant_id="tenant",
        database_id="database",
        source_class="CLI",
        source_contract=_head("contract"),
        token=_head("token"),
        custody=_head("custody"),
        inbox=_head("inbox"),
        selected_decision=selected,
        physical_record=_head("physical"),
        commit_sequence=1,
        raw_input_bytes=b"same text",
        custody_schema="custody.v1",
        canonical_custody_record=b"custody",
        inbox_schema="inbox.v1",
        canonical_inbox_record=b"inbox",
        source_authentication=_head("authentication"),
        authentication_schema="authentication.v1",
        canonical_authentication=b"authentication",
        normalization=_head("normalization"),
        normalization_schema="normalization.v1",
        canonical_normalization_record=b"normalization",
        normalized_prompt="same text",
        principal_id="principal",
        contour_head="contour",
        origin=OriginSelection(
            ingress_binding=ExactHead(
                identity="ingress", head="ingress/head", fingerprint="c" * 64
            ),
            recipient=ProviderRecipient(
                provider_id="provider",
                account_id="account",
                recipient_id="recipient",
                endpoint=ExactHead(
                    identity="endpoint", head="endpoint/head", fingerprint="a" * 64
                ),
                canonical_address=b"recipient://exact",
                credential_binding=ExactHead(
                    identity="credential", head="credential/head", fingerprint="b" * 64
                ),
            ),
        ),
    )


def _admitted_request(selected: CallSubjectHead) -> PrepareConversationAdmittedInputV1:
    admitted = _admitted_input(selected)
    return PrepareConversationAdmittedInputV1(
        command_id="command",
        source_cut=ConversationSourceCutV1(
            tenant_id="tenant",
            database_id="database",
            tenant_commit_sequence=1,
            materialization_commitment="e" * 64,
            expected_previous_entry=Absent(),
            source_selected_decision=selected,
            source_physical_record=_head("physical"),
            source_schema_id="chiplog.agent-loop.selected-admitted-run-input.v1",
            source_bytes=admitted.canonical_bytes(),
            source=ConversationAdmittedInputSourceV1(selected_admitted_input=selected),
        ),
        original_selected_admitted_input_bytes=admitted.canonical_bytes(),
        original_selected_admitted_input_head=selected,
        original_normalized_principal_text_bytes=b"same text",
        origin_channel_id="channel",
        visible_channels=("channel",),
        envelope=envelope(b"same text"),
        proposed_entry=_entry(entry_id="principal/1", sequence=1),
    )


def _completion_cut(run: ExecutionRun) -> ExecutionRecoveryCut:
    return ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment="d" * 64,
        current_run=_head("captured-run"),
        complete_ordered_run_lineage=(run,),
        complete_ordered_responses=(),
        frontier=RecoveryFrontier(
            tenant_id="tenant",
            run_id="run",
            tenant_commit_sequence=4,
            registry=RecoveryFrontierRegistry(
                registry_id="registry",
                version="1",
                fingerprint="a" * 64,
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
                    subject="run",
                    branch="ACTIVE",
                    ordered_heads=(_head("captured-run").revision,),
                    fingerprint="b" * 64,
                ),
            ),
            ordered_calls=(),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="c" * 64,
        ),
        current_bindings=FrozenRunBindings(
            objective="Plan",
            requested_work="Plan",
            prompt_artifact=Present(head="prompt", fingerprint="1" * 64),
            ordered_tool_specs=(Present(head="tool", fingerprint="2" * 64),),
            generated_schema=Present(head="schema", fingerprint="3" * 64),
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=Present(head="scope", fingerprint="4" * 64),
            authority_mandate_heads=(),
            policy=Present(head="policy", fingerprint="5" * 64),
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=_head("source"),
                schema_id="source.v1",
                canonical_record_bytes=b"source",
                selected_decision=_head("decision"),
                physical_record=_head("physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="e" * 64,
    )


def _terminal_run(source: ExecutionRun, state: str, event: str) -> ExecutionRun:
    wire = source.model_dump(mode="json")
    wire.update(state=state, event=event, head=f"terminal:{state.lower()}")
    return TypeAdapter(ExecutionRun).validate_json(json.dumps(wire))


async def _completion_inputs(
    source_run: ExecutionRun | None = None,
) -> tuple[
    PrepareConversationCompletionV1,
    PrepareConversationCompletionRejectedV1,
]:
    captured = await execution_fixture(complete=True)
    run = captured.captured_run if source_run is None else source_run
    succeeded = _terminal_run(run, "SUCCEEDED", "ExecutionCompleted")
    aborted = _terminal_run(run, "ABORTED", "ExecutionRejected")
    _, delivery = await delivery_captured()
    raw = delivery.captured_response
    original = PrepareExecutionCompletion(
        command_id="complete",
        run=run,
        selected_attempt=_head("selected-attempt"),
        selector_generation=0,
        visibility_manifest=_head("visibility"),
        exact_captured_response=raw,
        cut=_completion_cut(run),
        complete_earlier_continuations=(),
        delivery=delivery,
        fence=captured.request.cut.fence,
    )
    proposal = prepare_delivery_completion(DeliveryCompletion.model_validate_json(raw), delivery)
    manifest = ExecutionTerminalManifest(
        manifest_id="terminal-manifest",
        command_id="complete",
        prior_run=_head("captured-run"),
        target="SUCCEEDED",
        source_cut_fingerprint="a" * 64,
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    prepared = PreparedExecutionCompletion(
        source_request_fingerprint="b" * 64,
        complete_earlier_continuations=(),
        delivery=proposal,
        terminal_manifest=manifest,
        run=succeeded,
        complete_owner_commitment="c" * 64,
    )
    cut = ConversationSourceCutV1(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment="d" * 64,
        expected_previous_entry=Present(head="principal/1", fingerprint="e" * 64),
        source_selected_decision=_head("decision"),
        source_physical_record=_head("physical"),
        source_schema_id=run.schema_id,
        source_bytes=run.canonical_bytes(),
        source=ConversationCompletionSourceV1(
            captured_run=_head("captured-run"), selected_attempt=_head("selected-attempt")
        ),
    )
    entries = tuple(
        ConversationCompletionEntryV1(
            delivery_id=accepted.delivery_id,
            recipient_binding=accepted.selection,
            entry=_entry(
                entry_id=f"assistant/{accepted.delivery_id}",
                sequence=index + 2,
                role="assistant",
            ).model_copy(update={"accepted_bytes": accepted.rendered_bytes}),
        )
        for index, accepted in enumerate(proposal.manifest.ordered_deliveries)
    )
    success = PrepareConversationCompletionV1(
        command_id="conversation-complete",
        source_cut=cut,
        original_completion_request_bytes=original.canonical_bytes(),
        loop_preparation_bytes=prepared.canonical_bytes(),
        original_delivery_proposal_bytes=proposal.canonical_bytes(),
        proposed_terminal_run=succeeded,
        proposed_terminal_run_head=_head("proposed-terminal-run"),
        proposed_acceptance_head=proposal.acceptance,
        proposed_terminal_manifest=manifest,
        proposed_terminal_manifest_head=_head("proposed-terminal-manifest"),
        proposed_accepted_delivery_manifest_bytes=proposal.manifest.canonical_bytes(),
        ordered_assistant_entries=entries,
    )
    rejected = PreparedExecutionCompletionReject(
        source_request_fingerprint="f" * 64,
        original_captured_attempt=_head("selected-attempt"),
        preserved_trace=_head("preserved-trace"),
        visibility_manifest=_head("visibility"),
        reasons=("SCHEMA",),
        run=aborted,
        complete_owner_commitment="0" * 64,
    )
    failure = PrepareConversationCompletionRejectedV1(
        command_id="conversation-reject",
        source_cut=cut,
        original_completion_request_bytes=original.canonical_bytes(),
        loop_rejection_bytes=rejected.canonical_bytes(),
        proposed_terminal_run=aborted,
        proposed_terminal_run_head=_head("proposed-terminal-run"),
        preserved_trace=rejected.preserved_trace,
    )
    return success, failure


def test_admitted_records_bind_identical_text_to_distinct_selected_heads() -> None:
    first = make_conversation_canonical_member(
        source_kind="ADMITTED_INPUT",
        source_request_fingerprint="1" * 64,
        entry=_entry(entry_id="principal/1", sequence=1),
        delivery_id=None,
        recipient_binding_json=None,
        source_heads_json=_source_heads(),
    )
    second = make_conversation_canonical_member(
        source_kind="ADMITTED_INPUT",
        source_request_fingerprint="2" * 64,
        entry=_entry(entry_id="principal/1", sequence=1),
        delivery_id=None,
        recipient_binding_json=None,
        source_heads_json=conversation_source_heads_json(
            _head("decision/other"), _head("physical"), Absent()
        ),
    )

    assert first.fingerprint != second.fingerprint
    decoded = decode_conversation_canonical_member(first)
    assert decoded.entry.accepted_bytes == b"exact accepted bytes"


def test_admitted_requests_with_identical_text_and_distinct_heads_have_distinct_fingerprints(
) -> None:
    first = _admitted_request(_head("selected/one"))
    second = _admitted_request(_head("selected/two"))

    assert (
        first.original_normalized_principal_text_bytes
        == second.original_normalized_principal_text_bytes
    )
    first_fingerprint = conversation_source_request_fingerprint(first)
    second_fingerprint = conversation_source_request_fingerprint(second)
    assert first_fingerprint != second_fingerprint


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_completion_success_request_and_result_unions_retain_exact_owner_bytes(
    version: str,
) -> None:
    request, _ = await _completion_inputs(_v3_source_run() if version == "v3" else None)
    request_adapter: TypeAdapter[ConversationPreparationRequestV1] = TypeAdapter(
        ConversationPreparationRequestV1
    )
    assert request_adapter.validate_json(request.canonical_json_bytes()) == request
    assert isinstance(request.source_cut.source, ConversationCompletionSourceV1)
    assert request.source_cut.source.captured_run != request.proposed_terminal_run_head
    assert request.source_cut.source.selected_attempt.subject_id == "selected-attempt"
    assert request.source_cut.source_schema_id == request.proposed_terminal_run.schema_id
    assert request.proposed_terminal_run.state == "SUCCEEDED"
    original = PrepareExecutionCompletion.model_validate_json(
        request.original_completion_request_bytes
    )
    assert original.run.state == "ACTIVE"
    if version == "v3":
        response = original.run.turns[-1].attempts[-1].response_base64
        assert response is not None
        assert DeliveryCompletion.model_validate_json(b64decode(response)).turn_id == "turn-v3"
    assert request.proposed_accepted_delivery_manifest_bytes
    assert len(request.ordered_assistant_entries) == len(
        json.loads(request.original_delivery_proposal_bytes)["manifest"]["ordered_deliveries"]
    )

    source_fingerprint = conversation_source_request_fingerprint(request)
    members = tuple(
        make_conversation_canonical_member(
            source_kind="COMPLETION",
            source_request_fingerprint=source_fingerprint,
            entry=item.entry,
            delivery_id=item.delivery_id,
            recipient_binding_json=conversation_recipient_binding_json(item.recipient_binding),
            source_heads_json=conversation_source_heads_json(
                request.source_cut.source_selected_decision,
                request.source_cut.source_physical_record,
                request.source_cut.expected_previous_entry,
            ),
        )
        for item in request.ordered_assistant_entries
    )
    result = PreparedConversationCompletionV1(
        source_request_fingerprint=source_fingerprint,
        ordered_members=members,
        complete_owner_commitment=conversation_complete_owner_commitment(members),
    )
    result_adapter: TypeAdapter[CompletionPreparationResultV1] = TypeAdapter(
        CompletionPreparationResultV1
    )
    assert result_adapter.validate_json(result.canonical_json_bytes()) == result


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_completion_rejection_request_and_result_unions_have_zero_assistant_entries(
    version: str,
) -> None:
    _, request = await _completion_inputs(_v3_source_run() if version == "v3" else None)
    request_adapter: TypeAdapter[ConversationPreparationRequestV1] = TypeAdapter(
        ConversationPreparationRequestV1
    )
    assert request_adapter.validate_json(request.canonical_json_bytes()) == request
    assert isinstance(request.source_cut.source, ConversationCompletionSourceV1)
    assert request.source_cut.source.captured_run != request.proposed_terminal_run_head
    assert request.source_cut.source_schema_id == request.proposed_terminal_run.schema_id
    assert request.proposed_terminal_run.state == "ABORTED"
    assert (
        PrepareExecutionCompletion.model_validate_json(request.original_completion_request_bytes).run.state
        == "ACTIVE"
    )
    assert request.preserved_trace.subject_id == "preserved-trace"

    result = PreparedConversationCompletionRejectedV1(
        source_request_fingerprint=conversation_source_request_fingerprint(request),
        no_conversation_change_commitment=conversation_no_change_commitment(),
    )
    result_adapter: TypeAdapter[CompletionRejectedPreparationResultV1] = TypeAdapter(
        CompletionRejectedPreparationResultV1
    )
    assert result_adapter.validate_json(result.canonical_json_bytes()) == result
    assert result.ordered_members == ()


def test_source_cut_rejects_unregistered_schema_and_schema_body_mismatch() -> None:
    request = _admitted_request(_head("selected"))
    wire = request.source_cut.model_dump()
    wire["source_schema_id"] = "chiplog.agent-loop.execution-record.v2"
    with pytest.raises(ValidationError):
        ConversationSourceCutV1.model_validate(wire)
    wire["source_schema_id"] = "foreign.schema.v1"
    with pytest.raises(ValidationError):
        ConversationSourceCutV1.model_validate(wire)

    v3 = _v3_source_run()
    completion_source = ConversationCompletionSourceV1(
        captured_run=_head("captured-v3"), selected_attempt=_head("attempt-v3")
    )
    v3_cut = ConversationSourceCutV1(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="a" * 64,
        expected_previous_entry=Absent(),
        source_selected_decision=_head("decision"),
        source_physical_record=_head("physical"),
        source_schema_id="chiplog.agent-loop.execution-record.v3",
        source_bytes=v3.canonical_bytes(),
        source=completion_source,
    )
    assert v3_cut.source_schema_id == v3.schema_id
    mismatch = v3_cut.model_dump()
    mismatch["source_schema_id"] = "chiplog.agent-loop.execution-record.v2"
    with pytest.raises(ValidationError):
        ConversationSourceCutV1.model_validate(mismatch)
    noncanonical = v3_cut.model_dump()
    noncanonical["source_bytes"] = json.dumps(
        json.loads(v3.canonical_bytes()), indent=2
    ).encode()
    with pytest.raises(ValidationError):
        ConversationSourceCutV1.model_validate(noncanonical)


@pytest.mark.parametrize("kind", ("ORIGIN_EXACT", "MODEL_SELECTED_EXACT"))
def test_completion_member_preserves_one_recipient_bound_entry_per_delivery(kind: str) -> None:
    entry = _entry(entry_id=f"assistant/{kind}", sequence=2, role="assistant")
    binding = conversation_recipient_binding_json(_recipient(kind))
    member = make_conversation_canonical_member(
        source_kind="COMPLETION",
        source_request_fingerprint="3" * 64,
        entry=entry,
        delivery_id=f"delivery/{kind}",
        recipient_binding_json=binding,
        source_heads_json=_source_heads(Present(head="principal/1", fingerprint="d" * 64)),
    )

    decoded = decode_conversation_canonical_member(member)
    assert decoded.entry == entry
    assert decoded.entry.sequence == 2
    assert decoded.delivery_id == f"delivery/{kind}"
    assert decoded.recipient_binding_json == binding


@pytest.mark.parametrize(
    "mutate",
    (
        lambda member: member.model_copy(update={"fingerprint": "0" * 64}),
        lambda member: member.model_copy(update={"schema_id": "chiplog.conversation.v1"}),
        lambda member: member.model_copy(update={"canonical_bytes": b"{not-json"}),
    ),
)
def test_v2_decoder_rejects_fingerprint_schema_and_malformed_bytes(mutate: object) -> None:
    member = make_conversation_canonical_member(
        source_kind="ADMITTED_INPUT",
        source_request_fingerprint="4" * 64,
        entry=_entry(entry_id="principal/1", sequence=1),
        delivery_id=None,
        recipient_binding_json=None,
        source_heads_json=_source_heads(),
    )
    with pytest.raises(ConversationPreparationIntegrityError):
        decode_conversation_canonical_member(mutate(member))  # type: ignore[operator]


def test_v2_decoder_rejects_noncanonical_outer_json_and_legacy_golden_shape() -> None:
    member = make_conversation_canonical_member(
        source_kind="ADMITTED_INPUT",
        source_request_fingerprint="5" * 64,
        entry=_entry(entry_id="principal/1", sequence=1),
        delivery_id=None,
        recipient_binding_json=None,
        source_heads_json=_source_heads(),
    )
    noncanonical = json.dumps(json.loads(member.canonical_bytes), indent=2).encode()
    altered = member.model_copy(
        update={
            "canonical_bytes": noncanonical,
            "fingerprint": hashlib.sha256(noncanonical).hexdigest(),
        }
    )
    with pytest.raises(ConversationPreparationIntegrityError, match="canonical"):
        decode_conversation_canonical_member(altered)

    legacy = b'{"entry_json":"{}","fingerprint":"' + b"0" * 64 + b'"}'
    legacy_member = ConversationCanonicalMemberV2(
        record_id="legacy",
        canonical_bytes=legacy,
        fingerprint=hashlib.sha256(legacy).hexdigest(),
    )
    with pytest.raises(ConversationPreparationIntegrityError):
        decode_conversation_canonical_member(legacy_member)
    assert ACCEPTED_ENTRY_SCHEMA == "chiplog.conversation.accepted-entry.v2"


def test_v2_decoder_rejects_extra_payload_field_and_entry_without_role() -> None:
    member = make_conversation_canonical_member(
        source_kind="ADMITTED_INPUT",
        source_request_fingerprint="7" * 64,
        entry=_entry(entry_id="principal/1", sequence=1),
        delivery_id=None,
        recipient_binding_json=None,
        source_heads_json=_source_heads(),
    )
    payload = json.loads(member.canonical_bytes)
    payload["unexpected"] = True
    extra = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ConversationPreparationIntegrityError):
        decode_conversation_canonical_member(
            member.model_copy(
                update={"canonical_bytes": extra, "fingerprint": hashlib.sha256(extra).hexdigest()}
            )
        )

    payload = json.loads(member.canonical_bytes)
    entry = json.loads(payload["entry_json"])
    del entry["role"]
    payload["entry_json"] = json.dumps(entry, separators=(",", ":"), sort_keys=True)
    missing_role = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ConversationPreparationIntegrityError):
        decode_conversation_canonical_member(
            member.model_copy(
                update={
                    "canonical_bytes": missing_role,
                    "fingerprint": hashlib.sha256(missing_role).hexdigest(),
                }
            )
        )


def test_request_decoder_discriminates_admitted_and_rejected_completion_variants() -> None:
    request = _admitted_request(_head("selected"))
    adapter: TypeAdapter[ConversationPreparationRequestV1] = TypeAdapter(
        ConversationPreparationRequestV1
    )
    assert adapter.validate_json(request.canonical_json_bytes()) == request
    substituted = request.model_dump()
    substituted["kind"] = "PREPARE_CONVERSATION_COMPLETION_REJECTED_V1"
    with pytest.raises(ValidationError):
        adapter.validate_python(substituted)


def test_semantic_rejection_has_zero_conversation_members() -> None:
    result = PreparedConversationCompletionRejectedV1(
        source_request_fingerprint="8" * 64,
        no_conversation_change_commitment="9" * 64,
    )
    assert result.ordered_members == ()


def test_completion_and_admitted_variants_reject_wrong_delivery_shape() -> None:
    with pytest.raises(ConversationPreparationIntegrityError):
        make_conversation_canonical_member(
            source_kind="ADMITTED_INPUT",
            source_request_fingerprint="6" * 64,
            entry=_entry(entry_id="principal/1", sequence=1),
            delivery_id="delivery",
            recipient_binding_json=conversation_recipient_binding_json(_recipient()),
            source_heads_json=_source_heads(),
        )
    with pytest.raises(ConversationPreparationIntegrityError):
        make_conversation_canonical_member(
            source_kind="COMPLETION",
            source_request_fingerprint="6" * 64,
            entry=_entry(entry_id="assistant/1", sequence=2, role="assistant"),
            delivery_id=None,
            recipient_binding_json=None,
            source_heads_json=_source_heads(),
        )
