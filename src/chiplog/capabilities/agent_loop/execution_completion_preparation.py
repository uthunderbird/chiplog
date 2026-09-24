"""Native-v2 owner preparation for one sealed zero-call Complete response.

The returned proposal is inert.  The broker must still authenticate and
re-enumerate the sealed source and recovery cut when it selects publication.
"""

from __future__ import annotations

import hashlib
import json
from base64 import b64encode

from pydantic import ValidationError

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    SealedResponseRecord,
)
from .call_acceptance_preparation import call_record_reference
from .completion_owner_record_contracts import (
    completion_request_fingerprint,
    make_prepared_delivery_acceptance_member,
    make_terminal_manifest_member,
    make_terminal_run_member,
    validate_completion_request_source,
)
from .contracts import DeliveryAcceptanceReference
from .delivery_preparation import DeliveryAcceptanceProposal, DeliveryCompletion, prepare_completion
from .execution_completion_contracts import (
    ExecutionCompletionResult,
    PreparedExecutionCompletion,
    PrepareExecutionCompletion,
)
from .execution_contracts import ExecutionRunRecord
from .execution_recovery_observations import ExecutionTerminalManifest, SealedAccountingRecord
from .recovery_contracts import Present
from .recovery_domain import sealed_call_accounting
from .recovery_frontier_registry_contracts import frontier_registry_reference

RESPONSE_SEAL_SCHEMA = "chiplog.call.response-seal.v1"
FRONTIER_REGISTRY_SCHEMA = "chiplog.recovery.frontier-registry.v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rejected(request: object, code: str, reason: str) -> CallPreparationRejected:
    command_id = getattr(request, "command_id", None)
    return CallPreparationRejected(
        command_id=command_id if isinstance(command_id, str) and command_id else "invalid-command",
        code=code,  # type: ignore[arg-type]
        reason=reason,
    )


def _source_matches(
    request: PrepareExecutionCompletion, *, schema_id: str, raw: bytes, subject: CallSubjectHead
) -> bool:
    return any(
        source.schema_id == schema_id
        and source.canonical_record_bytes == raw
        and source.subject == subject
        for source in request.cut.complete_sources
    )


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=_sha(run.canonical_bytes())),
    )


def _current_seal(
    request: PrepareExecutionCompletion, run: ExecutionRunRecord
) -> tuple[SealedResponseRecord, CallSubjectHead]:
    turn = run.turns[-1]
    if (
        run.event != "ModelCompletionPrepared"
        or turn.state != "RESPONSE_AVAILABLE"
        or turn.response_seal is None
        or turn.initialized_calls != ()
    ):
        raise ValueError("completion requires a sealed zero-call native Run")
    matches = [
        view
        for view in request.cut.complete_ordered_responses
        if view.selected_seal == turn.response_seal
    ]
    if len(matches) != 1:
        raise ValueError("completion cut has no unique selected response seal")
    view = matches[0]
    if request.cut.current_run != _run_ref(run) or not _source_matches(
        request, schema_id=run.schema_id, raw=run.canonical_bytes(), subject=_run_ref(run)
    ):
        raise ValueError("sealed native Run lacks an exact retained source")
    predecessors = [
        candidate
        for candidate in request.cut.complete_ordered_run_lineage
        if candidate.head == run.predecessor
    ]
    if len(predecessors) != 1 or not isinstance(predecessors[0], ExecutionRunRecord):
        raise ValueError("sealed native Run has no unique native capture predecessor")
    captured = predecessors[0]
    captured_ref = _run_ref(captured)
    if (
        not _source_matches(
            request,
            schema_id=captured.schema_id,
            raw=captured.canonical_bytes(),
            subject=captured_ref,
        )
        or view.original_run != captured_ref
        or view.original_turn
        != CallSubjectHead(
            subject_id=captured.turns[-1].turn_id,
            revision=Present(
                head=captured.turns[-1].head,
                fingerprint=_sha(captured.turns[-1].canonical_bytes()),
            ),
        )
    ):
        raise ValueError("response seal is not bound to exact captured native Run/Turn")
    seal = view.seal
    expected_seal = call_record_reference(seal.response_seal_id, seal)
    if (
        turn.response_seal != expected_seal
        or view.selected_seal != expected_seal
        or seal.tenant_id != run.tenant
        or seal.original_run_id != run.run_id
        or seal.original_turn_id != turn.turn_id
        or seal.captured_response != captured_ref
        or seal.complete_ordered_initialized != ()
        or view.ordered_calls != ()
    ):
        raise ValueError("selected response seal differs from native zero-call Turn")
    if not _source_matches(
        request,
        schema_id=RESPONSE_SEAL_SCHEMA,
        raw=seal.canonical_bytes(),
        subject=expected_seal,
    ):
        raise ValueError("selected response seal lacks an exact retained source")
    return seal, expected_seal


