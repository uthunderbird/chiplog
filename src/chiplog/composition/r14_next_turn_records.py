"""Retained native next-Turn owner exchange and selected history validation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    CancelledBeforeAcceptRecord,
    NotExecutedCallResultRecord,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_next_turn_preparation import (
    reconstruct_prior_continuation_cut,
    verify_next_native_execution_turn,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedNextExecutionTurn,
    PrepareNextExecutionTurn,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    native_recovery_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    InitializedCall,
    TerminalCallFrontier,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from .common_cli_execution_runtime import CommonCliExecutionRuntime

from .r14_cancellation_contracts import CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA
from .r14_recovery_publication import (
    ACCOUNTING_SCHEMA,
    CONTINUATION_SCHEMA,
    RecoveryActorBudgetV1,
    RetainedRecoveryPublication,
    validate_recovery_actor_budget,
)
from .r14_recovery_publication import (
    OPERATION as ACCOUNTING_OPERATION,
)
from .r14_recovery_publication import (
    command as accounting_command,
)
from .r14_recovery_publication import (
    validate_selected as validate_accounting_selected,
)

NEXT_TURN_PUBLICATION = "agent_loop.execution-next-turn.v1"
RUN_SCHEMA = "chiplog.agent-loop.execution-record.v2"
OWNER_OPERATION = "agent_loop.prepare_next_execution_turn.v1"


class RetainedNextTurnPublication(RecoveryDTO):
    request: PrepareNextExecutionTurn
    result: PreparedNextExecutionTurn
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


def next_turn_command(evidence: RetainedNextTurnPublication) -> PhysicalPublicationCommand:
    request, result = evidence.request, evidence.result
    validate_recovery_actor_budget(evidence.actor_budget_v1)  # type: ignore[no-untyped-call]
    if evidence.actor_budget_v1.request.callee.tenant_id != request.cut.tenant_id:
        raise ValueError("next Turn actor budget tenant differs")
    verify_next_native_execution_turn(request, result)
    sent, reply = evidence.owner_call, evidence.owner_reply
    if (
        sent.operation_id != OWNER_OPERATION
        or sent.schema_id != EXECUTION_RECOVERY_REQUEST_SCHEMA
        or sent.canonical_payload != request.canonical_bytes()
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "agent_loop"
        or sent.caller.tenant_id != request.run.tenant
        or sent.callee.tenant_id != request.run.tenant
        or reply.responder != sent.callee
        or reply.request_id != sent.request_id
        or reply.schema_id != EXECUTION_RECOVERY_RESULT_SCHEMA
        or reply.canonical_payload != result.canonical_bytes()
        or result.source_request_fingerprint != request.digest()
    ):
        raise ValueError("next Turn retained owner exchange differs")
    run = result.run
    raw = run.canonical_bytes()
    return PhysicalPublicationCommand(
        tenant_id=run.tenant,
        operation_kind=NEXT_TURN_PUBLICATION,
        idempotency_key=f"execution-next-turn:{run.run_id}:{request.next_ordinal}",
        request_fingerprint=evidence.digest(),
        expected_head=request.cut.tenant_commit_sequence,
        expected_preimage_commitment=request.cut.materialization_commitment,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                run.head, "agent_loop", RUN_SCHEMA, raw, hashlib.sha256(raw).hexdigest()
            ),
        ),
    )


def retained_next_turn(entry: dict[str, object]) -> RetainedNextTurnPublication:
    raw = entry.get("execution_next_turn_evidence")
    if not isinstance(raw, str):
        raise ValueError("next Turn selected evidence missing")
    evidence = RetainedNextTurnPublication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("next Turn selected evidence noncanonical")
    return evidence


def selected_prior_turn_context(
    runtime: CommonCliExecutionRuntime, run: object
) -> list[dict[str, object]]:
    """Build the prior-turn model envelope from one authenticated same-Run publication."""
    from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
        AcceptedDirectTerminalDispositionV1,
        AcceptedDirectTerminalResultV1,
    )

    candidates: list[RetainedNextTurnPublication] = []
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED" or "execution_next_turn_evidence" not in entry:
            continue
        evidence = retained_next_turn(entry)
        if evidence.result.run == run:
            candidates.append(evidence)
    if len(candidates) != 1:
        raise ValueError("same-Run Turn 2 lacks one selected next-Turn publication")
    evidence = candidates[0]
    before, after = evidence.request.run, evidence.result.run
    if (
        not isinstance(run, ExecutionRunRecord)
        or before.run_id != after.run_id
        or before.turns != after.turns[:-1]
        or len(before.turns) != 1
        or len(after.turns) != 2
        or after.turns[-1].ordinal != 2
        or after.turns[-1].state != "PREPARING"
        or after.turns[-1].attempts
        or before.turns[0].response_seal is None
        or before.turns[0].selector >= len(before.turns[0].attempts)
    ):
        raise ValueError("selected same-Run continuation is not a fresh Turn 2")
    cut = evidence.request.cut
    responses = [
        view
        for view in cut.complete_ordered_responses
        if view.original_run.subject_id == before.run_id
        and view.original_turn.subject_id == before.turns[0].turn_id
    ]
    if len(responses) != 1 or responses[0].selected_seal != before.turns[0].response_seal:
        raise ValueError("Turn 2 selected recovery cut lacks the exact prior response seal")
    response = responses[0]
    attempt = before.turns[0].attempts[before.turns[0].selector]
    if (
        response.seal.original_run_id != before.run_id
        or response.seal.original_turn_id != before.turns[0].turn_id
    ):
        raise ValueError("Turn 2 selected response identifies another Run or Turn")
    if response.seal.complete_ordered_initialized != before.turns[0].initialized_calls:
        raise ValueError("Turn 2 selected response has another initialized-call set")
    sources = {
        (source.schema_id, source.subject.subject_id): source for source in cut.complete_sources
    }

    def selected_record(schema: str, ref: object, model: type) -> object:
        source = sources.get((schema, ref.subject_id))
        if (
            source is None
            or source.subject != ref
            or source.physical_record != ref
            or hashlib.sha256(source.canonical_record_bytes).hexdigest()
            != source.physical_record.revision.fingerprint
        ):
            raise ValueError("prior-turn result is not bound to its selected canonical source")
        value = model.model_validate_json(source.canonical_record_bytes)
        if value.canonical_bytes() != source.canonical_record_bytes:
            raise ValueError("prior-turn selected result is noncanonical")
        return value

    results: list[dict[str, object]] = []
    for call in response.ordered_calls:
        if (
            not isinstance(call, TerminalCallFrontier)
            or call.response_id != response.selected_seal.subject_id
        ):
            raise ValueError("prior-turn call differs from the selected response frontier")
        if isinstance(call.disposition, CancelledBeforeAcceptResult):
            if not isinstance(call.acceptance, InitializedCall):
                raise ValueError("cancelled prior-turn call was accepted")
            initialized_matches = [
                reference
                for reference in response.seal.complete_ordered_initialized
                if reference.subject_id == call.original_call_id
                and reference.revision == call.acceptance.initialized
            ]
            terminal_sources = [
                source
                for source in cut.complete_sources
                if source.schema_id == CANCELLATION_SCHEMA
                and source.subject.revision == call.disposition.terminal
            ]
            result_sources = [
                source
                for source in cut.complete_sources
                if source.schema_id == NOT_EXECUTED_SCHEMA
                and source.subject.revision == call.disposition.not_executed_result
            ]
            if (
                len(initialized_matches) != 1
                or len(terminal_sources) != 1
                or len(result_sources) != 1
            ):
                raise ValueError("cancelled prior-turn call lacks exact selected sources")
            initialized = initialized_matches[0]
            terminal_source = terminal_sources[0]
            result_source = result_sources[0]
            terminal = selected_record(
                CANCELLATION_SCHEMA, terminal_source.subject, CancelledBeforeAcceptRecord
            )
            result = selected_record(
                NOT_EXECUTED_SCHEMA, result_source.subject, NotExecutedCallResultRecord
            )
            if (
                terminal_source.subject.revision != call.disposition.terminal
                or result_source.subject.revision != call.disposition.not_executed_result
                or terminal.original_call_id != call.original_call_id
                or result.original_call_id != call.original_call_id
                or terminal.original.tenant_id != before.tenant
                or terminal.original.original_run_id != before.run_id
                or terminal.original.original_turn_id != before.turns[0].turn_id
                or terminal.original.captured_response != response.seal.captured_response
                or terminal.initialized != initialized
                or call.disposition.initialized_predecessor != initialized.revision
                or result.initialized != initialized
                or terminal.not_executed_result_id != result.result_id
                or result.terminal != terminal_source.subject
                or result.outcome != "NOT_EXECUTED"
            ):
                raise ValueError("cancelled prior-turn terminal/result join differs")
            results.append(
                {
                    "original_call_id": call.original_call_id,
                    "terminal": terminal.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                }
            )
            continue
        terminal_sources = [
            source
            for source in cut.complete_sources
            if source.schema_id == "chiplog.execution.accepted-direct-terminal-disposition.v1"
        ]
        matched = []
        for terminal_source in terminal_sources:
            terminal = AcceptedDirectTerminalDispositionV1.model_validate_json(
                terminal_source.canonical_record_bytes
            )
            if terminal.original_call_id == call.original_call_id:
                matched.append((terminal_source, terminal))
        if len(matched) != 1:
            raise ValueError("prior-turn call lacks one selected direct-terminal disposition")
        terminal_source, terminal = matched[0]
        terminal_ref = terminal_source.subject
        result_source_candidates = [
            source
            for source in cut.complete_sources
            if source.schema_id == "chiplog.execution.accepted-direct-terminal-result.v1"
        ]
        result_matches = []
        for source in result_source_candidates:
            result = AcceptedDirectTerminalResultV1.model_validate_json(
                source.canonical_record_bytes
            )
            if result.original_call_id == call.original_call_id:
                result_matches.append((source, result))
        if len(result_matches) != 1:
            raise ValueError("prior-turn call lacks one selected direct-terminal result")
        result_source, result = result_matches[0]
        if terminal.response_id != response.selected_seal.subject_id:
            raise ValueError("prior-turn disposition names another response")
        if result.terminal != terminal_ref:
            raise ValueError("prior-turn result names another disposition")
        selected_record(
            "chiplog.execution.accepted-direct-terminal-disposition.v1",
            terminal_ref,
            AcceptedDirectTerminalDispositionV1,
        )
        selected_record(
            "chiplog.execution.accepted-direct-terminal-result.v1",
            result_source.subject,
            AcceptedDirectTerminalResultV1,
        )
        results.append(
            {
                "original_call_id": call.original_call_id,
                "terminal": terminal.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
        )
    return [
        {
            "turn_id": before.turns[0].turn_id,
            "response_base64": attempt.response_base64,
            "rejection": attempt.rejection,
            "visibility": attempt.manifest.digest(),
            "results": results,
        }
    ]


def _decision_ref(identity: str, decision_id: str, raw: bytes) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=identity,
        revision=Present(head=decision_id, fingerprint=hashlib.sha256(raw).hexdigest()),
    )


def _validate_eligible_accounting_candidates(
    runtime: CommonCliExecutionRuntime,
    selected: Sequence[tuple[str, bytes, dict[str, object], PhysicalPublicationCommand]],
    *,
    named_decision_id: str,
    through_sequence: int,
    authenticated_loop_decisions=None,
    verified_h1_prepares=None,
) -> RetainedRecoveryPublication:
    named_evidence: RetainedRecoveryPublication | None = None
    named_validated = False
    for decision_id, _, candidate, candidate_batch in selected:
        if int(candidate.get("expected_head", -1)) + 1 <= through_sequence:
            evidence = validate_accounting_selected(  # type: ignore[no-untyped-call]
                runtime,
                candidate,
                candidate_batch,
                through_sequence=through_sequence,
                authenticated_loop_decisions=authenticated_loop_decisions,
                verified_h1_prepares=verified_h1_prepares,
            )
            if decision_id == named_decision_id:
                named_evidence = evidence
                named_validated = True
    if not named_validated or named_evidence is None:
        raise ValueError("next Turn named readiness was not validated")
    return named_evidence


def validate_selected_next_turn(
    runtime: CommonCliExecutionRuntime,
    entry: dict[str, object],
    physical_command: PhysicalPublicationCommand,
    *,
    authenticated_loop_decisions=None,
    verified_h1_prepares=None,
) -> RetainedNextTurnPublication:
    """Authenticate C and the exact selected/physical C→C' bridge before replay."""
    evidence = retained_next_turn(entry)
    receipt = evidence.actor_budget_v1
    trust_entries = runtime._trust._journal.entries()
    matches = [
        (index, raw)
        for index, (identity, _, raw) in enumerate(trust_entries)
        if identity == receipt.trust_journal_head
    ]
    if len(matches) != 1:
        raise ValueError("selected next Turn actor trust head absent or rival")
    from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
        HermeticTrustObservationV1,
    )

    position, trust_raw = matches[0]
    logical = runtime._trust.owner_snapshot_entries()
    prefix = runtime._trust.historical_prefix(
        HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=receipt.trust_journal_head,
                fingerprint=hashlib.sha256(trust_raw).hexdigest(),
            ),
            logical_snapshot_head=logical[position][0],
        )
    )
    if prefix.snapshot_bytes != receipt.trust_snapshot_bytes:
        raise ValueError("selected next Turn actor original trust prefix differs")
    batch = next_turn_command(evidence)
    request = evidence.request
    publication = request.selected_publication
    predecessor = entry.get("predecessor")
    if (
        publication is None
        or predecessor != request.cut.materialization_commitment
        or batch
        != replace(
            physical_command,
            expected_preimage_commitment=request.cut.materialization_commitment,
        )
    ):
        raise ValueError("next Turn selected command or predecessor differs")
    if (
        request.cut.database_id != runtime._database_identity[0]
        or request.cut.tenant_id != runtime._tenant_id
    ):
        raise ValueError("next Turn foreign database or tenant")
    selected = []
    if authenticated_loop_decisions is None:
        loop_rows = None
        current_entries = runtime._loop_decisions().entries()
    else:
        from chiplog.composition.r14_native_recovery_historical import (
            _authenticated_loop_cut_matches,
            _thaw_json,
        )

        if not _authenticated_loop_cut_matches(runtime, authenticated_loop_decisions):
            raise ValueError("next Turn authenticated loop cut is stale")
        loop_rows = authenticated_loop_decisions.decisions
        current_entries = None
    rows = (
        ((decision_id, raw, json.loads(raw), None) for decision_id, _, raw in current_entries)
        if loop_rows is None
        else (
            (
                row.decision_id,
                row.raw,
                _thaw_json(row.entry),
                row.publication,
            )
            for row in loop_rows
        )
    )
    for decision_id, raw, candidate, command in rows:
        if candidate.get("kind") != "DECIDED":
            continue
        if command is None:
            command = runtime._publication(candidate)
        if command.operation_kind == ACCOUNTING_OPERATION:
            selected.append((str(decision_id), raw, candidate, command))
    named = [
        item
        for item in selected
        if item[2].get("operation_id") == publication.selected_decision.subject_id
    ]
    if len(named) != 1:
        raise ValueError("next Turn named readiness decision is absent or ambiguous")
    decision_id, raw, prior, prior_batch = named[0]
    if (
        publication.selected_decision.revision.head != decision_id
        or publication.selected_decision.revision.fingerprint != hashlib.sha256(raw).hexdigest()
        or int(prior.get("expected_head", -1)) + 1 != request.cut.tenant_commit_sequence
        or prior.get("resulting") != request.cut.materialization_commitment
    ):
        raise ValueError("next Turn names a readiness pair for another source cut")
    original = _validate_eligible_accounting_candidates(
        runtime,
        selected,
        named_decision_id=decision_id,
        through_sequence=request.cut.tenant_commit_sequence,
        authenticated_loop_decisions=authenticated_loop_decisions,
        verified_h1_prepares=verified_h1_prepares,
    )
    pair = accounting_command(original)
    expected_decision = _decision_ref(pair.idempotency_key, decision_id, raw)
    old = original.accounting_request.cut
    accounting = original.continuation_request.complete_accounting[0]
    readiness = original.continuation_result.complete_continuations[0]
    if (
        prior_batch != pair
        or reconstruct_prior_continuation_cut(request) != old
        or publication.source_cut not in (None, old)
        or request.continuation != readiness
        or publication.selected_decision != expected_decision
        or publication.result_stamp.selected_decision != expected_decision
        or publication.result_stamp.predecessor_tenant_commit_sequence != old.tenant_commit_sequence
        or publication.result_stamp.predecessor_materialization_commitment
        != old.materialization_commitment
        or publication.result_stamp.resulting_tenant_commit_sequence != pair.expected_head + 1
        or publication.result_stamp.resulting_materialization_commitment != prior.get("resulting")
        or request.cut.tenant_commit_sequence != pair.expected_head + 1
        or request.cut.materialization_commitment != prior.get("resulting")
        or tuple(row.schema_id for row in pair.records) != (ACCOUNTING_SCHEMA, CONTINUATION_SCHEMA)
    ):
        raise ValueError("next Turn selected historical C or atomic result differs")
    for source, member, record in zip(
        (publication.accounting_source, publication.readiness_source),
        pair.records,
        (accounting, readiness),
        strict=True,
    ):
        if (
            source.canonical_record_bytes != member.canonical_bytes
            or source.canonical_record_bytes != record.canonical_bytes()
            or source.physical_record.subject_id != member.record_id
            or source.physical_record.revision.fingerprint != member.fingerprint
            or source.selected_decision != expected_decision
        ):
            raise ValueError("next Turn selected/physical pair differs")
    # C was independently authenticated by validate_accounting_selected. Exact
    # two-member C→C' structural delta is checked by the owner verifier. Here
    # we independently check the registered native cut fingerprint domains.
    frontier = request.cut.frontier.model_dump(mode="json")
    actual_frontier = frontier.pop("fingerprint")
    if actual_frontier != native_recovery_fingerprint("frontier", frontier):
        raise ValueError("next Turn current frontier fingerprint differs")
    cut = request.cut.model_dump(mode="json")
    actual_inventory = cut.pop("complete_inventory_fingerprint")
    if actual_inventory != native_recovery_fingerprint("cut", cut):
        raise ValueError("next Turn current inventory fingerprint differs")
    return evidence
