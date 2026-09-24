"""Fanout physical command conversion and selected lifecycle inventory."""

import base64

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import Continue, LoopSnapshot
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.composition.r14_cancellation_contracts import RetainedCancellationPreparation
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord

from .r14_fanout_contracts import FanOutPhysicalEnvelope, RetainedFanOutPreparation
from .r14_fanout_verification import _decode, _require, validate_envelope
from .r14_fanout_verification import build_envelope as build_envelope
from .r14_fanout_verification import reference as reference


def physical_command(envelope: FanOutPhysicalEnvelope) -> PhysicalPublicationCommand:
    envelope = validate_envelope(envelope)
    records = [
        PhysicalRecord(
            member.record_id,
            member.owner,
            member.schema_id,
            base64.b64decode(member.canonical_payload_base64, validate=True),
            member.fingerprint,
        )
        for member in envelope.records
    ]
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
