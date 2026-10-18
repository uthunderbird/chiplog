"""Durable effects-specific accounting for one selected R16 OPEN obligation."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    AcceptedOpenRecoveryCutV1,
    PrepareAcceptedOpenAccounting,
    PrepareAcceptedOpenRecoveryV1,
    PreparedAcceptedOpenAccounting,
    PreparedAcceptedOpenRecoveryV1,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedOriginalCallResolution,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_domain import SealedAccounting, sealed_call_accounting
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    RecoveredOutcomeSubject,
    RecoveryRequiredResult,
    TerminalCallFrontier,
)
from chiplog.composition.r14_accepted_open_accounting_records import (
    ACCEPTED_OPEN_ACCOUNTING_OPERATION,
    ACCEPTED_OPEN_RECOVERY_OPERATION,
    ACCEPTED_OPEN_RECOVERY_OWNER_OPERATION,
    RetainedAcceptedOpenAccountingPublication,
    RetainedAcceptedOpenRecoveryPublication,
    accepted_open_accounting_command,
    accepted_open_recovery_command,
    retained_accepted_open_accounting,
    retained_accepted_open_recovery,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_recovery_history
from chiplog.composition.r14_native_accepted_open_cut import build_accepted_open_recovery_cut
from chiplog.composition.r14_native_preparation_bindings import (
    bind_native_execution_preparation_v2,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
)
from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareSource
from chiplog.composition.r14_recovery_publication import (
    _exchange,
    issue_execution_recovery_actor_v1,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        AcceptedOpenAccountingRecordV1,
    )
    from chiplog.capabilities.agent_loop.execution_recovery_observations import (
        SelectedExecutionSuspension,
    )
    from chiplog.composition.r14_native_selected_accounting_source import (
        R14NativeAcceptedOpenOutcome,
        R14NativeOneCallAcceptedFacts,
    )
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def _accepted_open_call_frontier(
    accepted_facts: R14NativeOneCallAcceptedFacts,
    cut: AcceptedOpenRecoveryCutV1,
    result: PreparedAcceptedOpenRecoveryV1,
) -> TerminalCallFrontier:
    """Build one exact RECOVERY_REQUIRED frontier from selected OPEN records."""
    opening, terminal = result.opening, result.terminal
    opening_raw, terminal_raw = opening.canonical_bytes(), terminal.canonical_bytes()
    opening_digest = hashlib.sha256(opening_raw).hexdigest()
    terminal_digest = hashlib.sha256(terminal_raw).hexdigest()
    opening_ref = CallSubjectHead(
        subject_id=opening.opening_id,
        revision=Present(head="record:" + opening_digest, fingerprint=opening_digest),
    )
    terminal_ref = CallSubjectHead(
        subject_id=terminal.terminal_id,
        revision=Present(head="record:" + terminal_digest, fingerprint=terminal_digest),
    )
    accepted = accepted_facts.acceptance
    manifest = tuple(
        member.revision for member in accepted.loop_proposal.complete_acceptance_manifest
    )
    mismatches = {
        "opening_source_cut": opening.source_cut_fingerprint == cut.cut_fingerprint,
        "terminal_source_cut": terminal.source_cut_fingerprint == cut.cut_fingerprint,
        "original_call": opening.original.original_call_id == cut.original_call_id,
        "original_run": opening.original.original_run_id == cut.current_run.subject_id,
        "terminal_call": terminal.original_call_id == cut.original_call_id,
        "response": terminal.response_id == cut.response_id,
        "accepted": terminal.accepted == cut.accepted,
        "effect": terminal.external_effect_intent == cut.external_effect_intent,
        "opening_ref": terminal.obligation_opening == opening_ref,
        "manifest_len": len(manifest) == 3,
        "manifest": manifest
        == (
            cut.accepted.revision,
            cut.execution_intent.revision,
            cut.external_effect_intent.revision,
        ),
        "evidence_stream": (
            opening.original.evidence_stream_id == cut.external_effect_intent.subject_id
        ),
    }
    if not all(mismatches.values()):
        raise LoopRejected(
            "accepted/open terminal or obligation differs from exact source cut: "
            + ",".join(name for name, matches in mismatches.items() if not matches)
        )
    call = TerminalCallFrontier(
        original_call_id=cut.original_call_id,
        response_id=cut.response_id,
        acceptance=ConsequentialAcceptedCall(
            initialized=cut.initialized.revision,
            accepted=cut.accepted.revision,
            execution_intent=cut.execution_intent.revision,
            external_effect_intent=cut.external_effect_intent.revision,
            complete_acceptance_manifest=manifest,
        ),
        disposition=RecoveryRequiredResult(
            terminal=terminal_ref.revision,
            obligation=opening.original,
            result=Absent(),
            recovered_outcome=RecoveredOutcomeSubject(
                original_call_id=cut.original_call_id,
                recovery_obligation_id=opening.original.obligation_id,
                outcome=Absent(),
            ),
        ),
    )
    return call


def _accepted_open_sealed_accounting(
    facts: R14NativeAcceptedOpenOutcome,
    cut: AcceptedOpenRecoveryCutV1,
    result: PreparedAcceptedOpenRecoveryV1,
) -> SealedAccounting:
    """Build the legacy whole-seal accounting projection for one call."""
    call = _accepted_open_call_frontier(facts.accepted, cut, result)
    seal = facts.accepted.selected_seal
    ordered_call_ids = tuple(item.original_call_id for item in seal.initialized_records)
    return sealed_call_accounting(
        cut.response_id,
        seal.response_seal.revision.fingerprint,
        ordered_call_ids,
        (call,),
    )


def _with_selected_recovered_outcome(
    accounting: SealedAccounting,
    opening: PreparedAcceptedOpenRecoveryV1,
    prepared: PreparedOriginalCallResolution | None,
) -> SealedAccounting:
    """Overlay only the exact selected resolver outcome on the recovery frontier."""
    if prepared is None:
        return accounting

    from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
        RecoveredOutcomeSubject,
        RecoveryRequiredResult,
    )

    basis, closure, outcome, batch = (
        prepared.basis,
        prepared.closure,
        prepared.recovered_outcome,
        prepared.batch,
    )

    def record_ref(record_id: str, record: RecoveryDTO) -> CallSubjectHead:
        digest = hashlib.sha256(record.canonical_bytes()).hexdigest()
        return CallSubjectHead(
            subject_id=record_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )

    basis_ref = record_ref(basis.basis_id, basis)
    closure_ref = record_ref(closure.closure_id, closure)
    outcome_ref = record_ref(outcome.outcome_id, outcome)
    selected_terminal_ref = record_ref(opening.terminal.terminal_id, opening.terminal)
    opening_ref = record_ref(opening.opening.opening_id, opening.opening)
    original = opening.opening.original
    if (
        basis.original != original
        or basis.original_terminal != selected_terminal_ref
        or closure.basis != basis_ref
        or closure.original != original
        or closure.prior_obligation != opening_ref
        or closure.accepted_witness != basis.selected_witness
        or outcome.basis != basis_ref
        or outcome.original != original
        or outcome.closure != closure_ref
        or outcome.accepted_witness != closure.accepted_witness
        or outcome.accepted_semantic_class != basis.accepted_semantic_class
        or batch.basis != basis_ref
        or batch.closure != closure_ref
        or batch.recovered_outcome != outcome_ref
        or outcome.result_schema != "chiplog.loop.recovered-effect-status.v1"
    ):
        raise LoopRejected("selected resolver outcome does not join exact accepted/open obligation")

    if len(accounting.calls) != 1:
        raise LoopRejected("resolved accepted/open accounting requires one sealed call")
    call = accounting.calls[0]
    if call.original_call_id != original.original_call_id or not isinstance(
        call.disposition, RecoveryRequiredResult
    ):
        raise LoopRejected("selected resolver outcome conflicts with accepted/open frontier")
    recovered = RecoveredOutcomeSubject(
        original_call_id=original.original_call_id,
        recovery_obligation_id=original.obligation_id,
        outcome=outcome_ref.revision,
    )
    disposition = call.disposition.model_copy(update={"recovered_outcome": recovered})
    return replace(accounting, calls=(call.model_copy(update={"disposition": disposition}),))


def _accepted_open_publication_state_is_valid(
    state: str,
    actual: str,
    anchored: str,
    predecessor: str,
    *,
    authenticated_later_suffix: bool = False,
) -> bool:
    """Allow exact pending recovery or complete history under a newer anchor."""
    if actual != anchored and not authenticated_later_suffix:
        return False
    if state == "COMPLETE":
        return True
    return state == "ABSENT" and actual == predecessor


def _selected(runtime: R14PlanningRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries: list[dict[str, object]] = [
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(entries) > 1:
        raise LoopRejected("ambiguous accepted/open accounting selection")
    if not entries:
        return None
    entry = entries[0]
    if entry.get("operation_kind") != ACCEPTED_OPEN_ACCOUNTING_OPERATION:
        raise LoopRejected("accepted/open accounting identity belongs to another operation")
    return entry


def _current_cut(
    runtime,
    *,
    original_call_id: str | None = None,
    selected_only: bool = False,
    expected_source_cut: AcceptedOpenRecoveryCutV1 | None = None,
    source_materialization_commitment: str | None = None,
    source_tenant_commit_sequence: int | None = None,
    authenticated_history=None,
):
    if authenticated_history is not None or (selected_only and expected_source_cut is not None):
        runtime._authority_gate().require_held()
    history = (
        authenticated_history
        if authenticated_history is not None
        else read_execution_recovery_history(
            runtime,
            selected_only=selected_only,
            exact_historical_sources=expected_source_cut is not None,
        )
    )
    snapshot, _, _, executions, _, acceptances = history
    if not snapshot.records:
        raise LoopRejected("accepted/open source requires selected execution history")
    if expected_source_cut is not None:
        if original_call_id not in (None, expected_source_cut.original_call_id):
            raise LoopRejected("accepted/open historical target differs")
        original_call_id = expected_source_cut.original_call_id
    if original_call_id is None:
        accepted_ids = {value.loop_request.binding.original_call_id for value in acceptances}
        if len(accepted_ids) != 1:
            return None
        original_call_id = next(iter(accepted_ids))
    matches = tuple(
        value
        for value in executions
        if any(
            record.original_call_id == original_call_id
            for record in value.proposal.fan_out.initialized_records
        )
    )
    if len(matches) != 1:
        raise LoopRejected("accepted/open requires one selected native execution for call")
    seal = matches[0].proposal.fan_out.response_seal
    seal_digest = seal.digest()
    seal_ref = CallSubjectHead(
        subject_id=seal.response_seal_id,
        revision=Present(head="record:" + seal_digest, fingerprint=seal_digest),
    )
    projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
    if projection is None or projection.selected_response_seal != seal_ref:
        raise LoopRejected("accepted/open original Prepare source unavailable")
    source = R14NativeSelectedAccountingSource(
        runtime,
        projection,
        bind_native_execution_preparation_v2(projection),
    )
    physical_history = None
    if selected_only and expected_source_cut is not None:
        runtime._authority_gate().require_held()
        pending = runtime._pending()
        if (
            len(pending) == 1
            and pending[0].get("operation_kind")
            in (
                ACCEPTED_OPEN_ACCOUNTING_OPERATION,
                ACCEPTED_OPEN_RECOVERY_OPERATION,
                "agent_loop.execution-accounting-readiness.v1",
            )
            and not runtime._pending_owners()
            and not runtime._pending_gate_publications()
        ):
            # These canonical OPEN/readiness batches add no Runs. Effects still
            # checks physical Run bytes; the source still verifies the suffix.
            physical_history = snapshot
        elif (
            len(pending) == 1
            and pending[0].get("operation_kind") == "agent_loop.execution-suspension.v1"
            and not runtime._pending_owners()
            and not runtime._pending_gate_publications()
        ):
            from chiplog.composition.r14_historical_suffix import (
                _materialized_native_open_run_history_held,
            )

            # The exact outer fold proved membership, including an ABSENT Run.
            # Keep its semantic snapshot for replay; effects needs physical Runs.
            with read_connection(runtime._database) as connection:
                physical_history = _materialized_native_open_run_history_held(
                    runtime, connection, snapshot=snapshot
                )
    facts = source.collect_one_accepted_open_outcome(
        execution_run_head(snapshot.records[-1])
        if expected_source_cut is None
        else expected_source_cut.current_run,
        original_call_id=original_call_id,
        selected_only=selected_only,
        authenticated_recovery_history=history if selected_only else None,
        authenticated_effects_history=physical_history,
        expected_outcome_record_id=(
            None if expected_source_cut is None else expected_source_cut.selected_outcome_record_id
        ),
        expected_selected_decision=(
            None if expected_source_cut is None else expected_source_cut.selected_decision
        ),
        expected_tenant_commit_sequence=(
            None if expected_source_cut is None else expected_source_cut.tenant_commit_sequence
        ),
    )
    if facts is None:
        return None
    cut = build_accepted_open_recovery_cut(
        runtime,
        facts,
        source_materialization_commitment=source_materialization_commitment,
        source_tenant_commit_sequence=source_tenant_commit_sequence,
        authenticated_history=(
            history if selected_only and expected_source_cut is not None else authenticated_history
        ),
    )
    if cut is None:
        return None
    return facts, cut


def _cut_is_preserved_by_selected_suspension(
    runtime,
    retained: AcceptedOpenRecoveryCutV1,
    current: AcceptedOpenRecoveryCutV1,
    original_obligation,
) -> bool:
    """Accept only the selected suspension that carries this exact open obligation."""
    retained_fields = retained.model_dump(mode="json")
    current_fields = current.model_dump(mode="json")
    suspension_fields = {"current_run", "current_run_bytes", "cut_fingerprint"}
    if any(
        retained_fields[key] != current_fields.get(key)
        for key in retained_fields
        if key not in suspension_fields
    ):
        return False
    from chiplog.composition.r14_execution_suspension import (
        read_selected_execution_suspension_held,
    )

    suspension: SelectedExecutionSuspension | None
    with runtime._authority_gate().hold():
        pending = runtime._pending()
        if (
            len(pending) == 1
            and pending[0].get("operation_kind") == "agent_loop.execution-suspension.v1"
            and not runtime._pending_owners()
            and not runtime._pending_gate_publications()
        ):
            from chiplog.composition.r14_execution_suspension import (
                _command,
                _entry_for,
                _retained,
                _validate_selected,
            )
            from chiplog.composition.r14_loop_history import _read_call_history

            _read_call_history(runtime, selected_only=True, exact_historical_sources=True)
            selected = [
                _entry_for(runtime, str(entry["operation_id"]))
                for _, _, raw in runtime._loop_decisions().entries()
                if (entry := json.loads(raw)).get("kind") == "DECIDED"
                and entry.get("operation_kind") == "agent_loop.execution-suspension.v1"
            ]
            if (
                len(selected) != 1
                or selected[0] is None
                or selected[0][2].get("operation_id") != pending[0].get("operation_id")
            ):
                return False
            entry = selected[0][2]
            _, suspension = _validate_selected(
                runtime,
                entry,
                _command(_retained(entry)),
                check_history=False,
                allow_pending_absent=True,
            )
        else:
            suspension = read_selected_execution_suspension_held(runtime)
    return bool(
        suspension is not None
        and suspension.pair.predecessor_run == retained.current_run
        and suspension.pair.suspended_run == current.current_run
        and original_obligation in suspension.baseline.original_obligations
    )


async def _finish(
    runtime: R14PlanningRuntime, entry: dict[str, object]
) -> AcceptedOpenAccountingRecordV1 | None:
    from chiplog.composition.r14_loop_history import read_selected_accepted_open_accounting

    evidence = retained_accepted_open_accounting(entry)
    command = accepted_open_accounting_command(evidence)
    if runtime._publication(entry) != command:
        raise LoopRejected("selected accepted/open accounting differs from retained command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    selected = read_selected_accepted_open_accounting(
        runtime, original_call_id=evidence.request.cut.original_call_id
    )
    if selected != evidence.result.accounting:
        raise LoopRejected("selected accepted/open accounting was not observed")
    return selected


def _selected_open_for_call(
    runtime: R14PlanningRuntime, operation: str, original_call_id: str | None
) -> list[dict[str, object]]:
    """Authenticate every marked OPEN envelope before choosing a replay target."""
    selected: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for _, _, raw in runtime._loop_decisions().entries():
        entry: dict[str, object] = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        recovery_marker = (
            command.operation_kind == ACCEPTED_OPEN_RECOVERY_OPERATION
            or "accepted_open_recovery_evidence" in entry
            or any(
                record.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
                for record in command.records
            )
        )
        accounting_marker = (
            command.operation_kind == ACCEPTED_OPEN_ACCOUNTING_OPERATION
            or "accepted_open_accounting_evidence" in entry
            or any(
                record.schema_id == "chiplog.execution.accepted-open-accounting.v1"
                for record in command.records
            )
        )
        if not recovery_marker and not accounting_marker:
            continue
        if (
            "accepted_open_recovery_evidence" in entry
            and "accepted_open_accounting_evidence" in entry
        ):
            raise LoopRejected("selected accepted/open envelope mixes publication families")
        evidence: (
            RetainedAcceptedOpenRecoveryPublication | RetainedAcceptedOpenAccountingPublication
        )
        if recovery_marker:
            evidence = retained_accepted_open_recovery(entry)
            expected = accepted_open_recovery_command(evidence)
            family = ACCEPTED_OPEN_RECOVERY_OPERATION
        else:
            evidence = retained_accepted_open_accounting(entry)
            expected = accepted_open_accounting_command(evidence)
            family = ACCEPTED_OPEN_ACCOUNTING_OPERATION
        cut = evidence.request.cut
        if (
            command != expected
            or command.operation_kind != family
            or entry.get("operation_kind") != family
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("expected_head") != command.expected_head
            or entry.get("fingerprint") != command.request_fingerprint
            or entry.get("predecessor") != cut.materialization_commitment
        ):
            raise LoopRejected("selected accepted/open publication envelope differs")
        key = (family, cut.response_id, cut.original_call_id)
        if key in seen:
            raise LoopRejected("duplicate accepted/open publication for selected call and seal")
        seen.add(key)
        if family == operation and original_call_id in (None, cut.original_call_id):
            selected.append(entry)
    return selected


async def publish_accepted_open_accounting(
    runtime, peer: str = "hermetic-ingress", *, original_call_id: str | None = None
):
    """Select one account-only record for the exact current accepted/open obligation."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        selected = _selected_open_for_call(
            runtime, ACCEPTED_OPEN_ACCOUNTING_OPERATION, original_call_id
        )
        if len(selected) > 1:
            raise LoopRejected("ambiguous accepted/open accounting history")
        prior = selected[0] if selected else None
    if prior is not None:
        return await _finish(runtime, prior)

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        current = _current_cut(runtime, original_call_id=original_call_id)
        if current is None:
            raise LoopRejected("exact selected accepted/open outcome unavailable; HOLD")
        facts, cut = current
        identity = "accepted-open-accounting:" + cut.cut_fingerprint
        if _selected(runtime, identity) is not None:
            raise LoopRejected("accepted/open selection changed during source read")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
        if predecessor != cut.materialization_commitment:
            raise LoopRejected("accepted/open cut predecessor differs")

    refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(observed, refreshed):
            raise LoopRejected("accepted/open authenticated actor changed before owner IPC")
        observed = refreshed

    request = PrepareAcceptedOpenAccounting(command_id=identity, cut=cut)
    result, owner_call, owner_reply = await _exchange(
        runtime,
        request,
        "agent_loop.prepare_accepted_open_accounting.v1",
        PreparedAcceptedOpenAccounting,
    )
    evidence = RetainedAcceptedOpenAccountingPublication(
        request=request,
        result=result,
        owner_call=owner_call,
        owner_reply=owner_reply,
        actor_budget_v1=actor_budget,
    )
    command = accepted_open_accounting_command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                current_facts, current_cut = _current_cut(
                    runtime, original_call_id=cut.original_call_id
                )
                if (
                    current_facts != facts
                    or current_cut != cut
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_competing_direct(runtime, cut.original_call_id)
                ):
                    return "STALE"
            except LoopRejected, ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            if _selected_competing_direct(runtime, cut.original_call_id):
                raise LoopRejected(
                    "accepted/open accounting conflicts with selected direct terminal"
                )
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": identity,
                    "operation_kind": ACCEPTED_OPEN_ACCOUNTING_OPERATION,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": record.record_id,
                            "owner": record.owner,
                            "schema": record.schema_id,
                            "payload": base64.b64encode(record.canonical_bytes).decode(),
                            "digest": record.fingerprint,
                        }
                        for record in command.records
                    ],
                    "accepted_open_accounting_evidence": evidence.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("accepted/open accounting publication " + result.disposition)
    with runtime._authority_gate().hold():
        entry = _selected(runtime, identity)
        if entry is None or retained_accepted_open_accounting(entry) != evidence:
            raise LoopRejected("selected accepted/open accounting evidence differs")
    return await _finish(runtime, entry)


