"""Focused consumer evidence for the inert v3 executable-history wire."""

import base64
import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallInventorySnapshot,
    CallPreparationCut,
    CallSubjectHead,
    FanOutPreparationRequest,
    InitializedCallRecord,
    OriginalCallKey,
    PreparedCallFanOut,
    SealedCallInput,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    ToolCall,
    ToolSpec,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    ExecutionPromptArtifact,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.execution_history_contracts import (
    EXECUTION_TOOLS_V3,
    ExecutionContinueV3,
    ExecutionModelAttemptV3,
    ExecutionPromptArtifactV3,
    ExecutionRunRecordV3,
    ExecutionTurnV3,
    ExecutionVisibilityManifestV3,
    execution_response_adapter_v3,
    execution_response_schema_v3,
)
from chiplog.capabilities.agent_loop.execution_history_fan_out_contracts import (
    ExecutionCapturedFanOutProposalV3,
    ExecutionCapturedFanOutRequestV3,
    ExecutionCapturedFanOutResultV3,
)
from chiplog.capabilities.agent_loop.execution_history_transition_contracts import (
    AccumulateExecutionVisibilityV3,
    ActivateExecutionRunV3,
    CaptureExecutionResponseV3,
    CreateExecutionRunV3,
    EmitExecutionAttemptV3,
    ExecutionTransitionProposalV3,
    ExecutionTransitionRequestV3,
    ExecutionTransitionResultV3,
    PrepareExecutionRequestV3,
    StartInitialExecutionTurnV3,
)
from chiplog.capabilities.agent_loop.execution_parsing import (
    EXECUTION_TOOLS,
    execution_response_schema,
    parse_execution_response,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import (
    FanOutToolPolicy,
    FanOutToolRegistry,
    ReadOnlyFanOutPolicy,
)
from chiplog.capabilities.agent_loop.readonly_execution_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(subject: str, raw: bytes | None = None) -> CallSubjectHead:
    digest = _digest(raw if raw is not None else subject.encode())
    return CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def _exact(identity: str) -> ExactHead:
    digest = _digest(identity.encode())
    return ExactHead(identity=identity, head="head:" + identity, fingerprint=digest)


def _artifact() -> ExecutionPromptArtifactV3:
    return ExecutionPromptArtifactV3(
        content_hash="a" * 64,
        library_version="test-v3",
        tools=EXECUTION_TOOLS_V3,
        response_schema_json=execution_response_schema_v3(),
        rendered="history-aware prompt",
    )


def _member(surface: str, content: str, label: DisclosureLabel) -> VisibilityMember:
    return VisibilityMember(
        record_id=surface,
        revision_head="revision:" + surface,
        content=content,
        provenance_head="provenance:" + surface,
        label_head="label:" + surface,
        label=label,
        producer="fixture",
        surface=surface,
    )


def _run(response: bytes | None = None) -> ExecutionRunRecordV3:
    artifact = _artifact()
    label = DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())
    members = (
        _member("prompt", artifact.rendered, label),
        _member("schema", artifact.response_schema_json, label),
        _member("context", "history-aware prompt", label),
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
    attempt = ExecutionModelAttemptV3(
        attempt_id="attempt-v3",
        lineage_id="lineage-v3",
        generation=0,
        state="RESPONSE_CAPTURED" if response else "PREPARED_NOT_EMITTED",
        head="attempt-head",
        manifest=manifest,
        request="request",
        provider_contract="hermetic-model.v1",
        recipient="hermetic-model",
        live_model=None,
        worker_session="worker",
        response_base64=base64.b64encode(response).decode() if response else None,
        receipt="receipt" if response else None,
        rejection=None,
    )
    turn = ExecutionTurnV3(
        turn_id="turn-v3",
        ordinal=1,
        head="turn-head",
        state="RESPONSE_AVAILABLE" if response else "CALL_ACTIVE",
        accumulator=members,
        attempts=(attempt,),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    recipient = ProviderRecipient(
        provider_id="hermetic-local",
        account_id="account",
        recipient_id="principal",
        endpoint=_exact("endpoint"),
        canonical_address=b"local://principal",
        credential_binding=_exact("credential"),
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
        origin=OriginSelection(ingress_binding=_exact("ingress"), recipient=recipient),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        root_binding="NOT_APPLICABLE",
        turns=(turn,),
        delivery_acceptance=None,
        suspension_baseline=None,
        original_obligations=(),
        no_retry_references=(),
        event="ModelResponseCaptured" if response else "ModelAttemptPrepared",
    )
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _registry() -> FanOutToolRegistry:
    entries = []
    for tool in EXECUTION_TOOLS_V3:
        readonly = tool.name == "read_conversation_history"
        entries.append(
            FanOutToolPolicy(
                tool_name=tool.name,
                tool_version=tool.version,
                schema_id=tool.schema_id,
                tool_schema=_head("schema:" + tool.name, tool.canonical_bytes()),
                tool_policy=_head("policy:" + tool.name),
                classification="READ_ONLY"
                if readonly
                else "CONSEQUENTIAL"
                if tool.name == "request_self_effect"
                else "PROPOSAL_ONLY",
                retry_policy=ReadOnlyFanOutPolicy(
                    max_attempts=2,
                    budget_version="budget-v1",
                    reducer_id="history",
                    reducer_version="1",
                )
                if readonly
                else NotApplicable(),
            )
        )
    return FanOutToolRegistry(registry_id="registry-v3", version="1", entries=tuple(entries))


def _fanout_request(
    run: ExecutionRunRecordV3, response: ExecutionContinueV3
) -> ExecutionCapturedFanOutRequestV3:
    raw = response.canonical_bytes()
    captured = _head(run.run_id, run.canonical_bytes())
    registry = _registry()
    policies = {entry.tool_name: entry for entry in registry.entries}
    cut = CallPreparationCut(
        tenant_id=run.tenant,
        current_run=captured,
        run_state="ACTIVE",
        tenant_commit_sequence=1,
        materialization_commitment="b" * 64,
        complete_call_inventory=_head("inventory"),
        predecessor_inventory=CallInventorySnapshot(
            tenant_id=run.tenant, tenant_commit_sequence=1, ordered_calls=()
        ),
        authority_registry=_head("authority"),
        sources=(
            CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=_head("actor"),
                generation="0",
                frontier="1",
                canonical_value_base64="e30=",
                observed_at_ns=1,
                valid_until_ns=2,
            ),
        ),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id=run.worker_session,
            runtime_generation="0",
        ),
    )
    calls = tuple(
        SealedCallInput(
            original=OriginalCallKey(
                tenant_id=run.tenant,
                original_run_id=run.run_id,
                original_turn_id="turn-v3",
                captured_response=captured,
                ordinal=ordinal,
                model_call_label=call.call_id,
            ),
            classification=policies[call.tool].classification,
            tool_schema=policies[call.tool].tool_schema,
            tool_policy=policies[call.tool].tool_policy,
            retry_lineage=NotApplicable(),
            canonical_call_base64=base64.b64encode(call.canonical_bytes()).decode(),
        )
        for ordinal, call in enumerate(response.tool_calls)
    )
    request = FanOutPreparationRequest(
        command_id="fanout-v3",
        original_run_id=run.run_id,
        original_turn_id="turn-v3",
        captured_response=captured,
        canonical_response_base64=base64.b64encode(raw).decode(),
        ordered_calls=calls,
        bound=FanOutBound(
            max_call_count=8,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=131072,
            canonicalization_version="chiplog.recovery.frontier.v1",
        ),
        cut=cut,
    )
    return ExecutionCapturedFanOutRequestV3(
        request=request,
        captured_run=run,
        tool_registry=registry,
        tool_registry_head=_head(registry.registry_id, registry.canonical_bytes()),
    )


