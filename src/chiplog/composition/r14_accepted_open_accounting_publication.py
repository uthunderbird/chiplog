"""Durable effects-specific accounting for one selected R16 OPEN obligation."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from typing import Literal

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


def _accepted_open_sealed_accounting(facts, cut, result):
    """Build the exact one-call RECOVERY_REQUIRED frontier from selected records."""
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
    accepted = facts.accepted.acceptance
    manifest = tuple(
        member.revision
        for member in accepted.loop_proposal.complete_acceptance_manifest
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


def _selected(runtime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [
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
    selected_only: bool = False,
    expected_source_cut: AcceptedOpenRecoveryCutV1 | None = None,
    source_materialization_commitment: str | None = None,
    source_tenant_commit_sequence: int | None = None,
):
    history = read_execution_recovery_history(
        runtime,
        selected_only=selected_only,
        exact_historical_sources=expected_source_cut is not None,
    )
    snapshot, _, _, executions, _, _ = history
    if len(executions) != 1 or not snapshot.records:
        raise LoopRejected("accepted/open source requires one selected native execution")
    seal = executions[0].proposal.fan_out.response_seal
    seal_digest = seal.digest()
    seal_ref = CallSubjectHead(
        subject_id=seal.response_seal_id,
        revision=Present(head="record:" + seal_digest, fingerprint=seal_digest),
    )
    projection = R14OriginalNativePrepareSource(runtime).collect(seal_ref)
    if projection is None:
        raise LoopRejected("accepted/open original Prepare source unavailable")
    source = R14NativeSelectedAccountingSource(
        runtime,
        projection,
        bind_native_execution_preparation_v2(projection),
    )
    facts = source.collect_one_accepted_open_outcome(
        execution_run_head(snapshot.records[-1]),
        selected_only=selected_only,
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
    )
    if cut is None:
        return None
    return facts, cut


async def _finish(runtime, entry: dict[str, object]):
    from chiplog.composition.r14_loop_history import read_selected_accepted_open_accounting

    evidence = retained_accepted_open_accounting(entry)
    command = accepted_open_accounting_command(evidence)
    if runtime._publication(entry) != command:
        raise LoopRejected("selected accepted/open accounting differs from retained command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(
            command.idempotency_key, entry
        ),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    selected = read_selected_accepted_open_accounting(runtime)
    if selected != evidence.result.accounting:
        raise LoopRejected("selected accepted/open accounting was not observed")
    return selected


async def publish_accepted_open_accounting(runtime, peer: str = "hermetic-ingress"):
    """Select one account-only record for the exact current accepted/open obligation."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        selected = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_ACCOUNTING_OPERATION
        ]
        if len(selected) > 1:
            raise LoopRejected("ambiguous accepted/open accounting history")
        prior = selected[0] if selected else None
    if prior is not None:
        return await _finish(runtime, prior)

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        current = _current_cut(runtime)
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
                current_facts, current_cut = _current_cut(runtime)
                if (
                    current_facts != facts
                    or current_cut != cut
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_competing_direct(runtime, cut.original_call_id)
                ):
                    return "STALE"
            except (LoopRejected, ValueError, TypeError, RuntimeError):
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


def validate_selected_accepted_open_accounting(runtime) -> None:
    """Authenticate selected/open source bytes before startup can replay the writer."""
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    runtime._pending()
    selected: list[dict[str, object]] = []
    claimed = False
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
                record.schema_id
                == "chiplog.execution.accepted-open-accounting.v1"
                for record in command.records
            )
        )
        if not marker:
            continue
        claimed = True
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
        with read_connection(runtime._database) as connection:
            actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        if actual not in (entry.get("predecessor"), entry.get("resulting")) or anchored not in (
            entry.get("predecessor"),
            entry.get("resulting"),
        ):
            raise LoopRejected("accepted/open publication is not an exact predecessor/result")
        current = _current_cut(
            runtime,
            selected_only=True,
        )
        if current is None:
            raise LoopRejected("accepted/open current cut unavailable")
        facts, cut = current
        if facts.outcome.canonical_bytes() != evidence.request.cut.selected_outcome_bytes or (
            cut != evidence.request.cut
        ):
            raise LoopRejected("accepted/open retained cut differs from exact selected history")
        selected.append(entry)
    if claimed and len(selected) != 1:
        raise LoopRejected("ambiguous accepted/open accounting selection")


async def hold_if_accepted_open(runtime, peer: str) -> bool:
    """Durably account and HOLD before any successor-turn owner preparation."""
    current = _current_cut(runtime)
    if current is None:
        return False
    await publish_accepted_open_recovery(runtime, peer)
    return True