def _authenticate_selected_open_source(
    runtime, entry, command, retained_cut, *, authenticated_history=None
):
    """Bind a historical OPEN source to the fully authenticated current physical cut."""
    with runtime._authority_gate().hold():
        with read_connection(runtime._database) as connection:
            actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        state, publication_actual, publication_anchor = runtime._selected_physical_state(command)
        if (
            publication_actual != actual
            or publication_anchor != anchored
            or state not in ("ABSENT", "COMPLETE")
            or (state == "ABSENT" and actual != anchored)
            or (state == "ABSENT" and actual != entry.get("predecessor"))
        ):
            raise LoopRejected("accepted/open exact publication is absent, partial, or stale")
        current = _current_cut(
            runtime,
            selected_only=True,
            expected_source_cut=retained_cut,
            source_materialization_commitment=retained_cut.materialization_commitment,
            source_tenant_commit_sequence=retained_cut.tenant_commit_sequence,
            authenticated_history=authenticated_history,
        )
        with read_connection(runtime._database) as connection:
            final_actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        if (
            current is None
            or current[0].materialization_commitment != actual
            or final_actual != actual
            or runtime._commitment_journal.load(runtime._tenant_id) != anchored
        ):
            raise LoopRejected("accepted/open selected source does not authenticate current cut")
        return current


