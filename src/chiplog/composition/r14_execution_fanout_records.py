"""Independent interpretation of retained executable output, without owner regeneration.

This verifier checks source/output consistency, not authenticity. Journal selection
and current writer checks must authenticate the retained original capture and cut.
"""

import base64
import hashlib
import json

from chiplog.adapters.driven.loop_sqlite import OWNER
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.contracts import Frozen
from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryCompletion
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionContinue
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
)
from chiplog.capabilities.agent_loop.execution_parsing import parse_execution_response
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord

from ._pure_bytes import reuse_exact_bytes
from .r14_execution_fanout_contracts import (
    EXECUTION_RUN_SCHEMA,
    ExecutionFanOutPhysicalEnvelope,
    RetainedExecutionFanOutPreparation,
)
from .r14_fanout_contracts import INITIALIZED_SCHEMA, SEAL_SCHEMA, FanOutPhysicalMember


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def reference(subject: str, value: Frozen) -> call.CallSubjectHead:
    digest = value.digest()
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def _without_fingerprint(value: RecoveryDTO, field: str) -> str:
    wire = json.loads(value.canonical_bytes())
    del wire[field]
    return hashlib.sha256(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _decode(encoded: str) -> bytes:
    raw = base64.b64decode(encoded, validate=True)
    _require(base64.b64encode(raw).decode() == encoded, "noncanonical payload base64")
    return raw


def _verify(evidence: RetainedExecutionFanOutPreparation) -> None:
    request, proposed = evidence.request, evidence.proposal
    inner, run = request.request, request.captured_run
    cut, fence = inner.cut, inner.cut.fence
    _require(isinstance(fence, NonSchedulerFence), "scheduler fanout is unsupported")
    assert isinstance(fence, NonSchedulerFence)
    _require(run.root_binding == "NOT_APPLICABLE", "scheduler-rooted fanout is unsupported")
    _require(
        run.state == "ACTIVE" and run.event == "ModelResponseCaptured", "not an active capture"
    )
    _require(
        run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "capture self head differs",
    )
    _require(bool(run.turns), "capture has no Turn")
    turn = run.turns[-1]
    _require(
        bool(turn.attempts)
        and turn.selector == len(turn.attempts) - 1
        and turn.response_seal is None
        and turn.initialized_calls is None,
        "capture has no selected unsealed attempt",
    )
    attempt = turn.attempts[turn.selector]
    _require(
        attempt.state == "RESPONSE_CAPTURED" and attempt.generation == turn.selector,
        "selected attempt is not captured",
    )
    capture = call.CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    _require(
        turn.state == "RESPONSE_AVAILABLE"
        and cut.run_state == "ACTIVE"
        and cut.tenant_id == run.tenant
        and inner.original_run_id == run.run_id
        and inner.original_turn_id == turn.turn_id
        and inner.captured_response == capture == cut.current_run
        and fence.run_id == run.run_id
        and fence.run_head == run.head,
        "capture, original identity or cut differs",
    )
    manifest = attempt.manifest
    _require(
        manifest.tenant == run.tenant
        and manifest.principal == run.principal
        and manifest.run_id == run.run_id
        and manifest.turn_id == turn.turn_id
        and manifest.generation == attempt.generation
        and manifest.contour_head == run.contour_head
        and run.worker_session
        == attempt.worker_session
        == manifest.worker_session
        == fence.worker_session_id
        and bool(attempt.receipt)
        and attempt.response_base64 == inner.canonical_response_base64,
        "captured manifest, worker or response differs",
    )
    caller, callee = evidence.caller, evidence.callee
    _require(
        caller.owner_id == "broker"
        and callee.owner_id == OWNER
        and caller.tenant_id == callee.tenant_id == run.tenant
        and caller.broker_epoch == callee.broker_epoch
        and caller.generation_id == callee.generation_id == fence.runtime_generation
        and bool(caller.session_id)
        and bool(callee.session_id)
        and bool(callee.generation_id)
        and run.worker_session
        == f"{callee.broker_epoch}:{callee.generation_id}:{callee.session_id}"
        and evidence.deadline_ns > 0,
        "retained owner exchange session or deadline differs",
    )
    inventory = cut.predecessor_inventory
    _require(
        inventory.tenant_id == run.tenant
        and inventory.tenant_commit_sequence == cut.tenant_commit_sequence
        and cut.complete_call_inventory == reference("call-inventory:" + run.tenant, inventory),
        "predecessor inventory cut or reference differs",
    )
    occupied = tuple(row.original_call_id for row in inventory.ordered_calls)
    _require(
        occupied == tuple(sorted(set(occupied))), "inventory identity order or uniqueness differs"
    )
    for row in inventory.ordered_calls:
        record = row.initialized_record
        _require(
            record.original_call_id
            == row.original_call_id
            == "call:" + record.call.original.digest()
            and record.call.original.tenant_id == run.tenant
            and row.initialized == reference(record.original_call_id, record)
            and row.acceptance.initialized == row.initialized.revision,
            "inventory initialization differs",
        )
    _require(
        len({source.source_id for source in cut.sources}) == len(cut.sources),
        "duplicate cut source",
    )
    _require(cut.authority_registry == request.tool_registry_head, "cut authority registry differs")
    for source in cut.sources:
        raw_source = _decode(source.canonical_value_base64)
        digest = hashlib.sha256(raw_source).hexdigest()
        _require(
            source.source.subject_id == source.source_id
            and source.source.revision == Present(head="record:" + digest, fingerprint=digest)
            and source.generation == callee.generation_id
            and source.frontier == str(cut.tenant_commit_sequence)
            and source.observed_at_ns < evidence.deadline_ns <= source.valid_until_ns,
            "source reference, generation, frontier or retained deadline differs",
        )
    raw = _decode(inner.canonical_response_base64)
    _require(len(raw) <= run.policy.max_response_bytes, "capture exceeds response byte bound")
    response = parse_execution_response(raw, manifest.artifact)
    if isinstance(response, DeliveryCompletion):
        _require(
            (response.tenant, response.run_id, response.turn_id)
            == (run.tenant, run.run_id, turn.turn_id),
            "foreign Complete response",
        )
    registry = request.tool_registry
    _require(
        request.tool_registry_head == reference(registry.registry_id, registry),
        "registry reference differs",
    )
    keys = tuple(
        (entry.tool_name, entry.tool_version, entry.schema_id) for entry in registry.entries
    )
    tools = manifest.artifact.tools
    _require(
        keys
        == tuple(sorted(set(keys)))
        == tuple(sorted((t.name, t.version, t.schema_id) for t in tools))
        and len({tool.name for tool in tools}) == len(tools),
        "registry coverage or ordering differs",
    )
    policies = {entry.tool_name: entry for entry in registry.entries}
    for tool in tools:
        policy = policies[tool.name]
        _require(
            policy.tool_schema == reference(tool.schema_id, tool)
            and policy.classification
            == ("CONSEQUENTIAL" if tool.name == "request_self_effect" else "PROPOSAL_ONLY")
            and isinstance(policy.retry_policy, NotApplicable),
            "registered proposal schema or classification differs",
        )
    calls = response.tool_calls if isinstance(response, ExecutionContinue) else ()
    _require(
        len(calls)
        == len(inner.ordered_calls)
        <= min(inner.bound.max_call_count, run.policy.max_tool_calls),
        "complete call count differs or exceeds bound",
    )
    records = []
    for ordinal, (parsed, sealed) in enumerate(zip(calls, inner.ordered_calls, strict=True)):
        policy = policies[parsed.tool]
        original = call.OriginalCallKey(
            tenant_id=run.tenant,
            original_run_id=run.run_id,
            original_turn_id=turn.turn_id,
            captured_response=capture,
            ordinal=ordinal,
            model_call_label=parsed.call_id,
        )
        _require(
            sealed.original == original
            and sealed.classification == policy.classification
            and sealed.tool_schema == policy.tool_schema
            and sealed.tool_policy == policy.tool_policy
            and isinstance(sealed.retry_lineage, NotApplicable)
            and _decode(sealed.canonical_call_base64) == parsed.canonical_bytes(),
            "initialized call differs from captured call and registered policy",
        )
        identity = "call:" + original.digest()
        _require(identity not in occupied, "original call already initialized")
        records.append(
            call.InitializedCallRecord(original_call_id=identity, call=sealed, predecessor=Absent())
        )
    heads = tuple(reference(record.original_call_id, record) for record in records)
    seal = call.SealedResponseRecord(
        response_seal_id="response-seal:" + inner.digest(),
        tenant_id=run.tenant,
        original_run_id=run.run_id,
        original_turn_id=turn.turn_id,
        captured_response=capture,
        complete_ordered_initialized=heads,
        bound=inner.bound,
    )
    complete_manifest = (reference(seal.response_seal_id, seal), *heads)
    _require(
        len(b"[" + b",".join(head.canonical_bytes() for head in complete_manifest) + b"]")
        <= inner.bound.max_manifest_bytes,
        "complete semantic manifest exceeds bound",
    )
    expected = call.PreparedCallFanOut(
        source_request_fingerprint=inner.digest(),
        response_seal=seal,
        initialized_records=tuple(records),
        complete_ordered_record_manifest=complete_manifest,
        proposal_fingerprint="0" * 64,
    )
    expected = expected.model_copy(
        update={"proposal_fingerprint": _without_fingerprint(expected, "proposal_fingerprint")}
    )
    outer = ExecutionCapturedFanOutProposal(
        source_request_fingerprint=request.digest(),
        fan_out=expected,
        sealed_run=proposed.sealed_run,
        proposal_fingerprint="0" * 64,
    )
    outer = outer.model_copy(
        update={"proposal_fingerprint": _without_fingerprint(outer, "proposal_fingerprint")}
    )
    _require(proposed == outer, "retained owner proposal differs from exact captured semantics")
    _require(
        len(proposed.canonical_bytes()) <= inner.bound.max_serialized_batch_bytes,
        "complete owner output exceeds bound",
    )
    sealed_run = proposed.sealed_run
    _require(
        sealed_run.head == "loop:" + sealed_run.model_copy(update={"head": "pending"}).digest(),
        "sealed Run self head differs",
    )
    _require(
        sealed_run.model_dump(exclude={"head", "predecessor", "event", "turns"})
        == run.model_dump(exclude={"head", "predecessor", "event", "turns"})
        and sealed_run.predecessor == run.head
        and sealed_run.turns[:-1] == run.turns[:-1]
        and len(sealed_run.turns) == len(run.turns),
        "unrelated Run fields changed",
    )
    continued = isinstance(response, ExecutionContinue)
    _require(
        sealed_run.event == ("ModelResponseSealed" if continued else "ModelCompletionPrepared"),
        "wrong sealing transition",
    )
    selected_turn = sealed_run.turns[-1]
    _require(
        selected_turn.head
        == "execution-turn:" + selected_turn.model_copy(update={"head": "pending"}).digest()
        and selected_turn.model_dump(
            exclude={"head", "state", "attempts", "response_seal", "initialized_calls"}
        )
        == turn.model_dump(
            exclude={"head", "state", "attempts", "response_seal", "initialized_calls"}
        )
        and selected_turn.state == ("ACCEPTED" if continued else "RESPONSE_AVAILABLE")
        and selected_turn.response_seal == complete_manifest[0]
        and selected_turn.initialized_calls == heads
        and len(selected_turn.attempts) == len(turn.attempts)
        and selected_turn.attempts[:-1] == turn.attempts[:-1],
        "sealed Turn differs",
    )
    selected_attempt = selected_turn.attempts[-1]
    if continued:
        _require(
            selected_attempt.state == "TERMINAL_ACCEPTED"
            and selected_attempt.head
            == "execution-attempt:"
            + selected_attempt.model_copy(update={"head": "pending"}).digest()
            and selected_attempt.model_dump(exclude={"head", "state"})
            == attempt.model_dump(exclude={"head", "state"}),
            "accepted attempt differs",
        )
    else:
        _require(
            selected_attempt.canonical_bytes() == attempt.canonical_bytes(),
            "Complete preparation consumed captured attempt",
        )


def _member(identity: str, owner: str, schema: str, raw: bytes) -> FanOutPhysicalMember:
    return FanOutPhysicalMember(
        record_id=identity,
        owner=owner,
        schema_id=schema,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def build_envelope(evidence: RetainedExecutionFanOutPreparation) -> ExecutionFanOutPhysicalEnvelope:
    return ExecutionFanOutPhysicalEnvelope.model_validate_json(
        _envelope_bytes(evidence.canonical_bytes())
    )


@reuse_exact_bytes
def _envelope_bytes(raw: bytes) -> bytes:
    evidence = RetainedExecutionFanOutPreparation.model_validate_json(raw)
    _verify(evidence)
    prepared, run = evidence.proposal.fan_out, evidence.proposal.sealed_run
    seal = prepared.response_seal
    members = (
        _member(run.head, OWNER, EXECUTION_RUN_SCHEMA, run.canonical_bytes()),
        _member("record:" + seal.digest(), OWNER, SEAL_SCHEMA, seal.canonical_bytes()),
        *(
            _member(
                "record:" + record.digest(), OWNER, INITIALIZED_SCHEMA, record.canonical_bytes()
            )
            for record in prepared.initialized_records
        ),
    )
    envelope = ExecutionFanOutPhysicalEnvelope(
        tenant_id=run.tenant,
        idempotency_key=run.head,
        expected_head=evidence.request.request.cut.tenant_commit_sequence,
        records=members,
        request_fingerprint="0" * 64,
    )
    envelope = envelope.model_copy(
        update={"request_fingerprint": _without_fingerprint(envelope, "request_fingerprint")}
    )
    _require(
        len(envelope.canonical_bytes())
        <= evidence.request.request.bound.max_serialized_batch_bytes,
        "complete physical envelope exceeds bound",
    )
    physical_command(envelope)
    return envelope.canonical_bytes()


def physical_command(envelope: ExecutionFanOutPhysicalEnvelope) -> PhysicalPublicationCommand:
    envelope = ExecutionFanOutPhysicalEnvelope.model_validate_json(envelope.canonical_bytes())
    _require(
        envelope.request_fingerprint == _without_fingerprint(envelope, "request_fingerprint"),
        "physical envelope fingerprint differs",
    )
    ids = tuple(member.record_id for member in envelope.records)
    _require(
        len(ids) == len(set(ids)) and all("\n" not in value and "\r" not in value for value in ids),
        "physical member identities duplicate or contain newline",
    )
    records = []
    for member in envelope.records:
        raw = _decode(member.canonical_payload_base64)
        _require(
            hashlib.sha256(raw).hexdigest() == member.fingerprint,
            "physical member fingerprint differs",
        )
        records.append(
            PhysicalRecord(
                member.record_id, member.owner, member.schema_id, raw, member.fingerprint
            )
        )
    return PhysicalPublicationCommand(
        envelope.tenant_id,
        envelope.operation_kind,
        envelope.idempotency_key,
        envelope.request_fingerprint,
        envelope.expected_head,
        envelope.fence_generation,
        envelope.expected_fence_frontier,
        envelope.minimum_fence_frontier,
        tuple(records),
    )