def _fanout_proposal(
    request: ExecutionCapturedFanOutRequestV3,
) -> ExecutionCapturedFanOutProposalV3:
    sealed_call = request.request.ordered_calls[0]
    initialized = InitializedCallRecord(
        original_call_id="call:" + sealed_call.original.digest(),
        call=sealed_call,
        predecessor=Absent(),
    )
    initialized_head = _head(initialized.original_call_id, initialized.canonical_bytes())
    response_seal = SealedResponseRecord(
        response_seal_id="response-seal:" + request.request.digest(),
        tenant_id=request.captured_run.tenant,
        original_run_id=request.captured_run.run_id,
        original_turn_id="turn-v3",
        captured_response=request.request.captured_response,
        complete_ordered_initialized=(initialized_head,),
        bound=request.request.bound,
    )
    fan_out = PreparedCallFanOut(
        source_request_fingerprint=request.request.digest(),
        response_seal=response_seal,
        initialized_records=(initialized,),
        complete_ordered_record_manifest=(
            _head(response_seal.response_seal_id, response_seal.canonical_bytes()),
            initialized_head,
        ),
        proposal_fingerprint="d" * 64,
    )
    return ExecutionCapturedFanOutProposalV3(
        source_request_fingerprint=request.digest(),
        fan_out=fan_out,
        sealed_run=request.captured_run,
        proposal_fingerprint="e" * 64,
    )