def validate_selected_accepted_open_accounting(runtime) -> None:
    """Authenticate selected/open source bytes before startup can replay the writer."""
    runtime._pending()
    seen_call_keys = set()
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        if (
            command.operation_kind == ACCEPTED_OPEN_RECOVERY_OPERATION
            or "accepted_open_recovery_evidence" in entry
        ):
            continue
        marker = (
            command.operation_kind == ACCEPTED_OPEN_ACCOUNTING_OPERATION
            or "accepted_open_accounting_evidence" in entry
            or any(
                record.schema_id == "chiplog.execution.accepted-open-accounting.v1"
                for record in command.records
            )
        )
        if not marker:
            continue
        evidence = retained_accepted_open_accounting(entry)
        expected = accepted_open_accounting_command(evidence)
        if (
            command != expected
            or entry.get("operation_kind") != ACCEPTED_OPEN_ACCOUNTING_OPERATION
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("predecessor") != evidence.request.cut.materialization_commitment
            or entry.get("expected_head") != command.expected_head
            or entry.get("fingerprint") != command.request_fingerprint
            or len(command.records) != 1
        ):
            raise LoopRejected("selected accepted/open accounting envelope differs")
        key = (evidence.request.cut.response_id, evidence.request.cut.original_call_id)
        if key in seen_call_keys:
            raise LoopRejected("duplicate accepted/open accounting for selected call and seal")
        seen_call_keys.add(key)
        current = _authenticate_selected_open_source(runtime, entry, command, evidence.request.cut)
        facts, cut = current
        if facts.outcome.canonical_bytes() != evidence.request.cut.selected_outcome_bytes or (
            cut != evidence.request.cut
        ):
            raise LoopRejected("accepted/open retained cut differs from exact selected history")


