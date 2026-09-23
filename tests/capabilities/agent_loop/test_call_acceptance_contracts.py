"""Public consumer wire shapes only; no authorization or reducer evidence."""

import base64
import json
from typing import Literal, get_type_hints

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    FanOutBound,
    InitializedCall,
    ReadOnlyRetryLineage,
)


def _head(subject: str) -> call.CallSubjectHead:
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head=subject + "/head", fingerprint="a" * 64)
    )


def _cut() -> call.CallPreparationCut:
    return call.CallPreparationCut(
        tenant_id="tenant",
        current_run=_head("successor-run"),
        run_state="ACTIVE",
        tenant_commit_sequence=12,
        materialization_commitment="b" * 64,
        complete_call_inventory=_head("call-inventory"),
        predecessor_inventory=call.CallInventorySnapshot(
            tenant_id="tenant",
            tenant_commit_sequence=12,
            ordered_calls=(),
        ),
        authority_registry=_head("authority-registry"),
        sources=(
            call.CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=_head("actor"),
                generation="generation",
                frontier="frontier",
                canonical_value_base64=base64.b64encode(b"actor-observation").decode(),
                observed_at_ns=10,
                valid_until_ns=20,
            ),
        ),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id="successor-run",
            run_head="successor-run/head",
            worker_session_id="worker",
            runtime_generation="generation",
        ),
    )


def _examples() -> tuple[RecoveryDTO, ...]:
    cut = _cut()
    bound = FanOutBound(
        max_call_count=3,
        max_manifest_bytes=4096,
        max_serialized_batch_bytes=16384,
        canonicalization_version="chiplog.recovery.frontier.v1",
    )
    entries: tuple[tuple[str, Literal["PROPOSAL_ONLY", "CONSEQUENTIAL", "READ_ONLY"]], ...] = (
        ("propose", "PROPOSAL_ONLY"),
        ("send", "CONSEQUENTIAL"),
        ("read", "READ_ONLY"),
    )
    retry = call.InitializedReadOnlyLineage(
        lineage=ReadOnlyRetryLineage(
            lineage_id="retry-lineage",
            original_call_id="call:2",
            max_attempts=3,
            budget_version="budget-v1",
            reducer_id="read-reducer",
            reducer_version="v1",
        )
    )
    calls = tuple(
        call.SealedCallInput(
            original=call.OriginalCallKey(
                tenant_id="tenant",
                original_run_id="original-run",
                original_turn_id="turn",
                captured_response=_head("capture"),
                ordinal=ordinal,
                model_call_label=label,
            ),
            classification=classification,
            tool_schema=_head("schema:" + label),
            tool_policy=_head("policy:" + label),
            canonical_call_base64=base64.b64encode(label.encode()).decode(),
            retry_lineage=retry if classification == "READ_ONLY" else NotApplicable(),
        )
        for ordinal, (label, classification) in enumerate(entries)
    )
    # Parse the wire input above through public DTOs; class labels carry no mandate.
    initialized = tuple(
        call.InitializedCallRecord(original_call_id=f"call:{i}", call=value, predecessor=Absent())
        for i, value in enumerate(calls)
    )
    initialized_heads = tuple(_head(row.original_call_id) for row in initialized)
    predecessor = call.CallLifecycleObservation(
        original_call_id=initialized[1].original_call_id,
        initialized=initialized_heads[1],
        initialized_record=initialized[1],
        acceptance=InitializedCall(initialized=initialized_heads[1].revision),
        terminal=Absent(),
    )
    fanout = call.FanOutPreparationRequest(
        command_id="fanout",
        original_run_id="original-run",
        original_turn_id="turn",
        captured_response=_head("capture"),
        canonical_response_base64="e30=",
        ordered_calls=calls,
        bound=bound,
        cut=cut,
    )
    seal = call.SealedResponseRecord(
        response_seal_id="seal",
        tenant_id="tenant",
        original_run_id="original-run",
        original_turn_id="turn",
        captured_response=_head("capture"),
        complete_ordered_initialized=initialized_heads,
        bound=bound,
    )
    prepared_fanout = call.PreparedCallFanOut(
        source_request_fingerprint="c" * 64,
        response_seal=seal,
        initialized_records=initialized,
        complete_ordered_record_manifest=(_head("seal"), *initialized_heads),
        proposal_fingerprint="d" * 64,
    )
    binding = call.ConsequentialAcceptanceBinding(
        original_call_id="call:1",
        original=calls[1].original,
        initialized=initialized_heads[1],
        tool_schema=calls[1].tool_schema,
        tool_policy=calls[1].tool_policy,
        external_intent=_head("external-intent"),
        dispatch_semantics=call.CallDispatchSemantics(
            normative_manifest=_head("normative"),
            reducer=_head("reducer"),
            transition_registry=_head("transition"),
            canonicalization=_head("codec"),
            adapter_contract=_head("adapter"),
        ),
        cut=cut,
    )
    acceptance = call.AcceptConsequentialCallRequest(
        command_id="accept",
        binding=binding,
        initialized_record=initialized[1],
    )
    accepted = call.ToolCallAcceptedRecord(
        accepted_id="accepted",
        source_command_id="accept",
        binding=binding,
        execution_intent_id="execution-intent",
    )
    execution = call.ToolExecutionIntentRecord(
        execution_intent_id="execution-intent",
        original_call_id="call:1",
        initialized=initialized_heads[1],
        accepted=_head("accepted"),
        external_intent=binding.external_intent,
    )
    prepared_acceptance = call.PreparedConsequentialAcceptance(
        source_request_fingerprint="c" * 64,
        accepted=accepted,
        execution_intent=execution,
        complete_acceptance_manifest=(
            _head("accepted"),
            _head("execution-intent"),
            binding.external_intent,
        ),
        proposal_fingerprint="d" * 64,
    )
    cancellation = call.CancelBeforeAcceptRequest(
        command_id="cancel",
        original_call_id="call:1",
        original=calls[1].original,
        initialized=initialized_heads[1],
        initialized_record=initialized[1],
        cancellation_act=_head("cancel-act"),
        cut=cut,
    )
    terminal = call.CancelledBeforeAcceptRecord(
        terminal_id="terminal",
        source_command_id="cancel",
        original_call_id="call:1",
        original=calls[1].original,
        initialized=initialized_heads[1],
        cancellation_act=_head("cancel-act"),
        cut=cut,
        not_executed_result_id="result",
    )
    result = call.NotExecutedCallResultRecord(
        result_id="result",
        original_call_id="call:1",
        initialized=initialized_heads[1],
        terminal=_head("terminal"),
        outcome="NOT_EXECUTED",
    )
    prepared_cancel = call.PreparedPreAcceptCancellation(
        source_request_fingerprint="c" * 64,
        terminal=terminal,
        result=result,
        complete_ordered_record_manifest=(_head("terminal"), _head("result")),
        proposal_fingerprint="d" * 64,
    )
    rejected = call.CallPreparationRejected(command_id="denied", code="HOLD", reason="unresolved")
    return (
        _head("reference"),
        retry,
        predecessor,
        cut.predecessor_inventory,
        calls[0].original,
        *calls,
        cut.sources[0],
        cut,
        fanout,
        *initialized,
        seal,
        binding.dispatch_semantics,
        binding,
        acceptance,
        accepted,
        execution,
        cancellation,
        terminal,
        result,
        prepared_fanout,
        prepared_acceptance,
        prepared_cancel,
        rejected,
    )


