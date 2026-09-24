"""Pure verification of retained fanout bytes; no issuance or publication authority."""

import base64
import hashlib
import json

from chiplog.adapters.driven.loop_sqlite import OWNER, SCHEMA
from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import Complete, Continue, Frozen, LoopSnapshot
from chiplog.capabilities.agent_loop.fan_out_contracts import CapturedFanOutProposal
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.capabilities.agent_loop.response_parsing import parse_captured_response
from chiplog.composition.r14_cancellation_contracts import RetainedCancellationPreparation
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord

from ._pure_bytes import reuse_exact_bytes
from .r14_fanout_contracts import (
    INITIALIZED_SCHEMA,
    SEAL_SCHEMA,
    FanOutPhysicalEnvelope,
    FanOutPhysicalMember,
    RetainedFanOutPreparation,
)


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


def _verify(evidence: RetainedFanOutPreparation) -> None:
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
    turn = domain.current_turn(run)
    attempt = domain.current_attempt(run, "RESPONSE_CAPTURED")
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
    response = parse_captured_response(raw, manifest.artifact)
    _require(isinstance(response, Continue | Complete), "expanded completion fanout is unsupported")
    assert isinstance(response, Continue | Complete)
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
            and policy.classification == "PROPOSAL_ONLY"
            and isinstance(policy.retry_policy, NotApplicable),
            "registered proposal schema or classification differs",
        )
    calls = response.tool_calls if isinstance(response, Continue) else ()
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
            and sealed.classification == "PROPOSAL_ONLY"
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
    outer = CapturedFanOutProposal(
        source_request_fingerprint=request.digest(), fan_out=expected, proposal_fingerprint="0" * 64
    )
    outer = outer.model_copy(
        update={"proposal_fingerprint": _without_fingerprint(outer, "proposal_fingerprint")}
    )
    _require(proposed == outer, "retained owner proposal differs from exact captured semantics")
    accepted = (
        domain.accept_tools(run, response)
        if isinstance(response, Continue)
        else domain.complete(run, response)
    )
    _require(
        evidence.accepted_run == accepted, "accepted Run differs from exact captured transition"
    )
    domain.validate_record(run, evidence.accepted_run)
    if isinstance(response, Continue):
        _require(not evidence.companions, "Continue has unregistered companion")
    else:
        _require(
            len(evidence.companions) == 1, "Complete requires exactly one conversation companion"
        )
        companion = evidence.companions[0]
        _require(
            (companion.record_id, companion.owner, companion.schema_id)
            == (run.run_id + "/accepted", CONVERSATION_OWNER, CONVERSATION_SCHEMA),
            "Complete conversation companion identity differs",
        )
        _decode(companion.payload_base64)


