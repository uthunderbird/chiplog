"""Consumer wire shapes only; supplied retained values grant no publication authority."""

import base64
import hashlib
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import contracts as loop
from chiplog.capabilities.agent_loop import fan_out_contracts as fan
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import FanOutBound
from chiplog.composition.r14_fanout_contracts import (
    FANOUT_OPERATION,
    INITIALIZED_SCHEMA,
    SEAL_SCHEMA,
    FanOutPhysicalEnvelope,
    FanOutPhysicalMember,
    RetainedFanOutPreparation,
)
from chiplog.platform.broker import BrokerSession


def _head(identity: str) -> call.CallSubjectHead:
    return call.CallSubjectHead(
        subject_id=identity, revision=Present(head=identity, fingerprint="a" * 64)
    )


def _retained() -> RetainedFanOutPreparation:
    run = loop.RunRecord(
        tenant="tenant",
        principal="principal",
        run_id="run",
        state="ACTIVE",
        head="capture",
        predecessor="prior",
        prompt="Help",
        policy=loop.BudgetPolicy(),
        origin=loop.EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="ingress",
            endpoint_head="endpoint",
            endpoint_id="endpoint",
            provider="hermetic-local",
            recipient="principal",
            canonical_address="local:principal",
            credential_binding_head="credential",
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        event="ModelResponseCaptured",
    )
    cut = call.CallPreparationCut(
        tenant_id="tenant",
        current_run=_head("run"),
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
                generation="1",
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
            run_id="run",
            run_head="capture",
            worker_session_id="worker",
            runtime_generation="generation",
        ),
    )
    bound = FanOutBound(
        max_call_count=8,
        max_manifest_bytes=4096,
        max_serialized_batch_bytes=65536,
        canonicalization_version="chiplog.recovery.frontier.v1",
    )
    inner = call.FanOutPreparationRequest(
        command_id="prepare",
        original_run_id="run",
        original_turn_id="turn",
        captured_response=_head("capture"),
        canonical_response_base64="e30=",
        ordered_calls=(),
        bound=bound,
        cut=cut,
    )
    registry = fan.FanOutToolRegistry(
        registry_id="registry",
        version="1",
        entries=(
            fan.FanOutToolPolicy(
                tool_name="propose_planning",
                tool_version="1",
                schema_id="schema",
                tool_schema=_head("schema"),
                tool_policy=_head("policy"),
                classification="PROPOSAL_ONLY",
                retry_policy=NotApplicable(),
            ),
        ),
    )
    request = fan.CapturedFanOutRequest(
        request=inner,
        captured_run=run,
        tool_registry=registry,
        tool_registry_head=_head("registry"),
    )
    seal = call.SealedResponseRecord(
        response_seal_id="seal",
        tenant_id="tenant",
        original_run_id="run",
        original_turn_id="turn",
        captured_response=inner.captured_response,
        complete_ordered_initialized=(),
        bound=bound,
    )
    proposal = fan.CapturedFanOutProposal(
        source_request_fingerprint=request.digest(),
        proposal_fingerprint="c" * 64,
        fan_out=call.PreparedCallFanOut(
            source_request_fingerprint=inner.digest(),
            response_seal=seal,
            initialized_records=(),
            complete_ordered_record_manifest=(_head("seal"),),
            proposal_fingerprint="d" * 64,
        ),
    )
    caller = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker",
        session_id="broker-session",
    )
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="owner-session",
    )
    return RetainedFanOutPreparation(
        request=request,
        proposal=proposal,
        accepted_run=run,
        companions=(
            loop.DurableCompanion(
                record_id="run/accepted",
                owner="conversation",
                schema_id="conversation.v1",
                payload_base64="e30=",
            ),
        ),
        expected_snapshot_fingerprint="e" * 64,
        caller=caller,
        callee=callee,
        request_id="request",
        deadline_ns=2**64 - 1,
    )


def _envelope() -> FanOutPhysicalEnvelope:
    descriptions = (
        ("run-head", "agent_loop", "chiplog.agent-loop.record.v1", b'{"run":"caf\xc3\xa9"}'),
        ("seal-head", "agent_loop", SEAL_SCHEMA, b'{"seal":1}'),
        ("call-head", "agent_loop", INITIALIZED_SCHEMA, b'{"ordinal":0}'),
        ("run/accepted", "conversation", "conversation.v1", b'{"accepted":"Ready"}'),
    )
    return FanOutPhysicalEnvelope(
        tenant_id="tenant",
        idempotency_key="run-head",
        expected_head=2**64 - 1,
        records=tuple(
            FanOutPhysicalMember(
                record_id=identity,
                owner=owner,
                schema_id=schema,
                canonical_payload_base64=base64.b64encode(payload).decode(),
                fingerprint=hashlib.sha256(payload).hexdigest(),
            )
            for identity, owner, schema, payload in descriptions
        ),
        request_fingerprint="f" * 64,
    )


