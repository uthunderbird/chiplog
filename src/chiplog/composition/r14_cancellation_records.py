"""Closed v1 interpretation of retained cancellation; no current authority issuer.

History callers must independently authenticate selection and physical membership.
Fresh callers must additionally reproduce every source at the writer boundary.
"""

import base64
import hashlib
import json
from typing import Literal

from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    RecoveryDTO,
)
from chiplog.composition.r14_acceptance_records import _verify_inventory
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    MAX_CANCELLATION_BYTES,
    NOT_EXECUTED_SCHEMA,
    CancellationPhysicalEnvelope,
    RetainedCancellationAct,
    RetainedCancellationPreparation,
)
from chiplog.composition.r14_fanout_contracts import FanOutPhysicalMember
from chiplog.composition.r14_fanout_records import reference
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.r7_trust import TrustOwnerCall, TrustOwnerResult


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("invalid cancellation: " + reason)


def _without(value: RecoveryDTO, field: str) -> str:
    body = json.loads(value.canonical_bytes())
    del body[field]
    return hashlib.sha256(
        json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class _CancellationIdentity(RecoveryDTO):
    kind: Literal["CALL_CANCELLATION_IDENTITY_V1"] = "CALL_CANCELLATION_IDENTITY_V1"
    tenant_id: str
    act_id: str


def cancellation_identity(tenant: str, act_id: str) -> str:
    return "cancel-call:" + _CancellationIdentity(tenant_id=tenant, act_id=act_id).digest()


def act_reference(act: RetainedCancellationAct) -> call.CallSubjectHead:
    return reference("cancellation-act:" + act.digest(), act)


def _verify_trust(evidence: RetainedCancellationPreparation) -> None:
    act, trust = evidence.act, evidence.trust
    sent, received = trust.request, trust.response
    _require(
        sent.operation_id == "deployment_trust.authenticate"
        and sent.schema_id == "chiplog.deployment-trust.owner-call.v1"
        and received.schema_id == "chiplog.deployment-trust.owner-result.v1"
        and received.request_id == sent.request_id
        and received.responder == sent.callee
        and sent.caller.owner_id == "broker"
        and sent.callee.owner_id == "deployment_trust"
        and sent.caller.tenant_id == sent.callee.tenant_id == act.policy.tenant_id
        and sent.caller.broker_epoch == sent.callee.broker_epoch
        and sent.caller.generation_id == sent.callee.generation_id,
        "original trust exchange identities differ",
    )
    body = json.loads(sent.canonical_payload)
    for key in ("snapshot_bytes", "request_bytes"):
        body[key] = base64.b64decode(body[key], validate=True)
    request = TrustOwnerCall.model_validate(body)
    _require(
        request.canonical_bytes() == sent.canonical_payload
        and request.mode == "AUTHENTICATE"
        and request.snapshot_bytes == trust.snapshot_bytes,
        "original trust request differs",
    )
    body = json.loads(received.canonical_payload)
    if body.get("reference_bytes") is not None:
        body["reference_bytes"] = base64.b64decode(body["reference_bytes"], validate=True)
    result = TrustOwnerResult.model_validate(body)
    _require(
        result.canonical_bytes() == received.canonical_payload
        and result.disposition == "VALID"
        and result.reference_bytes == trust.authenticated_reference_bytes
        and act.authenticated_reference_bytes == trust.authenticated_reference_bytes
        and act.trust_evidence_fingerprint == trust.digest(),
        "original trust result or act binding differs",
    )
    source = json.loads(request.request_bytes)
    authenticated = json.loads(trust.authenticated_reference_bytes)
    _require(
        isinstance(source, dict)
        and set(source) == {"contour", "credential_id", "peer_credential", "session_id"}
        and source["contour"] == "CLI"
        and source["credential_id"] == "hermetic-credential"
        and source["session_id"] == "hermetic-session"
        and isinstance(authenticated, dict)
        and set(authenticated)
        == {
            "contour",
            "credential_head",
            "freshness_sequence",
            "materialization_head",
            "peer_credential",
            "principal_id",
            "session_head",
            "source_head",
            "tenant_id",
            "trust_head",
        }
        and authenticated["tenant_id"] == act.policy.tenant_id
        and authenticated["principal_id"] == act.policy.principal_id
        and authenticated["contour"] == "CLI"
        and authenticated["source_head"] == "local"
        and authenticated["peer_credential"] == source["peer_credential"],
        "original authenticated subject is outside cancellation policy",
    )


def _verify(evidence: RetainedCancellationPreparation) -> None:
    act, request, proposal = evidence.act, evidence.request, evidence.proposal
    submitted, cut = act.submission, request.cut
    previous, result = evidence.run_predecessor, evidence.run_companion
    initialized = request.initialized_record
    _verify_trust(evidence)
    _verify_inventory(cut)
    _require(
        previous.tenant == act.policy.tenant_id == cut.tenant_id
        and previous.principal == act.policy.principal_id
        and previous.state == "ACTIVE"
        and previous.root_binding == "NOT_APPLICABLE"
        and submitted.current_run.subject_id == previous.run_id
        and submitted.current_run.revision.head == previous.head
        and submitted.current_run.revision.fingerprint == previous.digest()
        and cut.current_run == submitted.current_run
        and cut.run_state == "ACTIVE"
        and isinstance(cut.fence, NonSchedulerFence),
        "original Run or applicability differs",
    )
    assert isinstance(cut.fence, NonSchedulerFence)
    _require(
        cut.fence.run_id == previous.run_id
        and cut.fence.run_head == previous.head
        and cut.fence.worker_session_id == previous.worker_session
        and request.command_id == cancellation_identity(cut.tenant_id, submitted.act_id)
        and request.original_call_id == submitted.original_call_id == initialized.original_call_id
        and request.original == initialized.call.original
        and request.original.original_run_id == previous.run_id
        and request.original.original_turn_id == domain.current_turn(previous).turn_id
        and request.initialized == submitted.initialized
        and request.initialized == reference(initialized.original_call_id, initialized)
        and request.cancellation_act == act_reference(act),
        "request differs from original submitted act or initialized subject",
    )
    inventory = cut.predecessor_inventory
    _require(
        inventory.tenant_id == cut.tenant_id
        and inventory.tenant_commit_sequence == cut.tenant_commit_sequence
        and cut.complete_call_inventory == reference("call-inventory:" + cut.tenant_id, inventory)
        and tuple(row.original_call_id for row in inventory.ordered_calls)
        == tuple(sorted({row.original_call_id for row in inventory.ordered_calls})),
        "original inventory reference or order differs",
    )
    matches = [
        row for row in inventory.ordered_calls if row.original_call_id == request.original_call_id
    ]
    _require(len(matches) == 1, "original call absent from inventory")
    row = matches[0]
    _require(
        row.initialized_record == initialized
        and row.initialized == request.initialized
        and row.acceptance.kind == "INITIALIZED"
        and row.terminal == Absent(),
        "call already accepted or terminal",
    )
    policy_ref = reference("cancellation-policy:v1", act.policy)
    _require(cut.authority_registry == policy_ref, "unregistered cancellation policy")
    sources = (
        (policy_ref, "POLICY", act.policy),
        (
            reference("cancellation-trust:" + submitted.act_id, evidence.trust),
            "ACTOR",
            evidence.trust,
        ),
        (act_reference(act), "MANDATE", act),
    )
    _require(len(cut.sources) == len(sources), "incomplete cancellation source set")
    for observed, (ref, family, value) in zip(cut.sources, sources, strict=True):
        _require(
            observed.source_id == ref.subject_id
            and observed.source == ref
            and observed.family == family
            and observed.canonical_value_base64
            == base64.b64encode(value.canonical_bytes()).decode()
            and observed.generation == cut.fence.runtime_generation
            and observed.frontier == str(cut.tenant_commit_sequence),
            "retained source bytes or cut differ",
        )
    digest = request.digest()
    terminal = call.CancelledBeforeAcceptRecord(
        terminal_id="cancelled:" + digest,
        source_command_id=request.command_id,
        original_call_id=request.original_call_id,
        original=request.original,
        initialized=request.initialized,
        cancellation_act=request.cancellation_act,
        cut=cut,
        not_executed_result_id="not-executed:" + digest,
    )
    terminal_ref = reference(terminal.terminal_id, terminal)
    not_executed = call.NotExecutedCallResultRecord(
        result_id=terminal.not_executed_result_id,
        original_call_id=request.original_call_id,
        initialized=request.initialized,
        terminal=terminal_ref,
        outcome="NOT_EXECUTED",
    )
    _require(
        proposal.source_request_fingerprint == digest
        and proposal.terminal == terminal
        and proposal.result == not_executed
        and proposal.complete_ordered_record_manifest
        == (terminal_ref, reference(not_executed.result_id, not_executed))
        and proposal.proposal_fingerprint == _without(proposal, "proposal_fingerprint"),
        "owner proposal or result manifest differs",
    )
    sent, returned = evidence.owner_request, evidence.owner_response
    _require(
        sent.operation_id == "agent_loop.prepare_pre_accept_cancellation"
        and sent.schema_id == "chiplog.call.cancellation-preparation.v1"
        and sent.canonical_payload == request.canonical_bytes()
        and sent.caller == evidence.trust.request.caller
        and sent.callee.owner_id == OWNER
        and sent.callee.tenant_id == cut.tenant_id
        and sent.callee.generation_id == cut.fence.runtime_generation
        and sent.callee.broker_epoch == sent.caller.broker_epoch
        and previous.worker_session
        == f"{sent.callee.broker_epoch}:{sent.callee.generation_id}:{sent.callee.session_id}"
        and returned.request_id == sent.request_id
        and returned.responder == sent.callee
        and returned.schema_id == "chiplog.call.preparation-result.v1"
        and returned.canonical_payload == proposal.canonical_bytes(),
        "owner exchange differs from original preparation",
    )
    for source in cut.sources:
        _require(
            source.observed_at_ns < sent.budget.absolute_deadline_ns <= source.valid_until_ns
            and source.observed_at_ns < evidence.trust.request.budget.absolute_deadline_ns,
            "retained source interval differs from issued owner budget",
        )
    _require(
        result
        == domain.terminal_tool(
            previous, request.original.model_call_label, not_executed.canonical_bytes().decode()
        ),
        "Run companion does not carry exact NOT_EXECUTED result",
    )
    domain.validate_record(previous, result)


def build_cancellation_envelope(
    evidence: RetainedCancellationPreparation,
) -> CancellationPhysicalEnvelope:
    evidence = RetainedCancellationPreparation.model_validate_json(evidence.canonical_bytes())
    _verify(evidence)
    proposal = evidence.proposal
    values = (
        (evidence.run_companion.head, SCHEMA, evidence.run_companion.canonical_bytes()),
        (proposal.terminal.terminal_id, CANCELLATION_SCHEMA, proposal.terminal.canonical_bytes()),
        (proposal.result.result_id, NOT_EXECUTED_SCHEMA, proposal.result.canonical_bytes()),
    )
    _require(
        len({identity for identity, _, _ in values}) == 3
        and all("\n" not in identity and "\r" not in identity for identity, _, _ in values),
        "invalid physical identities",
    )
    envelope = CancellationPhysicalEnvelope(
        tenant_id=evidence.request.cut.tenant_id,
        idempotency_key=evidence.request.command_id,
        expected_head=evidence.request.cut.tenant_commit_sequence,
        retained_preparation_fingerprint=evidence.digest(),
        records=tuple(
            FanOutPhysicalMember(
                record_id=identity,
                owner=OWNER,
                schema_id=schema,
                canonical_payload_base64=base64.b64encode(raw).decode(),
                fingerprint=hashlib.sha256(raw).hexdigest(),
            )
            for identity, schema, raw in values
        ),
        request_fingerprint="0" * 64,
    )
    envelope = envelope.model_copy(
        update={"request_fingerprint": _without(envelope, "request_fingerprint")}
    )
    _require(
        len(envelope.canonical_bytes()) <= MAX_CANCELLATION_BYTES, "physical byte bound exceeded"
    )
    return envelope


def cancellation_command(
    evidence: RetainedCancellationPreparation,
) -> PhysicalPublicationCommand:
    envelope = build_cancellation_envelope(evidence)
    return PhysicalPublicationCommand(
        tenant_id=envelope.tenant_id,
        operation_kind=envelope.operation_kind,
        idempotency_key=envelope.idempotency_key,
        request_fingerprint=envelope.request_fingerprint,
        expected_head=envelope.expected_head,
        fence_generation=envelope.fence_generation,
        expected_fence_frontier=envelope.expected_fence_frontier,
        minimum_fence_frontier=envelope.minimum_fence_frontier,
        records=tuple(
            PhysicalRecord(
                member.record_id,
                member.owner,
                member.schema_id,
                base64.b64decode(member.canonical_payload_base64, validate=True),
                member.fingerprint,
            )
            for member in envelope.records
        ),
    )