def _member(identity: str, owner: str, schema: str, raw: bytes) -> FanOutPhysicalMember:
    return FanOutPhysicalMember(
        record_id=identity,
        owner=owner,
        schema_id=schema,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def build_envelope(evidence: RetainedFanOutPreparation) -> FanOutPhysicalEnvelope:
    return FanOutPhysicalEnvelope.model_validate_json(_envelope_bytes(evidence.canonical_bytes()))


@reuse_exact_bytes
def _envelope_bytes(raw: bytes) -> bytes:
    evidence = RetainedFanOutPreparation.model_validate_json(raw)
    _verify(evidence)
    prepared, run = evidence.proposal.fan_out, evidence.accepted_run
    seal = prepared.response_seal
    members = (
        _member(run.head, OWNER, SCHEMA, run.canonical_bytes()),
        _member("record:" + seal.digest(), OWNER, SEAL_SCHEMA, seal.canonical_bytes()),
        *(
            _member(
                "record:" + record.digest(), OWNER, INITIALIZED_SCHEMA, record.canonical_bytes()
            )
            for record in prepared.initialized_records
        ),
        *(
            _member(
                companion.record_id,
                companion.owner,
                companion.schema_id,
                _decode(companion.payload_base64),
            )
            for companion in evidence.companions
        ),
    )
    envelope = FanOutPhysicalEnvelope(
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


def physical_command(envelope: FanOutPhysicalEnvelope) -> PhysicalPublicationCommand:
    envelope = FanOutPhysicalEnvelope.model_validate_json(envelope.canonical_bytes())
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


def inventory_from_history(
    tenant: str,
    snapshot: LoopSnapshot,
    preparations: tuple[RetainedFanOutPreparation, ...],
    cancellations: tuple[RetainedCancellationPreparation, ...] = (),
) -> call.CallInventorySnapshot:
    from chiplog.composition.r14_cancellation_records import build_cancellation_envelope

    snapshot = LoopSnapshot.model_validate_json(snapshot.canonical_bytes())
    _require(
        all(run.tenant == tenant for run in snapshot.records), "foreign Run in inventory history"
    )
    history = {run.head: index for index, run in enumerate(snapshot.records)}
    _require(len(history) == len(snapshot.records), "duplicate Run history head")
    observations: dict[str, call.CallLifecycleObservation] = {}
    cancelled: dict[str, RetainedCancellationPreparation] = {}
    for cancellation in cancellations:
        build_cancellation_envelope(cancellation)
        identity = cancellation.request.original_call_id
        _require(identity not in cancelled, "duplicate selected cancellation")
        _require(
            cancellation.run_companion.tenant == tenant
            and cancellation.run_companion.head in history
            and snapshot.records[history[cancellation.run_companion.head]]
            == cancellation.run_companion,
            "cancellation companion absent from original history",
        )
        cancelled[identity] = cancellation
    captures: set[str] = set()
    for retained in preparations:
        retained = RetainedFanOutPreparation.model_validate_json(retained.canonical_bytes())
        build_envelope(retained)
        captured, accepted = retained.request.captured_run, retained.accepted_run
        _require(
            captured.tenant == tenant and accepted.tenant == tenant, "foreign retained capture"
        )
        _require(captured.head not in captures, "duplicate selected capture seal")
        captures.add(captured.head)
        _require(
            captured.head in history and accepted.head in history,
            "retained capture or acceptance absent from history",
        )
        capture_index, accepted_index = history[captured.head], history[accepted.head]
        _require(
            capture_index < accepted_index
            and snapshot.records[capture_index] == captured
            and snapshot.records[accepted_index] == accepted,
            "retained Run history differs",
        )
        following = tuple(
            row for row in snapshot.records[accepted_index + 1 :] if row.run_id == accepted.run_id
        )
        previous = accepted
        for row in following:
            domain.validate_record(previous, row)
            previous = row
        for initialized in retained.proposal.fan_out.initialized_records:
            identity = initialized.original_call_id
            _require(identity not in observations, "duplicate original call initialization")
            ref = reference(identity, initialized)
            terminal: Absent | Present = Absent()
            original = initialized.call.original
            parsed = Continue.model_validate_json(
                _decode(retained.request.request.canonical_response_base64)
            ).tool_calls[original.ordinal]
            for row in following:
                if row.event != "ToolTerminal":
                    continue
                turns = [turn for turn in row.turns if turn.turn_id == original.original_turn_id]
                _require(len(turns) == 1, "original Turn absent or duplicate in history")
                outcomes = turns[0].sealed_calls or ()
                _require(
                    len(outcomes) > original.ordinal, "original call absent from terminal history"
                )
                outcome = outcomes[original.ordinal]
                _require(
                    outcome.call == parsed
                    and outcome.proposal_id
                    == original.original_turn_id + "/proposal/" + parsed.call_id,
                    "terminal history call differs from initialization",
                )
                if outcome.state == "TERMINAL":
                    selected_cancellation = cancelled.get(identity)
                    if selected_cancellation is not None:
                        _require(
                            row == selected_cancellation.run_companion
                            and selected_cancellation.request.initialized_record == initialized
                            and selected_cancellation.request.initialized == ref,
                            "selected cancellation conflicts with original terminal",
                        )
                        terminal = reference(
                            selected_cancellation.proposal.terminal.terminal_id,
                            selected_cancellation.proposal.terminal,
                        ).revision
                    else:
                        terminal = Present(head=row.head, fingerprint=row.digest())
                    break
            observations[identity] = call.CallLifecycleObservation(
                original_call_id=identity,
                initialized=ref,
                initialized_record=initialized,
                acceptance=InitializedCall(initialized=ref.revision),
                terminal=terminal,
            )
    _require(set(cancelled) <= set(observations), "cancelled call absent from fanout history")
    return call.CallInventorySnapshot(
        tenant_id=tenant,
        tenant_commit_sequence=snapshot.tenant_head,
        ordered_calls=tuple(observations[key] for key in sorted(observations)),
    )
