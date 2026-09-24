"""Pure cross-owner acceptance binding; no owner calls, storage, CAS or dispatch.

Supplied history and authority remain untrusted. A passing envelope proves only
the closed structural interpretation and exact owner-byte link graph. Runtime
publication still requires authenticated inventory, current sources and writer CAS.
"""

import base64
import hashlib
import json
from typing import Literal

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
    RecoveryDTO,
)
from chiplog.capabilities.effects import contracts as effects
from chiplog.composition.r14_acceptance_contracts import (
    ACCEPTED_SCHEMA,
    EFFECT_SCHEMA,
    EXECUTION_SCHEMA,
    MAX_ACCEPTANCE_BYTES,
    AcceptancePhysicalEnvelope,
    AcceptancePhysicalMember,
    RetainedAcceptancePreparation,
    effects_dispatch_semantics,
    loop_dispatch_semantics,
)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("invalid acceptance envelope: " + reason)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _without(value: RecoveryDTO, field: str) -> str:
    body = json.loads(value.canonical_bytes())
    del body[field]
    return hashlib.sha256(
        json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _reference(subject: str, value: RecoveryDTO) -> call.CallSubjectHead:
    return call.CallSubjectHead(
        subject_id=subject,
        revision=Present(head="record:" + value.digest(), fingerprint=value.digest()),
    )


def _effects_reference(subject: str, body: object) -> effects.ExactHead:
    digest = _digest(body)
    return effects.ExactHead(subject_id=subject, head=subject + "/" + digest, fingerprint=digest)


def _translate(ref: call.CallSubjectHead) -> effects.ExactHead:
    return effects.ExactHead(
        subject_id=ref.subject_id, head=ref.revision.head, fingerprint=ref.revision.fingerprint
    )


def _verify_inventory(cut: call.CallPreparationCut) -> None:
    """Check supplied graph consistency without claiming complete durable history."""
    lineages: set[str] = set()
    for row in cut.predecessor_inventory.ordered_calls:
        initialized = row.initialized_record
        sealed = initialized.call
        _require(
            sealed.original.tenant_id == cut.tenant_id
            and row.original_call_id == initialized.original_call_id
            and row.original_call_id == "call:" + sealed.original.digest()
            and initialized.predecessor == Absent()
            and row.initialized == _reference(row.original_call_id, initialized)
            and row.acceptance.initialized == row.initialized.revision,
            "inventory member initialization differs",
        )
        raw = base64.b64decode(sealed.canonical_call_base64, validate=True)
        _require(
            base64.b64encode(raw).decode() == sealed.canonical_call_base64,
            "noncanonical inventory call encoding",
        )
        lineage = sealed.retry_lineage
        if sealed.classification == "READ_ONLY":
            _require(isinstance(lineage, call.InitializedReadOnlyLineage), "missing retry lineage")
            assert isinstance(lineage, call.InitializedReadOnlyLineage)
            _require(
                lineage.lineage.original_call_id == row.original_call_id
                and lineage.lineage.lineage_id not in lineages,
                "foreign or reused retry lineage",
            )
            lineages.add(lineage.lineage.lineage_id)
        else:
            _require(lineage == NotApplicable(), "non-read-only retry lineage")
        branch = row.acceptance
        if branch.kind == "READ_ONLY_ACCEPTED":
            _require(
                isinstance(lineage, call.InitializedReadOnlyLineage)
                and branch.lineage == lineage.lineage
                and branch.lineage_id == lineage.lineage.lineage_id,
                "read-only acceptance differs from initialization",
            )
        elif branch.kind == "CONSEQUENTIAL_ACCEPTED":
            manifest: tuple[Present, ...] = (branch.accepted, branch.execution_intent)
            if isinstance(branch.external_effect_intent, Present):
                manifest += (branch.external_effect_intent,)
            _require(
                sealed.classification == "CONSEQUENTIAL"
                and branch.complete_acceptance_manifest == manifest,
                "inventory consequential branch mismatch",
            )
    source_ids = tuple(source.source_id for source in cut.sources)
    _require(len(set(source_ids)) == len(source_ids), "duplicate observation source")
    for source in cut.sources:
        raw = base64.b64decode(source.canonical_value_base64, validate=True)
        _require(
            base64.b64encode(raw).decode() == source.canonical_value_base64
            and source.observed_at_ns < source.valid_until_ns,
            "invalid observation encoding or interval",
        )


def _verify_loop(retained: RetainedAcceptancePreparation) -> None:
    verify_loop_preparation(
        retained.loop_request, retained.loop_proposal, loop_dispatch_semantics()
    )


def verify_loop_preparation(
    request: call.AcceptConsequentialCallRequest,
    proposal: call.PreparedConsequentialAcceptance,
    expected_semantics: call.CallDispatchSemantics,
) -> None:
    """Version-neutral link graph; the registered caller fixes expected semantics.

    This checks supplied bytes, never the existence or authenticity of history.
    """
    binding, initialized = request.binding, request.initialized_record
    cut = binding.cut
    _require(isinstance(cut.fence, NonSchedulerFence), "unsupported execution fence")
    assert isinstance(cut.fence, NonSchedulerFence)
    _require(
        cut.current_run.subject_id == cut.fence.run_id
        and cut.current_run.revision.head == cut.fence.run_head,
        "current Run/fence mismatch",
    )
    _require(
        initialized.predecessor == Absent()
        and initialized.call.classification == "CONSEQUENTIAL"
        and initialized.call.retry_lineage == NotApplicable()
        and initialized.call.original == binding.original
        and binding.original.tenant_id == cut.tenant_id
        and initialized.original_call_id == binding.original_call_id
        and binding.original_call_id == "call:" + binding.original.digest()
        and binding.initialized == _reference(binding.original_call_id, initialized)
        and binding.tool_schema == initialized.call.tool_schema
        and binding.tool_policy == initialized.call.tool_policy,
        "original initialization or tool binding mismatch",
    )
    raw = base64.b64decode(initialized.call.canonical_call_base64, validate=True)
    _require(
        base64.b64encode(raw).decode() == initialized.call.canonical_call_base64,
        "noncanonical call encoding",
    )
    inventory = cut.predecessor_inventory
    _require(
        inventory.tenant_id == cut.tenant_id
        and inventory.tenant_commit_sequence == cut.tenant_commit_sequence
        and cut.complete_call_inventory == _reference("call-inventory:" + cut.tenant_id, inventory),
        "inventory reference mismatch",
    )
    ids = tuple(row.original_call_id for row in inventory.ordered_calls)
    _require(ids == tuple(sorted(set(ids))), "duplicate or unordered inventory")
    _verify_inventory(cut)
    target = [
        row for row in inventory.ordered_calls if row.original_call_id == binding.original_call_id
    ]
    _require(len(target) == 1, "missing original call")
    row = target[0]
    _require(
        row.initialized_record == initialized
        and row.initialized == binding.initialized
        and row.acceptance.kind == "INITIALIZED"
        and row.acceptance.initialized == binding.initialized.revision
        and row.terminal == Absent(),
        "call is not the exact pending initialization",
    )
    _require(binding.dispatch_semantics == expected_semantics, "unregistered loop semantics")
    digest = request.digest()
    accepted = call.ToolCallAcceptedRecord(
        accepted_id="accepted:" + digest,
        source_command_id=request.command_id,
        binding=binding,
        execution_intent_id="execution:" + digest,
    )
    accepted_ref = _reference(accepted.accepted_id, accepted)
    execution = call.ToolExecutionIntentRecord(
        execution_intent_id=accepted.execution_intent_id,
        original_call_id=binding.original_call_id,
        initialized=binding.initialized,
        accepted=accepted_ref,
        external_intent=binding.external_intent,
    )
    _require(
        proposal.source_request_fingerprint == digest
        and proposal.accepted == accepted
        and proposal.execution_intent == execution
        and proposal.complete_acceptance_manifest
        == (
            accepted_ref,
            _reference(execution.execution_intent_id, execution),
            binding.external_intent,
        )
        and proposal.proposal_fingerprint == _without(proposal, "proposal_fingerprint"),
        "loop proposal or semantic manifest mismatch",
    )


def _verify_effects(retained: RetainedAcceptancePreparation) -> None:
    request, loop = retained.loop_request, retained.loop_proposal
    command, proposal = retained.effects_command, retained.effects_proposal
    binding, intent = request.binding, command.intent
    record, store = proposal.record, proposal.expected_store
    manifest = tuple(_translate(ref) for ref in loop.complete_acceptance_manifest)
    data = intent.model_dump(mode="json")
    del data["fingerprint"]
    _require(
        intent.fingerprint == _digest(data)
        and intent.effect_fingerprint == hashlib.sha256(intent.payload).hexdigest()
        and intent.purpose.kind == "ORDINARY_EFFECT"
        and intent.semantics == effects_dispatch_semantics(),
        "invalid intent content or unsupported purpose/semantics",
    )
    intent_ref = effects.ExactHead(
        subject_id=intent.intent_id,
        head=intent.intent_id + "/" + intent.fingerprint,
        fingerprint=intent.fingerprint,
    )
    _require(_translate(binding.external_intent) == intent_ref, "immutable external intent differs")
    _require(
        command.call_id == binding.original_call_id
        and command.initialized_call == _translate(binding.initialized)
        and command.tool_schema_policy
        == (_translate(binding.tool_schema), _translate(binding.tool_policy))
        and command.execution_intent == manifest[1]
        and command.complete_acceptance_manifest == manifest
        and proposal.exact_companion_manifest == manifest
        and command.fence.canonical_bytes() == binding.cut.fence.canonical_bytes(),
        "cross-owner call, tool, manifest or fence mismatch",
    )
    _require(
        store.tenant_id == binding.cut.tenant_id == intent.authority.tenant_id
        and store.tenant_head == binding.cut.tenant_commit_sequence
        and command.identity.expected_tenant_head == store.tenant_head
        and all(
            item.snapshot.intent.authority.tenant_id == store.tenant_id for item in store.records
        )
        and not any(
            item.command.command_id == command.identity.command_id
            or item.snapshot.intent.intent_id == intent.intent_id
            for item in store.records
        ),
        "effects predecessor cut mismatch or reused identity",
    )
    snapshot = record.snapshot
    _require(
        record.command == command.identity
        and record.predecessor is None
        and record.kind == "INTENT_ACCEPTED"
        and record.source_command == command.canonical_bytes()
        and snapshot.intent == intent
        and snapshot.state == "INTENT_RECORDED"
        and snapshot.authorizations == ()
        and snapshot.transmissions == ()
        and snapshot.evidence == ()
        and snapshot.evidence_records == ()
        and snapshot.unresolved_obligations == ()
        and snapshot.recovery_obligation is None,
        "effects record is not the exact initial intent acceptance",
    )
    snapshot_body = snapshot.model_dump(mode="json")
    del snapshot_body["attempt"]
    expected_attempt = _effects_reference(
        intent.intent_id + "/attempt",
        {
            "command": command.identity.model_dump(mode="json"),
            "predecessor": None,
            "snapshot": snapshot_body,
        },
    )
    _require(snapshot.attempt == expected_attempt, "effects attempt preimage mismatch")
    expected_record = _effects_reference(
        "effects/" + command.identity.command_id,
        {
            "command": command.identity.model_dump(mode="json"),
            "predecessor": None,
            "kind": "INTENT_ACCEPTED",
            "snapshot": snapshot.model_dump(mode="json"),
            "source_command": command.canonical_bytes().hex(),
        },
    )
    _require(record.record == expected_record, "effects record preimage mismatch")


def _member(
    identity: str,
    owner: Literal["agent_loop", "effects"],
    schema: str,
    raw: bytes,
) -> AcceptancePhysicalMember:
    return AcceptancePhysicalMember(
        record_id=identity,
        owner=owner,
        schema_id=schema,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def build_acceptance_envelope(
    retained: RetainedAcceptancePreparation,
) -> AcceptancePhysicalEnvelope:
    """Validate supplied bytes only. Return no command or credential for a writer."""
    retained = RetainedAcceptancePreparation.model_validate_json(retained.canonical_bytes())
    # Each owner has its own canonicalization domain, including bytes and key order.
    for value in (
        retained.loop_request,
        retained.loop_proposal,
        retained.effects_command,
        retained.effects_proposal,
    ):
        type(value).model_validate_json(value.canonical_bytes())
    _verify_loop(retained)
    _verify_effects(retained)
    proposal = retained.loop_proposal
    effect = retained.effects_proposal.record
    members = (
        _member(
            proposal.accepted.accepted_id,
            "agent_loop",
            ACCEPTED_SCHEMA,
            proposal.accepted.canonical_bytes(),
        ),
        _member(
            proposal.execution_intent.execution_intent_id,
            "agent_loop",
            EXECUTION_SCHEMA,
            proposal.execution_intent.canonical_bytes(),
        ),
        _member(effect.record.head, "effects", EFFECT_SCHEMA, effect.canonical_bytes()),
    )
    _require(len({row.record_id for row in members}) == 3, "duplicate physical identity")
    _require(
        all("\n" not in row.record_id and "\r" not in row.record_id for row in members),
        "physical identity contains a membership delimiter",
    )
    envelope = AcceptancePhysicalEnvelope(
        tenant_id=retained.loop_request.binding.cut.tenant_id,
        original_call_id=retained.loop_request.binding.original_call_id,
        expected_tenant_head=retained.loop_request.binding.cut.tenant_commit_sequence,
        retained_preparation_fingerprint=retained.digest(),
        complete_records=members,
        physical_batch_fingerprint="0" * 64,
    )
    envelope = envelope.model_copy(
        update={"physical_batch_fingerprint": _without(envelope, "physical_batch_fingerprint")}
    )
    _require(
        len(envelope.canonical_bytes()) <= MAX_ACCEPTANCE_BYTES, "physical byte bound exceeded"
    )
    return envelope


def verify_acceptance_envelope(
    retained: RetainedAcceptancePreparation,
    envelope: AcceptancePhysicalEnvelope,
) -> None:
    envelope = AcceptancePhysicalEnvelope.model_validate_json(envelope.canonical_bytes())
    _require(envelope == build_acceptance_envelope(retained), "physical envelope differs")
