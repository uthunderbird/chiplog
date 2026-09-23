"""Public captured fan-out wire shapes, not producer or admission evidence."""

import base64
import json
from typing import Literal, get_type_hints

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import contracts as loop
from chiplog.capabilities.agent_loop import fan_out_contracts as fan
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound


def _head(subject: str, digest: str = "a" * 64) -> call.CallSubjectHead:
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def _request() -> fan.CapturedFanOutRequest:
    tool = loop.ToolSpec(name="propose_planning", schema_id="planning-schema")
    parsed = loop.Continue(
        kind="Continue",
        tool_calls=(loop.ToolCall(call_id="model-call", tool=tool.name, text="Plan café"),),
    )
    # The durable capture predates canonical serialization: retain whitespace and escapes.
    raw = json.dumps(parsed.model_dump(mode="json"), indent=2).encode() + b"\n"
    encoded = base64.b64encode(raw).decode()
    artifact = loop.PromptArtifact(
        content_hash="b" * 64,
        library_version="1",
        tools=(tool,),
        response_schema_json=json.dumps(loop.Continue.model_json_schema()),
        rendered="Plan",
    )
    manifest = loop.VisibilityManifest(
        tenant="tenant",
        principal="actor",
        contour_head="contour",
        run_id="run",
        turn_id="turn",
        generation=0,
        worker_session="worker",
        members=(),
        joined_label=loop.DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
        artifact=artifact,
    )
    attempt = loop.ModelAttempt(
        attempt_id="attempt",
        lineage_id="lineage",
        generation=0,
        state="RESPONSE_CAPTURED",
        head="attempt-head",
        manifest=manifest,
        request="Plan",
        worker_session="worker",
        response_base64=encoded,
        receipt="receipt",
    )
    run = loop.RunRecord(
        tenant="tenant",
        principal="actor",
        run_id="run",
        state="ACTIVE",
        head="run-head",
        predecessor="previous-run-head",
        prompt="Plan",
        policy=loop.BudgetPolicy(),
        origin=loop.EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="ingress",
            endpoint_head="endpoint-head",
            endpoint_id="endpoint",
            provider="hermetic-local",
            recipient="actor",
            canonical_address="local:actor",
            credential_binding_head="credential",
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        turns=(
            loop.Turn(
                turn_id="turn",
                ordinal=1,
                head="turn-head",
                state="RESPONSE_AVAILABLE",
                attempts=(attempt,),
                selector=0,
            ),
        ),
        event="ModelResponseCaptured",
    )
    captured = call.CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    entry = fan.FanOutToolPolicy(
        tool_name=tool.name,
        tool_version=tool.version,
        schema_id=tool.schema_id,
        tool_schema=_head(tool.schema_id, tool.digest()),
        tool_policy=_head("policy"),
        classification="PROPOSAL_ONLY",
        retry_policy=NotApplicable(),
    )
    registry = fan.FanOutToolRegistry(registry_id="registry", version="1", entries=(entry,))
    cut = call.CallPreparationCut(
        tenant_id="tenant",
        current_run=captured,
        run_state="ACTIVE",
        tenant_commit_sequence=1,
        materialization_commitment="b" * 64,
        complete_call_inventory=_head("inventory"),
        predecessor_inventory=call.CallInventorySnapshot(
            tenant_id="tenant",
            tenant_commit_sequence=1,
            ordered_calls=(),
        ),
        authority_registry=_head("authority"),
        sources=(
            call.CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=_head("actor"),
                generation="0",
                frontier="frontier",
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
    request = call.FanOutPreparationRequest(
        command_id="prepare",
        original_run_id=run.run_id,
        original_turn_id="turn",
        captured_response=captured,
        canonical_response_base64=encoded,
        ordered_calls=(
            call.SealedCallInput(
                original=call.OriginalCallKey(
                    tenant_id=run.tenant,
                    original_run_id=run.run_id,
                    original_turn_id="turn",
                    captured_response=captured,
                    ordinal=0,
                    model_call_label="model-call",
                ),
                classification=entry.classification,
                tool_schema=entry.tool_schema,
                tool_policy=entry.tool_policy,
                retry_lineage=NotApplicable(),
                canonical_call_base64=base64.b64encode(
                    parsed.tool_calls[0].canonical_bytes()
                ).decode(),
            ),
        ),
        bound=FanOutBound(
            max_call_count=8,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=131072,
            canonicalization_version="chiplog.recovery.frontier.v1",
        ),
        cut=cut,
    )
    return fan.CapturedFanOutRequest(
        request=request,
        captured_run=run,
        tool_registry=registry,
        tool_registry_head=_head(registry.registry_id, registry.digest()),
    )


def _proposal(request: fan.CapturedFanOutRequest) -> fan.CapturedFanOutProposal:
    inner = request.request
    initialized = call.InitializedCallRecord(
        original_call_id="call:" + inner.ordered_calls[0].original.digest(),
        call=inner.ordered_calls[0],
        predecessor=Absent(),
    )
    initialized_head = _head(initialized.original_call_id, initialized.digest())
    seal = call.SealedResponseRecord(
        response_seal_id="response-seal:" + inner.digest(),
        tenant_id="tenant",
        original_run_id="run",
        original_turn_id="turn",
        captured_response=inner.captured_response,
        complete_ordered_initialized=(initialized_head,),
        bound=inner.bound,
    )
    return fan.CapturedFanOutProposal(
        source_request_fingerprint=request.digest(),
        proposal_fingerprint="c" * 64,
        fan_out=call.PreparedCallFanOut(
            source_request_fingerprint=inner.digest(),
            response_seal=seal,
            initialized_records=(initialized,),
            complete_ordered_record_manifest=(
                _head(seal.response_seal_id, seal.digest()),
                initialized_head,
            ),
            proposal_fingerprint="d" * 64,
        ),
    )


def test_roundtrip_retains_capture_and_each_canonical_domain() -> None:
    value = _request()
    restored = fan.CapturedFanOutRequest.model_validate_json(value.canonical_bytes())
    assert restored == value
    assert restored.canonical_bytes() == value.canonical_bytes()
    run = restored.captured_run
    assert run.canonical_bytes() == value.captured_run.canonical_bytes()
    assert run.digest() == restored.request.captured_response.revision.fingerprint
    # The enclosing RecoveryDTO moves nested kind fields ahead of lexical keys.
    embedded = json.dumps(
        json.loads(value.canonical_bytes())["captured_run"],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    assert embedded != run.canonical_bytes()
    turn = run.turns[-1]
    attempt = turn.attempts[turn.selector]
    assert attempt.state == "RESPONSE_CAPTURED"
    assert attempt.response_base64 == restored.request.canonical_response_base64
    raw = base64.b64decode(restored.request.canonical_response_base64, validate=True)
    parsed = loop.Continue.model_validate_json(raw)
    assert raw != parsed.canonical_bytes()
    assert raw.endswith(b"\n") and b"\\u00e9" in raw
    assert base64.b64decode(restored.request.ordered_calls[0].canonical_call_base64) == (
        parsed.tool_calls[0].canonical_bytes()
    )
    entries = restored.tool_registry.entries
    assert tuple((e.tool_name, e.tool_version, e.schema_id) for e in entries) == tuple(
        (t.name, t.version, t.schema_id) for t in attempt.manifest.artifact.tools
    )
    assert (
        entries[0].tool_schema.revision.fingerprint == attempt.manifest.artifact.tools[0].digest()
    )
    assert restored.tool_registry_head.revision.fingerprint == restored.tool_registry.digest()


@pytest.mark.parametrize(
    "field", ("request", "captured_run", "tool_registry", "tool_registry_head")
)
def test_required_evidence_cannot_be_omitted(field: str) -> None:
    wire = _request().model_dump(mode="json")
    del wire[field]
    with pytest.raises(ValidationError, match="missing"):
        fan.CapturedFanOutRequest.model_validate_json(json.dumps(wire))


@pytest.mark.parametrize("field", ("artifact", "tools", "response_schema_json"))
def test_nested_prompt_evidence_cannot_be_omitted(field: str) -> None:
    wire = _request().model_dump(mode="json")
    manifest = wire["captured_run"]["turns"][0]["attempts"][0]["manifest"]
    del (manifest if field == "artifact" else manifest["artifact"])[field]
    with pytest.raises(ValidationError, match="missing"):
        fan.CapturedFanOutRequest.model_validate_json(json.dumps(wire))


@pytest.mark.parametrize("classification", ("PROPOSAL_ONLY", "CONSEQUENTIAL", "READ_ONLY"))
@pytest.mark.parametrize("readonly", (False, True))
def test_classification_and_retry_variants_are_transport_only(
    classification: Literal["PROPOSAL_ONLY", "CONSEQUENTIAL", "READ_ONLY"],
    readonly: bool,
) -> None:
    wire = _request().tool_registry.entries[0].model_dump(mode="json")
    wire["classification"] = classification
    policy = (
        fan.ReadOnlyFanOutPolicy(
            max_attempts=2**64 - 1,
            budget_version="budget",
            reducer_id="reducer",
            reducer_version="1",
        )
        if readonly
        else NotApplicable()
    )
    wire["retry_policy"] = policy.model_dump(mode="json")
    restored = fan.FanOutToolPolicy.model_validate_json(json.dumps(wire))
    assert restored.classification == classification
    assert restored.retry_policy == policy
    assert fan.FanOutToolPolicy.model_validate_json(restored.canonical_bytes()) == restored


@pytest.mark.parametrize("attempts", (0, -1, 2**64, True, "3"))
def test_retry_budget_rejects_non_uint64_or_zero(attempts: object) -> None:
    with pytest.raises(ValidationError):
        fan.ReadOnlyFanOutPolicy.model_validate_json(
            json.dumps(
                {
                    "max_attempts": attempts,
                    "budget_version": "budget",
                    "reducer_id": "reducer",
                    "reducer_version": "1",
                }
            )
        )


@pytest.mark.parametrize(
    "field,value",
    (("classification", "UNKNOWN"), ("retry_policy", {"kind": "UNKNOWN"}), ("retry_policy", {})),
)
def test_policy_discriminants_are_closed(field: str, value: object) -> None:
    wire = _request().tool_registry.entries[0].model_dump(mode="json")
    wire[field] = value
    with pytest.raises(
        ValidationError, match=r"literal_error|union_tag_invalid|union_tag_not_found"
    ):
        fan.FanOutToolPolicy.model_validate_json(json.dumps(wire))


def test_new_public_shapes_are_closed_and_frozen() -> None:
    request = _request()
    values: tuple[RecoveryDTO, ...] = (
        request,
        request.tool_registry,
        request.tool_registry.entries[0],
        _proposal(request),
        fan.ReadOnlyFanOutPolicy(
            max_attempts=1, budget_version="b", reducer_id="r", reducer_version="1"
        ),
    )
    for value in values:
        assert type(value).model_validate_json(value.canonical_bytes()) == value
        wire = value.model_dump(mode="json")
        wire["unexpected"] = True
        with pytest.raises(ValidationError, match="extra_forbidden"):
            type(value).model_validate_json(json.dumps(wire))
        field = next(iter(type(value).model_fields))
        with pytest.raises(ValidationError, match="frozen_instance"):
            setattr(value, field, getattr(value, field))
        if "kind" in wire:
            del wire["unexpected"]
            wire["kind"] = "UNKNOWN"
            with pytest.raises(ValidationError, match="literal_error"):
                type(value).model_validate_json(json.dumps(wire))


def test_result_union_and_port_remain_preparation_only() -> None:
    adapter: TypeAdapter[fan.CapturedFanOutResult] = TypeAdapter(fan.CapturedFanOutResult)
    request = _request()
    proposal = _proposal(request)
    rejected = call.CallPreparationRejected(command_id="prepare", code="HOLD", reason="unresolved")
    for value in (proposal, rejected):
        assert adapter.validate_json(value.canonical_bytes()) == value
    for kind in ("COMMITTED", "EXACT_REPLAY", "UNKNOWN"):
        with pytest.raises(ValidationError, match="union_tag_invalid"):
            adapter.validate_json(json.dumps({"kind": kind}))
    hints = get_type_hints(
        fan.CapturedFanOutPreparationPort.prepare_captured_fan_out, include_extras=True
    )
    assert hints["request"] == fan.CapturedFanOutRequest
    assert hints["return"] == fan.CapturedFanOutResult
    assert proposal.source_request_fingerprint == request.digest()
    assert proposal.fan_out.source_request_fingerprint == request.request.digest()
    assert proposal.source_request_fingerprint != proposal.fan_out.source_request_fingerprint
    assert "commit_sequence" not in fan.CapturedFanOutProposal.model_fields


@pytest.mark.parametrize("field", ("tool_schema", "tool_policy", "classification", "retry_policy"))
def test_registry_entry_requires_each_policy_observation(field: str) -> None:
    wire = _request().model_dump(mode="json")
    del wire["tool_registry"]["entries"][0][field]
    with pytest.raises(ValidationError, match="missing"):
        fan.CapturedFanOutRequest.model_validate_json(json.dumps(wire))


def test_nested_capture_is_closed_and_registry_cannot_be_empty() -> None:
    wire = _request().model_dump(mode="json")
    wire["captured_run"]["turns"][0]["attempts"][0]["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        fan.CapturedFanOutRequest.model_validate_json(json.dumps(wire))
    wire = _request().model_dump(mode="json")
    wire["tool_registry"]["entries"] = []
    with pytest.raises(ValidationError, match="too_short"):
        fan.CapturedFanOutRequest.model_validate_json(json.dumps(wire))