def _accounting(
    request: PrepareExecutionCompletion, seal: SealedResponseRecord, seal_ref: CallSubjectHead
) -> SealedAccountingRecord:
    registry_ref = frontier_registry_reference(request.cut.frontier.registry)
    if not _source_matches(
        request,
        schema_id=FRONTIER_REGISTRY_SCHEMA,
        raw=request.cut.frontier.registry.canonical_bytes(),
        subject=registry_ref,
    ):
        raise ValueError("frontier registry lacks an exact retained source")
    frontier_ref = call_record_reference(
        "recovery-frontier:" + request.cut.frontier.run_id, request.cut.frontier
    )
    views = [
        view for view in request.cut.complete_ordered_responses if view.selected_seal == seal_ref
    ]
    assert len(views) == 1
    if views[0].frontier_head != frontier_ref:
        raise ValueError("selected response frontier differs from recovery cut")
    accounted = sealed_call_accounting(
        seal.response_seal_id, seal.digest(), (), views[0].ordered_calls
    )
    preimage = {
        "domain": "chiplog.execution.sealed-accounting.v1",
        "source_cut_fingerprint": request.cut.digest(),
        "sealed_response": seal_ref.model_dump(mode="json"),
        "sealed_manifest_fingerprint": seal.digest(),
        "registry": registry_ref.model_dump(mode="json"),
        "frontier": frontier_ref.model_dump(mode="json"),
        "complete_ordered_calls": [item.model_dump(mode="json") for item in accounted.calls],
    }
    accounting_id = "sealed-accounting:" + _sha(
        json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()
    )
    return SealedAccountingRecord(
        accounting_id=accounting_id,
        source_cut_fingerprint=request.cut.digest(),
        sealed_response=seal_ref,
        sealed_manifest_fingerprint=seal.digest(),
        registry=registry_ref,
        frontier=frontier_ref,
        complete_ordered_calls=accounted.calls,
    )


def _terminal_run(
    run: ExecutionRunRecord, proposal: DeliveryAcceptanceProposal
) -> ExecutionRunRecord:
    turn = run.turns[-1]
    attempt = turn.attempts[turn.selector]
    accepted_attempt = attempt.model_copy(update={"state": "TERMINAL_ACCEPTED", "head": "pending"})
    accepted_attempt = accepted_attempt.model_copy(
        update={"head": "execution-attempt:" + accepted_attempt.digest()}
    )
    accepted_turn = turn.model_copy(
        update={
            "state": "ACCEPTED",
            "attempts": (
                *turn.attempts[: turn.selector],
                accepted_attempt,
                *turn.attempts[turn.selector + 1 :],
            ),
            "head": "pending",
        }
    )
    accepted_turn = accepted_turn.model_copy(
        update={"head": "execution-turn:" + accepted_turn.digest()}
    )
    delivery = DeliveryAcceptanceReference(
        acceptance_identity=proposal.acceptance.identity,
        acceptance_head=proposal.acceptance.head,
        acceptance_fingerprint=proposal.acceptance.fingerprint,
        proposal_canonical_base64=b64encode(proposal.canonical_bytes()).decode(),
    )
    pending = run.model_copy(
        update={
            "state": "SUCCEEDED",
            "predecessor": run.head,
            "event": "ExecutionCompleted",
            "turns": (*run.turns[:-1], accepted_turn),
            "delivery_acceptance": delivery,
            "head": "pending",
        }
    )
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def prepare_execution_completion(request: PrepareExecutionCompletion) -> ExecutionCompletionResult:
    """Prepare the H1 native-v2 terminal proposal from one selected sealed response."""
    try:
        request = PrepareExecutionCompletion.model_validate_json(request.canonical_bytes())
        if not isinstance(request.run, ExecutionRunRecord):
            return _rejected(request, "UNSUPPORTED", "only native v2 completion is mounted")
        selected_turn = validate_completion_request_source(request)
        if selected_turn is not request.run.turns[-1]:
            raise ValueError("selected completion Turn is not current")
        completion = DeliveryCompletion.model_validate_json(request.exact_captured_response)
        if completion.canonical_bytes() != request.exact_captured_response:
            raise ValueError("completion response bytes are not canonical")
        seal, seal_ref = _current_seal(request, request.run)
        accounting = _accounting(request, seal, seal_ref)
        proposal = prepare_completion(completion, request.delivery)
        terminal = _terminal_run(request.run, proposal)
        manifest = ExecutionTerminalManifest(
            manifest_id="terminal-manifest:" + _sha(request.canonical_bytes()),
            command_id=request.command_id,
            prior_run=request.cut.current_run,
            target="SUCCEEDED",
            source_cut_fingerprint=request.cut.digest(),
            complete_accounting=(accounting,),
            complete_open_original_obligations=request.run.original_obligations,
        )
        seed = PreparedExecutionCompletion(
            source_request_fingerprint=completion_request_fingerprint(request),
            complete_earlier_continuations=request.complete_earlier_continuations,
            delivery=proposal,
            terminal_manifest=manifest,
            run=terminal,
            complete_owner_commitment="0" * 64,
        )
        delivery_member = make_prepared_delivery_acceptance_member(request, seed)
        manifest_member = make_terminal_manifest_member(manifest)
        run_member = make_terminal_run_member(terminal)
        return seed.model_copy(
            update={
                "complete_owner_commitment": _sha(
                    delivery_member.canonical_record_bytes
                    + manifest_member.canonical_record_bytes
                    + run_member.canonical_record_bytes
                )
            }
        )
    except (ValidationError, ValueError, TypeError, IndexError) as error:
        return _rejected(request, "INTEGRITY_FAULT", str(error))