@pytest.mark.parametrize("value", _examples(), ids=lambda value: type(value).__name__)
def test_public_dto_roundtrips_is_frozen_and_rejects_extra_fields(value: RecoveryDTO) -> None:
    assert type(value).model_validate_json(value.canonical_bytes()) == value
    assert (
        type(value).model_validate_json(value.canonical_bytes()).canonical_bytes()
        == value.canonical_bytes()
    )
    fields = value.model_dump(mode="json")
    fields["unregistered"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        type(value).model_validate_json(json.dumps(fields))
    name = next(iter(type(value).model_fields))
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(value, name, getattr(value, name))


def test_heterogeneous_fanout_retains_order_and_original_identity() -> None:
    request = next(
        value for value in _examples() if isinstance(value, call.FanOutPreparationRequest)
    )
    assert tuple(value.classification for value in request.ordered_calls) == (
        "PROPOSAL_ONLY",
        "CONSEQUENTIAL",
        "READ_ONLY",
    )
    assert tuple(value.original.ordinal for value in request.ordered_calls) == (0, 1, 2)
    assert {value.original.original_run_id for value in request.ordered_calls} == {"original-run"}
    assert request.cut.current_run.subject_id == "successor-run"
    assert "current_run" not in call.OriginalCallKey.model_fields
    assert request.ordered_calls[1].original.kind == "chiplog.call.identity.v1"


@pytest.mark.parametrize("kind", ("COMMITTED", "EXACT_REPLAY", "UNKNOWN"))
def test_result_discriminator_has_no_committed_or_unknown_variant(kind: str) -> None:
    adapter: TypeAdapter[call.CallPreparationResult] = TypeAdapter(call.CallPreparationResult)
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_json(json.dumps({"kind": kind}))
    for value in _examples():
        if isinstance(
            value,
            (
                call.PreparedCallFanOut,
                call.PreparedConsequentialAcceptance,
                call.PreparedPreAcceptCancellation,
                call.CallPreparationRejected,
            ),
        ):
            assert adapter.validate_json(value.canonical_bytes()) == value


@pytest.mark.parametrize("field,value", (("classification", "EXECUTED"), ("kind", "OTHER")))
def test_unknown_call_class_or_identity_tag_is_rejected(field: str, value: str) -> None:
    sealed = next(item for item in _examples() if isinstance(item, call.SealedCallInput))
    raw = sealed.model_dump(mode="json")
    if field == "kind":
        raw["original"][field] = value
    else:
        raw[field] = value
    with pytest.raises(ValidationError, match="literal_error"):
        call.SealedCallInput.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("size", (0, 2, 4))
def test_acceptance_requires_singular_external_intent_and_three_manifest_members(size: int) -> None:
    value = next(
        item for item in _examples() if isinstance(item, call.PreparedConsequentialAcceptance)
    )
    assert tuple(head.subject_id for head in value.complete_acceptance_manifest) == (
        "accepted",
        "execution-intent",
        "external-intent",
    )
    raw = value.model_dump(mode="json")
    raw["complete_acceptance_manifest"] = [_head("member").model_dump(mode="json")] * size
    with pytest.raises(ValidationError):
        call.PreparedConsequentialAcceptance.model_validate_json(json.dumps(raw))
    binding = value.accepted.binding.model_dump(mode="json")
    binding["external_intent"] = [binding["external_intent"], binding["external_intent"]]
    with pytest.raises(ValidationError):
        call.ConsequentialAcceptanceBinding.model_validate_json(json.dumps(binding))


@pytest.mark.parametrize("size", (0, 1, 3))
def test_cancellation_requires_two_members_and_closed_not_executed_outcome(size: int) -> None:
    value = next(
        item for item in _examples() if isinstance(item, call.PreparedPreAcceptCancellation)
    )
    assert value.result.outcome == "NOT_EXECUTED"
    raw = value.model_dump(mode="json")
    raw["complete_ordered_record_manifest"] = [_head("member").model_dump(mode="json")] * size
    with pytest.raises(ValidationError):
        call.PreparedPreAcceptCancellation.model_validate_json(json.dumps(raw))
    raw = value.result.model_dump(mode="json")
    raw["outcome"] = "EXECUTED"
    with pytest.raises(ValidationError, match="literal_error"):
        call.NotExecutedCallResultRecord.model_validate_json(json.dumps(raw))


def test_preparation_protocol_exposes_only_preparation_and_rejection() -> None:
    expected = (
        (call.CallPreparationPort.prepare_fan_out, call.PreparedCallFanOut),
        (call.CallPreparationPort.prepare_acceptance, call.PreparedConsequentialAcceptance),
        (call.CallPreparationPort.prepare_cancellation, call.PreparedPreAcceptCancellation),
    )
    for method, prepared in expected:
        assert get_type_hints(method)["return"] == prepared | call.CallPreparationRejected
        assert "commit_sequence" not in prepared.model_fields
        assert "proposal_fingerprint" in prepared.model_fields
        assert "batch_fingerprint" not in prepared.model_fields


def test_zero_call_response_can_carry_only_its_seal() -> None:
    request = next(item for item in _examples() if isinstance(item, call.FanOutPreparationRequest))
    request_wire = request.model_dump(mode="json")
    request_wire["ordered_calls"] = []
    request_wire["canonical_response_base64"] = base64.b64encode(
        b'{"kind":"Complete","text":"Done"}'
    ).decode()
    empty_request = call.FanOutPreparationRequest.model_validate_json(json.dumps(request_wire))
    assert empty_request.ordered_calls == ()
    assert (
        call.FanOutPreparationRequest.model_validate_json(empty_request.canonical_bytes())
        == empty_request
    )
    prepared = next(item for item in _examples() if isinstance(item, call.PreparedCallFanOut))
    prepared_wire = prepared.model_dump(mode="json")
    prepared_wire["initialized_records"] = []
    prepared_wire["response_seal"]["complete_ordered_initialized"] = []
    prepared_wire["complete_ordered_record_manifest"] = [_head("seal").model_dump(mode="json")]
    empty_preparation = call.PreparedCallFanOut.model_validate_json(json.dumps(prepared_wire))
    assert empty_preparation.initialized_records == ()
    assert empty_preparation.response_seal.complete_ordered_initialized == ()
    assert empty_preparation.complete_ordered_record_manifest == (_head("seal"),)
    assert (
        call.PreparedCallFanOut.model_validate_json(empty_preparation.canonical_bytes())
        == empty_preparation
    )
    prepared_wire["complete_ordered_record_manifest"] = []
    with pytest.raises(ValidationError):
        call.PreparedCallFanOut.model_validate_json(json.dumps(prepared_wire))


def test_original_read_only_initialization_preserves_bounded_retry_lineage() -> None:
    prepared = next(item for item in _examples() if isinstance(item, call.PreparedCallFanOut))
    restored = call.PreparedCallFanOut.model_validate_json(prepared.canonical_bytes())
    proposal, consequential, read_only = restored.initialized_records
    assert proposal.call.retry_lineage == NotApplicable()
    assert consequential.call.retry_lineage == NotApplicable()
    retry = read_only.call.retry_lineage
    assert isinstance(retry, call.InitializedReadOnlyLineage)
    assert retry.kind == "READ_ONLY_RETRY_LINEAGE"
    assert retry.lineage.original_call_id == read_only.original_call_id == "call:2"
    assert retry.lineage.max_attempts == 3
    assert (
        retry.lineage.budget_version,
        retry.lineage.reducer_id,
        retry.lineage.reducer_version,
    ) == ("budget-v1", "read-reducer", "v1")
    assert read_only.call.original.original_run_id == "original-run"
    assert read_only.call.retry_lineage == prepared.initialized_records[2].call.retry_lineage


@pytest.mark.parametrize("defect", ("missing", "unknown"))
def test_retry_lineage_is_required_and_its_wire_tag_is_closed(defect: str) -> None:
    sealed = next(item for item in _examples() if isinstance(item, call.SealedCallInput))
    raw = sealed.model_dump(mode="json")
    if defect == "missing":
        del raw["retry_lineage"]
    else:
        raw["retry_lineage"] = {"kind": "ALLOCATE_ON_RETRY"}
    with pytest.raises(ValidationError, match=r"missing|union_tag_invalid"):
        call.SealedCallInput.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("terminal_present", (False, True))
def test_predecessor_inventory_can_carry_accepted_and_terminal_observations(
    terminal_present: bool,
) -> None:
    initialized = next(
        item for item in _examples() if isinstance(item, call.CallLifecycleObservation)
    )
    assert isinstance(initialized.acceptance, InitializedCall)
    assert initialized.terminal == Absent()
    accepted = call.CallLifecycleObservation(
        original_call_id=initialized.original_call_id,
        initialized=initialized.initialized,
        initialized_record=initialized.initialized_record,
        acceptance=ConsequentialAcceptedCall(
            initialized=initialized.initialized.revision,
            accepted=_head("accepted").revision,
            execution_intent=_head("execution-intent").revision,
            external_effect_intent=_head("external-intent").revision,
            complete_acceptance_manifest=(
                _head("accepted").revision,
                _head("execution-intent").revision,
                _head("external-intent").revision,
            ),
        ),
        terminal=_head("terminal").revision if terminal_present else Absent(),
    )
    inventory = call.CallInventorySnapshot(
        tenant_id="tenant",
        tenant_commit_sequence=12,
        ordered_calls=(accepted,),
    )
    cut_wire = _cut().model_dump(mode="json")
    cut_wire["predecessor_inventory"] = inventory.model_dump(mode="json")
    restored = call.CallPreparationCut.model_validate_json(json.dumps(cut_wire))
    assert restored.predecessor_inventory == inventory
    observation = restored.predecessor_inventory.ordered_calls[0]
    assert isinstance(observation.acceptance, ConsequentialAcceptedCall)
    assert isinstance(observation.terminal, Present if terminal_present else Absent)
    assert observation.initialized_record == initialized.initialized_record
    assert call.CallPreparationCut.model_validate_json(restored.canonical_bytes()) == restored


def test_preparation_cut_requires_explicit_predecessor_inventory() -> None:
    raw = _cut().model_dump(mode="json")
    del raw["predecessor_inventory"]
    with pytest.raises(ValidationError, match="predecessor_inventory"):
        call.CallPreparationCut.model_validate_json(json.dumps(raw))