async def hold_if_accepted_open(runtime, peer: str) -> bool:
    """Only current consumable reduction can clear an accepted/open Turn hold."""
    current = _current_cut(runtime)
    if current is None:
        return _hold_if_resolved_reduction_stale(runtime)
    facts, _ = current
    await publish_accepted_open_recovery(runtime, peer)
    from chiplog.composition.r14_original_call_resolution_publication import (
        read_selected_original_call_resolution_source_held,
    )

    with runtime._authority_gate().hold():
        resolution = read_selected_original_call_resolution_source_held(runtime)
    if resolution is None:
        return True
    reduction = runtime.read_selected_loop_semantic_reduction()
    if reduction is None:
        return True
    return not (
        _reduction_matches_resolution(resolution, reduction)
        and _reduction_matches_current_evidence(reduction, facts.complete_ordered_evidence_sources)
    )


def _hold_if_resolved_reduction_stale(runtime) -> bool:
    """Keep resolved R14 obligations held until their reduction matches current R16."""
    from chiplog.composition import (
        r14_loop_semantic_reduction_publication as reduction_publication,
    )
    from chiplog.composition import (
        r14_original_call_resolution_publication as resolution_publication,
    )
    from chiplog.composition.r14_original_call_resolution_source import (
        capture_original_call_resolution_request,
    )

    selected_resolution = resolution_publication._selected_entries(runtime)
    if not selected_resolution:
        return False
    if len(selected_resolution) != 1:
        return True
    try:
        retained_resolution = resolution_publication.validate_selected_command(
            runtime, selected_resolution[0][2], selected_resolution[0][3]
        )
        current_request = capture_original_call_resolution_request(
            runtime,
            allow_selected_resolution=True,
            expected_cut=retained_resolution.request.cut,
        )
        resolution = resolution_publication.read_selected_original_call_resolution_source_held(
            runtime
        )
        reduction = runtime.read_selected_loop_semantic_reduction()
        selected_reductions = reduction_publication._selected_entries(runtime)
        if reduction is None or not selected_reductions:
            return True
        retained_reduction = reduction_publication.validate_selected_command(
            runtime, selected_reductions[-1][2], selected_reductions[-1][3]
        )
        return not (
            resolution is not None
            and _reduction_matches_resolution(resolution, reduction)
            and _reduction_matches_current_evidence(
                reduction,
                current_request.cut.complete_ordered_evidence,
                current_witness=current_request.cut.selected_witness,
                reduction_witness=retained_reduction.request.complete_anchor_sources[-1],
            )
        )
    except LoopRejected, ValueError, TypeError, RuntimeError, OSError:
        return True