def _selected_recovery(runtime, identity: str) -> dict[str, object] | None:
    runtime._pending()
    entries = [
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


async def _finish_recovery(runtime, entry: dict[str, object]):
    from chiplog.composition.r14_loop_history import read_selected_accepted_open_recovery

    evidence = retained_accepted_open_recovery(entry)
    command = accepted_open_recovery_command(evidence)
    if runtime._publication(entry) != command:
        raise LoopRejected("selected accepted/open recovery differs from retained command")
    await runtime._recover_exact(
        command,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(
            command.idempotency_key, entry
        ),
    )
    runtime._finish_decision(command.idempotency_key, expected=entry)
    if read_selected_accepted_open_recovery(runtime) != evidence.result:
        raise LoopRejected("selected accepted/open recovery was not observed")
    return evidence.result


async def publish_accepted_open_recovery(runtime, peer: str = "hermetic-ingress"):
    """Atomically publish OPEN accounting, terminal disposition, and loop obligation."""
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        selected = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        ]
        if len(selected) > 1:
            raise LoopRejected("ambiguous accepted/open recovery history")
        prior = selected[0] if selected else None
    if prior is not None:
        return await _finish_recovery(runtime, prior)

    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        current = _current_cut(runtime)
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
                current_facts, current_cut = _current_cut(runtime)
                if (
                    current_facts != facts
                    or current_cut != cut
                    or runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_competing_direct(runtime, cut.original_call_id)
                ):
                    return "STALE"
            except (LoopRejected, ValueError, TypeError, RuntimeError):
                return "STALE"
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            if _selected_competing_direct(runtime, cut.original_call_id):
                raise LoopRejected(
                    "accepted/open recovery conflicts with selected direct terminal"
                )
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


def validate_selected_accepted_open_recovery(runtime) -> None:
    """Recompute every selected recovery publication before startup replay."""
    claimed = False
    selected = []
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
        claimed = True
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
        with read_connection(runtime._database) as connection:
            actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        authenticated_later_suffix = False
        if actual != anchored:
            from chiplog.composition.r14_original_call_resolution_source import (
                authenticate_selected_resolution_prefix_held,
            )

            with runtime._authority_gate().hold():
                authenticated_later_suffix = authenticate_selected_resolution_prefix_held(
                    runtime, actual, anchored
                )
        if actual != anchored and not authenticated_later_suffix:
            raise LoopRejected("accepted/open current physical and anchored commitments differ")
        state, publication_actual, publication_anchor = runtime._selected_physical_state(command)
        if (
            publication_actual != actual
            or publication_anchor != anchored
            or not _accepted_open_publication_state_is_valid(
                state,
                actual,
                anchored,
                str(entry["predecessor"]),
                authenticated_later_suffix=authenticated_later_suffix,
            )
        ):
            raise LoopRejected("accepted/open exact publication is absent, partial, or stale")
        current = _current_cut(
            runtime,
            selected_only=True,
            expected_source_cut=evidence.request.cut,
            source_materialization_commitment=evidence.request.cut.materialization_commitment,
            source_tenant_commit_sequence=evidence.request.cut.tenant_commit_sequence,
        )
        if current is None:
            raise LoopRejected("accepted/open recovery selected source cut differs")
        if current[1] != evidence.request.cut:
            observed = current[1].model_dump(mode="json")
            retained = evidence.request.cut.model_dump(mode="json")
            differing = tuple(key for key in retained if retained[key] != observed.get(key))
            raise LoopRejected(
                "accepted/open recovery selected source cut differs: " + ",".join(differing)
            )
        _accepted_open_sealed_accounting(current[0], current[1], evidence.result)
        selected.append(entry)
    if claimed and len(selected) != 1:
        raise LoopRejected("ambiguous accepted/open recovery selection")


def read_selected_accepted_open_frontier(runtime):
    """Return selected recovery accounting, with an exact outcome when resolved."""
    from chiplog.composition.r14_loop_history import read_execution_recovery_history
    from chiplog.composition.r14_original_call_resolution_publication import (
        read_selected_original_call_resolution_source_held,
    )

    with runtime._authority_gate().hold():
        read_execution_recovery_history(runtime)
        validate_selected_accepted_open_recovery(runtime)
        entries = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (entry := json.loads(raw)).get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        ]
        if not entries:
            return None
        evidence = retained_accepted_open_recovery(entries[0])
        current = _current_cut(
            runtime,
            selected_only=True,
            expected_source_cut=evidence.request.cut,
            source_materialization_commitment=evidence.request.cut.materialization_commitment,
            source_tenant_commit_sequence=evidence.request.cut.tenant_commit_sequence,
        )
        if current is None:
            raise LoopRejected("selected accepted/open frontier source is unavailable")
        accounting = _accepted_open_sealed_accounting(
            current[0], evidence.request.cut, evidence.result
        )
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