def test_v3_transition_and_captured_fanout_retain_history_call_and_v3_containment() -> None:
    response = ExecutionContinueV3(
        kind="Continue",
        tool_calls=(
            ReadOnlyHistoryToolCall(
                call_id="history-0",
                tool="read_conversation_history",
                arguments=ConversationHistoryQuery(limit=2, after_cursor=None),
            ),
        ),
    )
    assert execution_response_adapter_v3().validate_json(response.canonical_bytes()) == response
    run = _run(response.canonical_bytes())
    captured = _fanout_request(run, response)
    restored = ExecutionCapturedFanOutRequestV3.model_validate_json(captured.canonical_bytes())
    sealed = restored.request.ordered_calls[0]
    assert (
        sealed.canonical_call_base64
        == base64.b64encode(response.tool_calls[0].canonical_bytes()).decode()
    )
    assert sealed.original.model_call_label == "history-0"
    assert sealed.original.ordinal == 0
    assert sealed.classification == "READ_ONLY"
    assert sealed.tool_schema == next(
        item.tool_schema
        for item in restored.tool_registry.entries
        if item.tool_name == "read_conversation_history"
    )
    assert isinstance(
        next(
            item.retry_policy
            for item in restored.tool_registry.entries
            if item.tool_name == "read_conversation_history"
        ),
        ReadOnlyFanOutPolicy,
    )
    fan_out = _fanout_proposal(captured)
    assert (
        TypeAdapter(ExecutionCapturedFanOutResultV3).validate_json(fan_out.canonical_bytes())
        == fan_out
    )

    transition = PrepareExecutionRequestV3(
        command_id="prepare-v3", run=run, manifest=run.turns[0].attempts[0].manifest
    )
    transition_adapter: TypeAdapter[ExecutionTransitionRequestV3] = TypeAdapter(
        ExecutionTransitionRequestV3
    )
    assert transition_adapter.validate_json(transition.canonical_bytes()) == transition
    proposal = ExecutionTransitionProposalV3(
        source_request_fingerprint=transition.digest(), run=run, proposal_fingerprint="c" * 64
    )
    assert (
        TypeAdapter(ExecutionTransitionResultV3).validate_json(proposal.canonical_bytes())
        == proposal
    )


def test_v3_mixed_calls_preserve_order_and_complete_stays_completion_pending() -> None:
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
    restored_response = execution_response_adapter_v3().validate_json(response.canonical_bytes())
    assert restored_response == response
    assert isinstance(restored_response, ExecutionContinueV3)
    captured = _fanout_request(_run(restored_response.canonical_bytes()), restored_response)
    assert (
        ExecutionCapturedFanOutRequestV3.model_validate_json(captured.canonical_bytes()) == captured
    )
    assert tuple(item.original.model_call_label for item in captured.request.ordered_calls) == (
        "proposal",
        "effect",
        "history",
    )
    complete = DeliveryCompletion(
        tenant="tenant",
        run_id="run-v3",
        turn_id="turn-v3",
        deliveries=(ProposedDelivery(payload=(Commentary(text="done"),)),),
    )
    assert isinstance(
        TypeAdapter(ExecutionContinueV3 | DeliveryCompletion).validate_json(
            complete.canonical_bytes()
        ),
        DeliveryCompletion,
    )
    pending = _run(complete.canonical_bytes())
    assert pending.state == "ACTIVE"
    assert pending.turns[0].attempts[0].state == "RESPONSE_CAPTURED"
    assert pending.turns[0].state == "RESPONSE_AVAILABLE"