def _reduction_matches_current_evidence(
    reduction,
    evidence_sources,
    *,
    current_witness=None,
    reduction_witness=None,
) -> bool:
    """A consumable reduction must cover the complete current authenticated R16 cut."""
    try:
        current = tuple(source.physical_record for source in evidence_sources)
    except AttributeError, TypeError:
        return False
    if not current or reduction.record.complete_ordered_evidence != current:
        return False
    if current_witness is not None or reduction_witness is not None:
        return current_witness is not None and current_witness == reduction_witness
    return True


def _reduction_matches_resolution(resolution, reduction) -> bool:
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        ConsumableReduction,
        ResolvedReductionAnchor,
    )

    def reference(record_id: str, record: RecoveryDTO) -> CallSubjectHead:
        fingerprint = hashlib.sha256(record.canonical_bytes()).hexdigest()
        return CallSubjectHead(
            subject_id=record_id,
            revision=Present(head="record:" + fingerprint, fingerprint=fingerprint),
        )

    record = reduction.record
    anchor = record.anchor
    return (
        isinstance(record.disposition, ConsumableReduction)
        and isinstance(anchor, ResolvedReductionAnchor)
        and record.stream.stream_id == resolution.basis.original.evidence_stream_id
        and record.reduction_id == resolution.basis.reduction_id
        and record.reducer_id == resolution.basis.original.reducer_id
        and record.reducer_version == resolution.basis.original.reducer_version
        and record.disposition.semantic_class == resolution.basis.accepted_semantic_class
        and anchor.original == resolution.basis.original
        and anchor.accepted_witness == resolution.basis.selected_witness
        and anchor.closure == reference(resolution.closure.closure_id, resolution.closure)
        and anchor.recovered_outcome
        == reference(resolution.recovered_outcome.outcome_id, resolution.recovered_outcome)
        and anchor.resolver_batch == reference(resolution.batch.batch_id, resolution.batch)
    )