def test_physical_envelope_preserves_complete_wire_and_member_order() -> None:
    envelope = _envelope()
    wire = envelope.canonical_bytes()
    restored = FanOutPhysicalEnvelope.model_validate_json(wire)
    assert restored == envelope
    assert restored.canonical_bytes() == wire
    assert restored.operation_kind == FANOUT_OPERATION == "agent_loop.fanout.v1"
    assert tuple(member.record_id for member in restored.records) == (
        "run-head",
        "seal-head",
        "call-head",
        "run/accepted",
    )
    assert set(json.loads(wire)) == {
        "kind",
        "tenant_id",
        "operation_kind",
        "idempotency_key",
        "expected_head",
        "fence_generation",
        "expected_fence_frontier",
        "minimum_fence_frontier",
        "records",
        "request_fingerprint",
    }
    assert restored.fence_generation == "r6"
    assert restored.expected_fence_frontier == restored.minimum_fence_frontier == 0
    assert len(wire) > sum(
        len(base64.b64decode(member.canonical_payload_base64)) for member in restored.records
    )
    for member in restored.records:
        assert (
            member.fingerprint
            == hashlib.sha256(base64.b64decode(member.canonical_payload_base64)).hexdigest()
        )
    raw = envelope.model_dump(mode="json")
    raw["records"] = list(reversed(raw["records"]))
    reordered = FanOutPhysicalEnvelope.model_validate_json(json.dumps(raw))
    assert tuple(member.record_id for member in reordered.records) == tuple(
        reversed(tuple(member.record_id for member in envelope.records))
    )
    assert reordered.canonical_bytes() != wire


def test_retained_exchange_roundtrips_nested_bytes_sessions_and_companions() -> None:
    value = _retained()
    restored = RetainedFanOutPreparation.model_validate_json(value.canonical_bytes())
    assert restored == value
    assert restored.canonical_bytes() == value.canonical_bytes()
    assert restored.request.canonical_bytes() == value.request.canonical_bytes()
    assert restored.proposal.canonical_bytes() == value.proposal.canonical_bytes()
    assert restored.accepted_run.canonical_bytes() == value.accepted_run.canonical_bytes()
    assert restored.companions == value.companions
    assert restored.caller.owner_id == "broker"
    assert restored.callee.owner_id == "agent_loop"
    assert restored.caller.session_id != restored.callee.session_id
    assert restored.response_schema == "chiplog.call.captured-fanout-result.v1"
    assert restored.expected_snapshot_fingerprint == "e" * 64
    assert restored.request_id == "request"
    assert restored.deadline_ns == 2**64 - 1


@pytest.mark.parametrize(
    "value",
    (_envelope(), _envelope().records[0], _retained()),
    ids=lambda value: type(value).__name__,
)
def test_publication_shapes_are_closed_and_frozen(value: RecoveryDTO) -> None:
    wire = value.model_dump(mode="json")
    wire["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        type(value).model_validate_json(json.dumps(wire))
    field = next(iter(type(value).model_fields))
    with pytest.raises(ValidationError, match="frozen_instance"):
        setattr(value, field, getattr(value, field))


@pytest.mark.parametrize(
    "field,value",
    (
        ("kind", "OTHER"),
        ("operation_kind", "agent_loop"),
        ("fence_generation", "r14"),
        ("expected_fence_frontier", 1),
        ("minimum_fence_frontier", 1),
        ("expected_head", -1),
        ("expected_head", 2**64),
        ("expected_head", True),
        ("request_fingerprint", "not-a-digest"),
        ("records", []),
        ("records", {}),
    ),
)
def test_physical_envelope_rejects_wrong_operation_fence_and_malformed_fields(
    field: str,
    value: object,
) -> None:
    wire = _envelope().model_dump(mode="json")
    wire[field] = value
    with pytest.raises(ValidationError):
        FanOutPhysicalEnvelope.model_validate_json(json.dumps(wire))


def test_envelope_requires_two_members_and_complete_member_fields() -> None:
    wire = _envelope().model_dump(mode="json")
    wire["records"] = wire["records"][:1]
    with pytest.raises(ValidationError, match="too_short"):
        FanOutPhysicalEnvelope.model_validate_json(json.dumps(wire))
    for field in FanOutPhysicalMember.model_fields:
        wire = _envelope().model_dump(mode="json")
        del wire["records"][0][field]
        with pytest.raises(ValidationError, match="missing"):
            FanOutPhysicalEnvelope.model_validate_json(json.dumps(wire))


@pytest.mark.parametrize(
    "field",
    (
        "request",
        "proposal",
        "accepted_run",
        "companions",
        "expected_snapshot_fingerprint",
        "caller",
        "callee",
        "request_id",
        "deadline_ns",
    ),
)
def test_retained_original_evidence_fields_are_required(field: str) -> None:
    wire = _retained().model_dump(mode="json")
    del wire[field]
    with pytest.raises(ValidationError, match="missing"):
        RetainedFanOutPreparation.model_validate_json(json.dumps(wire))


@pytest.mark.parametrize(
    "field,value",
    (
        ("kind", "OTHER"),
        ("response_schema", "chiplog.call.preparation-result.v1"),
        ("deadline_ns", -1),
        ("deadline_ns", 2**64),
        ("deadline_ns", True),
        ("expected_snapshot_fingerprint", "not-a-digest"),
    ),
)
def test_retained_tags_schema_and_numeric_fields_are_closed(field: str, value: object) -> None:
    wire = _retained().model_dump(mode="json")
    wire[field] = value
    with pytest.raises(ValidationError):
        RetainedFanOutPreparation.model_validate_json(json.dumps(wire))