def test_v3_public_unions_reject_wrong_generator_and_nested_v2_substitution() -> None:
    run = _run()
    transition_adapter: TypeAdapter[ExecutionTransitionRequestV3] = TypeAdapter(
        ExecutionTransitionRequestV3
    )
    commands = (
        CreateExecutionRunV3(
            command_id="create",
            tenant="tenant",
            principal="principal",
            run_id="run-v3",
            prompt="prompt",
            policy=BudgetPolicy(),
            origin=run.origin,
            contour_head="contour",
            policy_head="policy",
            worker_session="worker",
        ),
        ActivateExecutionRunV3(command_id="activate", run=run),
        StartInitialExecutionTurnV3(command_id="start", run=run),
        AccumulateExecutionVisibilityV3(command_id="accumulate", run=run, members=()),
        PrepareExecutionRequestV3(
            command_id="prepare", run=run, manifest=run.turns[0].attempts[0].manifest
        ),
        EmitExecutionAttemptV3(command_id="emit", run=run),
        CaptureExecutionResponseV3(command_id="capture", run=run, raw=b"{}", receipt="receipt"),
    )
    for command in commands:
        assert transition_adapter.validate_json(command.canonical_bytes()) == command

    baseline = json.loads(ActivateExecutionRunV3(command_id="activate", run=run).canonical_bytes())
    assert transition_adapter.validate_json(json.dumps(baseline)) == ActivateExecutionRunV3(
        command_id="activate", run=run
    )
    for field, value in (
        ("generator_version", "chiplog.turn-schema.execution.v2"),
        ("tools", [json.loads(EXECUTION_TOOLS_V3[0].canonical_bytes())]),
        ("response_schema_json", "{}"),
    ):
        bad_generator = json.loads(json.dumps(baseline))
        bad_generator["run"]["turns"][0]["attempts"][0]["manifest"]["artifact"][field] = value
        with pytest.raises(ValidationError):
            transition_adapter.validate_json(json.dumps(bad_generator))

    v2_artifact = _v2_artifact()
    hybrid = json.loads(json.dumps(baseline))
    hybrid["run"]["turns"][0]["attempts"][0]["manifest"]["artifact"] = v2_artifact.model_dump(
        mode="json"
    )
    with pytest.raises(ValidationError):
        transition_adapter.validate_json(json.dumps(hybrid))
    captured = _fanout_request(
        run,
        ExecutionContinueV3(
            kind="Continue",
            tool_calls=(
                ReadOnlyHistoryToolCall(
                    call_id="history",
                    tool="read_conversation_history",
                    arguments=ConversationHistoryQuery(limit=1, after_cursor=None),
                ),
            ),
        ),
    )
    captured_baseline = json.loads(captured.canonical_bytes())
    assert (
        ExecutionCapturedFanOutRequestV3.model_validate_json(json.dumps(captured_baseline))
        == captured
    )
    bad_captured = json.loads(json.dumps(captured_baseline))
    bad_captured["captured_run"] = hybrid["run"]
    with pytest.raises(ValidationError):
        ExecutionCapturedFanOutRequestV3.model_validate_json(json.dumps(bad_captured))

    manifest = run.turns[0].attempts[0].manifest
    manifest_baseline = json.loads(manifest.canonical_bytes())
    assert (
        ExecutionVisibilityManifestV3.model_validate_json(json.dumps(manifest_baseline)) == manifest
    )
    manifest_baseline["call_slot"] = False
    with pytest.raises(ValidationError):
        ExecutionVisibilityManifestV3.model_validate_json(json.dumps(manifest_baseline))


def _v2_artifact() -> ExecutionPromptArtifact:
    return ExecutionPromptArtifact(
        content_hash="a" * 64,
        library_version="test-v2",
        tools=(ToolSpec(name="propose_intent", schema_id="chiplog.propose-intent.v1"),),
        response_schema_json="{}",
        rendered="v2",
    )


def test_v2_artifact_bytes_and_digest_are_unchanged_and_v2_has_no_history_tool() -> None:
    artifact = _v2_artifact()
    baseline = (
        b'{"content_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"generator_version":"chiplog.turn-schema.execution.v2","library_version":"test-v2",'
        b'"prompt_id":"chiplog.agent-loop","rendered":"v2","response_schema_json":"{}",'
        b'"tools":[{"name":"propose_intent","schema_id":"chiplog.propose-intent.v1","version":"1"}],'
        b'"version":"2"}'
    )
    assert artifact.canonical_bytes() == baseline
    assert artifact.digest() == _digest(baseline)
    with pytest.raises(ValidationError):
        ExecutionContinue.model_validate(
            {
                "kind": "Continue",
                "tool_calls": [
                    {
                        "call_id": "history",
                        "tool": "read_conversation_history",
                        "arguments": {"limit": 1, "after_cursor": None},
                    }
                ],
            }
        )
    registered_v2 = ExecutionPromptArtifact(
        content_hash="a" * 64,
        library_version="test-v2",
        tools=EXECUTION_TOOLS,
        response_schema_json=execution_response_schema(),
        rendered="v2",
    )
    with pytest.raises(ValidationError):
        parse_execution_response(
            ExecutionContinueV3(
                kind="Continue",
                tool_calls=(
                    ReadOnlyHistoryToolCall(
                        call_id="history",
                        tool="read_conversation_history",
                        arguments=ConversationHistoryQuery(limit=1, after_cursor=None),
                    ),
                ),
            ).canonical_bytes(),
            registered_v2,
        )