def _selected_recovery(runtime: R14PlanningRuntime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries: list[dict[str, object]] = [
        json.loads(raw)
        for _, _, raw in runtime._loop_decisions().entries()
        if (entry := json.loads(raw)).get("kind") == "DECIDED"
        and entry.get("operation_id") == identity
    ]
    if len(entries) > 1:
        raise LoopRejected("ambiguous accepted/open recovery selection")
    if not entries:
        return None
    entry = entries[0]
    if entry.get("operation_kind") != ACCEPTED_OPEN_RECOVERY_OPERATION:
        raise LoopRejected("accepted/open recovery identity belongs to another operation")
    return entry


async def _finish_recovery(
    runtime: R14PlanningRuntime, entry: dict[str, object]
) -> PreparedAcceptedOpenRecoveryV1:
    from chiplog.composition.r14_loop_history import read_selected_accepted_open_recovery

    evidence = retained_accepted_open_recovery(entry)
    command = accepted_open_recovery_command(evidence)
    if runtime._publication(entry) != command:
        raise LoopRejected("selected accepted/open recovery differs from retained command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(command.idempotency_key, entry),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    if (
        read_selected_accepted_open_recovery(
            runtime, original_call_id=evidence.request.cut.original_call_id
        )
        != evidence.result
    ):
        raise LoopRejected("selected accepted/open recovery was not observed")
    return evidence.result


async def publish_accepted_open_recovery(
    runtime, peer: str = "hermetic-ingress", *, original_call_id: str | None = None
):
    """Atomically publish OPEN accounting, terminal disposition, and loop obligation."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        selected = _selected_open_for_call(
            runtime, ACCEPTED_OPEN_RECOVERY_OPERATION, original_call_id
        )
        if len(selected) > 1:
            raise LoopRejected("ambiguous accepted/open recovery history")
        prior = selected[0] if selected else None
    if prior is not None:
        return await _finish_recovery(runtime, prior)

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        current = _current_cut(runtime, original_call_id=original_call_id)
        if current is None:
            raise LoopRejected("exact selected accepted/open outcome unavailable; HOLD")
        facts, cut = current
        identity = "accepted-open-recovery:" + cut.cut_fingerprint
        if _selected_recovery(runtime, identity) is not None:
            raise LoopRejected("accepted/open recovery selection changed during source read")
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
        if predecessor != cut.materialization_commitment:
            raise LoopRejected("accepted/open recovery cut predecessor differs")

    refreshed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        if not runtime._same_execution_actor_identity(observed, refreshed):
            raise LoopRejected("accepted/open recovery actor changed before owner IPC")
        observed = refreshed

    request = PrepareAcceptedOpenRecoveryV1(command_id=identity, cut=cut)
    result, owner_call, owner_reply = await _exchange(
        runtime,
        request,
        ACCEPTED_OPEN_RECOVERY_OWNER_OPERATION,
        PreparedAcceptedOpenRecoveryV1,
    )
    evidence = RetainedAcceptedOpenRecoveryPublication(
        request=request,
        result=result,
        owner_call=owner_call,
        owner_reply=owner_reply,
        actor_budget_v1=actor_budget,
    )
    command = accepted_open_recovery_command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                current_facts, current_cut = _current_cut(
                    runtime, original_call_id=cut.original_call_id
                )
                if (
                    current_facts != facts
                    or current_cut != cut
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_competing_direct(runtime, cut.original_call_id)
                ):
                    return "STALE"
            except LoopRejected, ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            if _selected_competing_direct(runtime, cut.original_call_id):
                raise LoopRejected("accepted/open recovery conflicts with selected direct terminal")
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": identity,
                    "operation_kind": ACCEPTED_OPEN_RECOVERY_OPERATION,
                    "expected_head": command.expected_head,
                    "fingerprint": command.request_fingerprint,
                    "predecessor": predecessor,
                    "resulting": resulting,
                    "records": [
                        {
                            "record_id": record.record_id,
                            "owner": record.owner,
                            "schema": record.schema_id,
                            "payload": base64.b64encode(record.canonical_bytes).decode(),
                            "digest": record.fingerprint,
                        }
                        for record in command.records
                    ],
                    "accepted_open_recovery_evidence": evidence.canonical_bytes().decode(),
                }
            )

    submitted = await runtime._appender.submit(
        replace(command, admission_guard=guard, decision_guard=decide)
    )
    if submitted.disposition not in ("COMMITTED", "EXACT_REPLAY"):
        raise LoopRejected("accepted/open recovery publication " + submitted.disposition)
    entry = _selected_recovery(runtime, identity)
    if entry is None:
        raise LoopRejected("accepted/open recovery selected decision is absent")
    return await _finish_recovery(runtime, entry)


def _validated_selected_accepted_open_recoveries(runtime, *, authenticated_history=None):
    """Validate each selected recovery publication and retain its exact frontier."""
    selected = []
    seen_call_keys = set()
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        command = runtime._publication(entry)
        marker = (
            command.operation_kind == ACCEPTED_OPEN_RECOVERY_OPERATION
            or "accepted_open_recovery_evidence" in entry
            or any(
                record.schema_id == "chiplog.execution.accepted-open-terminal-disposition.v1"
                for record in command.records
            )
        )
        if not marker:
            continue
        evidence = retained_accepted_open_recovery(entry)
        expected = accepted_open_recovery_command(evidence)
        if (
            command != expected
            or entry.get("operation_kind") != ACCEPTED_OPEN_RECOVERY_OPERATION
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("predecessor") != evidence.request.cut.materialization_commitment
            or entry.get("expected_head") != command.expected_head
            or entry.get("fingerprint") != command.request_fingerprint
            or len(command.records) != 3
        ):
            raise LoopRejected("selected accepted/open recovery envelope differs")
        key = (evidence.request.cut.response_id, evidence.request.cut.original_call_id)
        if key in seen_call_keys:
            raise LoopRejected("duplicate accepted/open recovery for selected call and seal")
        seen_call_keys.add(key)
        current = _authenticate_selected_open_source(
            runtime,
            entry,
            command,
            evidence.request.cut,
            authenticated_history=authenticated_history,
        )
        accounting_cut = current[1]
        if current[1] != evidence.request.cut:
            if not _cut_is_preserved_by_selected_suspension(
                runtime,
                evidence.request.cut,
                current[1],
                evidence.result.opening.original,
            ):
                observed = current[1].model_dump(mode="json")
                retained = evidence.request.cut.model_dump(mode="json")
                differing = tuple(key for key in retained if retained[key] != observed.get(key))
                raise LoopRejected(
                    "accepted/open recovery selected source cut differs: " + ",".join(differing)
                )
            accounting_cut = evidence.request.cut
        frontier = _accepted_open_call_frontier(
            current[0].accepted, accounting_cut, evidence.result
        )
        selected.append((entry, evidence, frontier, current[0]))
    return tuple(selected)


def _validated_selected_accepted_open_recovery(runtime, *, authenticated_history=None):
    """Legacy whole-seal accessor remains singleton-only."""
    selected = _validated_selected_accepted_open_recoveries(
        runtime, authenticated_history=authenticated_history
    )
    if len(selected) > 1:
        raise LoopRejected("ambiguous accepted/open recovery selection")
    if not selected:
        return None
    entry, evidence, _frontier, facts = selected[0]
    accounting = _accepted_open_sealed_accounting(facts, evidence.request.cut, evidence.result)
    return entry, evidence, accounting


def validate_selected_accepted_open_recovery(runtime) -> None:
    """Recompute every selected recovery publication before startup replay."""
    _validated_selected_accepted_open_recoveries(runtime)


def read_selected_accepted_open_frontier(runtime, *, _authenticated_history=None):
    """Return selected recovery accounting, with an exact outcome when resolved."""
    from chiplog.composition.r14_original_call_resolution_publication import (
        read_selected_original_call_resolution_source_held,
    )

    with runtime._authority_gate().hold():
        runtime._require_no_pending()
        validated = _validated_selected_accepted_open_recovery(
            runtime, authenticated_history=_authenticated_history
        )
        if validated is None:
            # With no selected recovery record, retain the original reader's
            # full selected execution-history validation before returning None.
            read_execution_recovery_history(runtime)
            return None
        _, evidence, accounting = validated
        resolution = read_selected_original_call_resolution_source_held(runtime)
        if resolution is None:
            return accounting
        return _with_selected_recovered_outcome(
            accounting,
            evidence.result,
            resolution,
        )


def _selected_competing_direct(runtime, original_call_id: str) -> bool:
    from chiplog.composition.r14_accepted_direct_terminal_publication import (
        selected_direct_terminal_for_call,
    )

    return selected_direct_terminal_for_call(runtime, original_call_id)


__all__ = ["publish_accepted_open_accounting", "publish_accepted_open_recovery"]
