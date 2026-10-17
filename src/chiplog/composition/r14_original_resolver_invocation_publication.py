"""Selected physical record for a fresh R14 original-resolver invocation."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    OriginalResolverInvocationV1,
    PreparedOriginalResolverInvocationV1,
    PrepareOriginalResolverInvocationV1,
)
from chiplog.capabilities.agent_loop.original_resolver_invocation_preparation import (
    prepare_original_resolver_invocation_v1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_result,
)
from chiplog.composition.r14_loop_history import read_selected_original_obligation_sources
from chiplog.composition.r14_original_resolver_policy_publication import (
    _validate_actor_history,
)
from chiplog.composition.r14_original_resolver_policy_publication import (
    validate_selected as validate_policy_selected,
)
from chiplog.composition.r14_recovery_publication import (
    RecoveryActorBudgetV1,
    issue_execution_recovery_actor_v1,
    validate_recovery_actor_budget,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.composition.r14_execution_runtime import R14ExecutionRuntime
    from chiplog.composition.r14_runtime import R14PlanningRuntime

OPERATION = "agent_loop.original-resolver-invocation.v1"
OWNER_OPERATION = "agent_loop.prepare_original_resolver_invocation.v1"
SCHEMA = "chiplog.loop.original-resolver-invocation.v1"
EVIDENCE_FIELD = "original_resolver_invocation_evidence"


class RetainedOriginalResolverInvocationPublication(RecoveryDTO):
    tenant_id: str
    database_id: str
    request: PrepareOriginalResolverInvocationV1
    prepared: PreparedOriginalResolverInvocationV1
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1
    expected_head: int
    predecessor: str


def _validate_invocation_actor(
    runtime: R14PlanningRuntime, evidence: RetainedOriginalResolverInvocationPublication
) -> None:
    receipt = evidence.actor_budget_v1
    _validate_actor_history(runtime, receipt)
    result = runtime._decode_trust_result(receipt.response.canonical_payload)
    identity = json.loads(result.reference_bytes or b"{}")
    if (
        receipt.trust_journal_head != evidence.prepared.invocation.trust_journal_head
        or hashlib.sha256(receipt.response.canonical_payload).hexdigest()
        != evidence.prepared.invocation.trust_response_fingerprint
        or identity.get("tenant_id") != evidence.tenant_id
        or identity.get("principal_id") != "hermetic-principal"
    ):
        raise ValueError("selected resolver invocation differs from its authenticated actor")


def command(evidence: RetainedOriginalResolverInvocationPublication) -> PhysicalPublicationCommand:
    request = PrepareOriginalResolverInvocationV1.model_validate_json(
        evidence.request.canonical_bytes()
    )
    prepared = prepare_original_resolver_invocation_v1(request)
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    _validate_invocation_actor_fields(evidence.actor_budget_v1, request.invocation)
    reply = decode_original_recovery_result(
        ORIGINAL_RECOVERY_RESULT_SCHEMA, evidence.owner_reply.canonical_payload
    ).value
    if (
        request != evidence.request
        or prepared != evidence.prepared
        or not isinstance(prepared, PreparedOriginalResolverInvocationV1)
        or reply != evidence.prepared
        or evidence.owner_call.operation_id != OWNER_OPERATION
        or evidence.owner_call.schema_id != ORIGINAL_RECOVERY_REQUEST_SCHEMA
        or evidence.owner_call.canonical_payload != request.canonical_bytes()
        or evidence.owner_reply.request_id != evidence.owner_call.request_id
        or evidence.owner_reply.responder != evidence.owner_call.callee
        or evidence.owner_reply.schema_id != ORIGINAL_RECOVERY_RESULT_SCHEMA
        or evidence.owner_reply.canonical_payload != evidence.prepared.canonical_bytes()
        or evidence.owner_call.caller.owner_id != "broker"
        or evidence.owner_call.callee.owner_id != "agent_loop"
        or evidence.owner_call.caller.tenant_id != evidence.tenant_id
        or evidence.owner_call.callee.tenant_id != evidence.tenant_id
        or evidence.actor_budget_v1.request.caller.tenant_id != evidence.tenant_id
        or evidence.prepared.source_request_fingerprint != request.digest()
        or evidence.prepared.complete_batch_fingerprint
        != hashlib.sha256(evidence.prepared.invocation.canonical_bytes()).hexdigest()
    ):
        raise ValueError("original resolver invocation owner exchange or actor differs")
    invocation = evidence.prepared.invocation
    raw = invocation.canonical_bytes()
    return PhysicalPublicationCommand(
        tenant_id=evidence.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=invocation.invocation_id,
        request_fingerprint=evidence.digest(),
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                invocation.invocation_id,
                "agent_loop",
                SCHEMA,
                raw,
                hashlib.sha256(raw).hexdigest(),
            ),
        ),
    )


def _validate_invocation_actor_fields(
    receipt: RecoveryActorBudgetV1, invocation: OriginalResolverInvocationV1
) -> None:
    validate_recovery_actor_budget(receipt)
    if (
        receipt.trust_journal_head != invocation.trust_journal_head
        or hashlib.sha256(receipt.response.canonical_payload).hexdigest()
        != invocation.trust_response_fingerprint
    ):
        raise ValueError("resolver invocation trust reference differs from retained actor receipt")


def _selected_policy_source_records(
    runtime: R14PlanningRuntime,
) -> tuple[RecoverySourceRecord, ...] | None:
    from chiplog.composition import r14_original_resolver_policy_publication

    selected = r14_original_resolver_policy_publication._selected_entries(runtime)
    if not selected:
        return None
    decision_id, decision_bytes, entry, batch = selected[0]
    evidence = validate_policy_selected(runtime, entry, batch)
    if len(batch.records) != 2:
        raise ValueError("selected resolver policy lacks reducer record")
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        original_effect_evidence_reducer_policy_v1,
    )
    from chiplog.capabilities.agent_loop.recovery_contracts import Present
    from chiplog.composition.r14_original_resolver_policy_publication import REDUCER_SCHEMA, SCHEMA

    raw = decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    selected_digest = hashlib.sha256(raw).hexdigest()
    policy_sources = []
    for record in batch.records:
        digest = hashlib.sha256(record.canonical_bytes).hexdigest()
        subject_id = (
            evidence.request.policy.policy_id
            if record.schema_id == SCHEMA
            else original_effect_evidence_reducer_policy_v1(runtime._tenant_id).policy_id
        )
        if record.schema_id not in (SCHEMA, REDUCER_SCHEMA):
            raise ValueError("selected resolver policy source schema differs")
        policy_sources.append(
            RecoverySourceRecord(
                owner=record.owner,
                subject=CallSubjectHead(
                    subject_id=subject_id,
                    revision=Present(head="record:" + digest, fingerprint=digest),
                ),
                schema_id=record.schema_id,
                canonical_record_bytes=record.canonical_bytes,
                selected_decision=CallSubjectHead(
                    subject_id=decision_id,
                    revision=Present(head=decision_id, fingerprint=selected_digest),
                ),
                physical_record=CallSubjectHead(
                    subject_id=record.record_id,
                    revision=Present(head="record:" + digest, fingerprint=digest),
                ),
            )
        )
    return tuple(policy_sources)


def _selected_open_sources_for_historical_suffix(
    runtime: R14PlanningRuntime,
) -> tuple[RecoverySourceRecord, RecoverySourceRecord] | None:
    from chiplog.composition.r14_accepted_open_accounting_records import (
        ACCEPTED_OPEN_RECOVERY_OPERATION,
        RetainedAcceptedOpenRecoveryPublication,
        accepted_open_recovery_command,
        retained_accepted_open_recovery,
    )
    from chiplog.composition.r14_original_obligation_sources import (
        accepted_open_recovery_sources,
    )

    matches = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry: dict[str, object] = json.loads(raw)
        if (
            entry.get("kind") == "DECIDED"
            and entry.get("operation_kind") == ACCEPTED_OPEN_RECOVERY_OPERATION
        ):
            matches.append((decision_id, raw, entry))
    if len(matches) != 1:
        return None
    decision_id, raw, entry = matches[0]
    evidence = RetainedAcceptedOpenRecoveryPublication.model_validate_json(
        retained_accepted_open_recovery(entry).canonical_bytes()
    )
    batch = accepted_open_recovery_command(evidence)
    if (
        runtime._publication(entry) != batch
        or entry.get("operation_id") != batch.idempotency_key
        or entry.get("expected_head") != batch.expected_head
        or entry.get("fingerprint") != batch.request_fingerprint
    ):
        raise ValueError("selected accepted/open sources differ from exact publication")
    state, _, _ = runtime._selected_physical_state(batch)
    if state != "COMPLETE":
        raise ValueError("selected accepted/open sources are not physically complete")
    selected_bytes = raw.encode() if isinstance(raw, str) else raw
    return accepted_open_recovery_sources(
        evidence,
        selected_decision_id=decision_id,
        selected_decision_bytes=selected_bytes,
    )


def _current_sources(runtime: R14PlanningRuntime) -> tuple[RecoverySourceRecord, ...] | None:
    opened = read_selected_original_obligation_sources(runtime, historical=True)
    policy_sources = _selected_policy_source_records(runtime)
    if opened is None or policy_sources is None:
        return None
    return (*opened, *policy_sources)


def validate_selected_for_historical_suffix(
    runtime: R14PlanningRuntime, entry: dict[str, object], batch: PhysicalPublicationCommand
) -> RetainedOriginalResolverInvocationPublication:
    """Validate invocation evidence against leaf selected sources without reentering history."""
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected original resolver invocation evidence missing")
    evidence = RetainedOriginalResolverInvocationPublication.model_validate_json(raw)
    invocation = evidence.prepared.invocation
    opening_sources = _selected_open_sources_for_historical_suffix(runtime)
    policy_sources = _selected_policy_source_records(runtime)
    if opening_sources is None or policy_sources is None:
        raise ValueError("selected invocation historical sources are absent")
    sources = (*opening_sources, *policy_sources)
    if (
        evidence.canonical_bytes().decode() != raw
        or evidence.tenant_id != runtime._tenant_id
        or evidence.database_id != runtime._database_identity[0]
        or evidence.predecessor != entry.get("predecessor")
        or command(evidence) != batch
        or entry.get("operation_kind") != OPERATION
        or entry.get("operation_id") != batch.idempotency_key
        or invocation.tenant_id != runtime._tenant_id
        or (
            invocation.accepted_opening_source,
            invocation.accepted_terminal_source,
            invocation.resolver_policy_source,
            invocation.reducer_policy_source,
        )
        != sources
    ):
        raise ValueError("selected invocation differs from exact historical sources")
    _validate_invocation_actor(runtime, evidence)
    return evidence


def _selected_entries(
    runtime: R14PlanningRuntime,
) -> list[tuple[str, bytes, dict[str, object], PhysicalPublicationCommand]]:
    matches = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry: dict[str, object] = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        rows = entry.get("records")
        schema_match = isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("schema") == SCHEMA for row in rows
        )
        if entry.get("operation_kind") == OPERATION or EVIDENCE_FIELD in entry or schema_match:
            matches.append((decision_id, raw, entry, runtime._publication(entry)))
    if len(matches) > 1:
        raise ValueError("ambiguous original resolver invocation selection")
    return matches


def _validate_physical_catalog(
    runtime: R14PlanningRuntime, batch: PhysicalPublicationCommand | None
) -> None:
    with read_connection(runtime._database) as connection:
        records = tuple(
            connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence "
                "FROM records WHERE tenant_id=? AND schema_id=? ORDER BY record_id",
                (runtime._tenant_id, SCHEMA),
            ).fetchall()
        )
        publications = tuple(
            connection.execute(
                "SELECT idempotency_key, request_fingerprint, commit_sequence, record_ids "
                "FROM publications WHERE tenant_id=? AND operation_kind=? "
                "ORDER BY idempotency_key",
                (runtime._tenant_id, OPERATION),
            ).fetchall()
        )
    if batch is None:
        if records or publications:
            raise ValueError("unselected original resolver invocation physical records exist")
        return
    record = batch.records[0]
    expected_record = (
        record.record_id,
        record.owner,
        record.schema_id,
        record.canonical_bytes,
        batch.expected_head + 1,
    )
    actual_publications = tuple(
        (key, fingerprint, sequence, record_ids.split("\n"))
        for key, fingerprint, sequence, record_ids in publications
    )
    expected_publication = (
        batch.idempotency_key,
        batch.request_fingerprint,
        batch.expected_head + 1,
        [record.record_id],
    )
    if records != (expected_record,) or actual_publications != (expected_publication,):
        raise ValueError("original resolver invocation physical catalog differs from selection")


def validate_selected(
    runtime: R14PlanningRuntime, entry: dict[str, object], batch: PhysicalPublicationCommand
) -> RetainedOriginalResolverInvocationPublication:
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected original resolver invocation evidence missing")
    evidence = RetainedOriginalResolverInvocationPublication.model_validate_json(raw)
    invocation = evidence.prepared.invocation
    sources = _current_sources(runtime)
    if sources is None:
        raise ValueError("selected original resolver invocation sources are absent")
    if (
        evidence.canonical_bytes().decode() != raw
        or evidence.tenant_id != runtime._tenant_id
        or evidence.database_id != runtime._database_identity[0]
        or evidence.predecessor != entry.get("predecessor")
        or command(evidence) != batch
        or entry.get("operation_kind") != OPERATION
        or entry.get("operation_id") != batch.idempotency_key
        or invocation.tenant_id != runtime._tenant_id
        or (
            invocation.accepted_opening_source,
            invocation.accepted_terminal_source,
            invocation.resolver_policy_source,
            invocation.reducer_policy_source,
        )
        != sources
    ):
        raise ValueError("selected original resolver invocation differs from exact current sources")
    _validate_invocation_actor(runtime, evidence)
    return evidence


def read_selected_original_resolver_invocation_source_held(
    runtime: R14PlanningRuntime, *, historical: bool = False
) -> tuple[OriginalResolverInvocationV1, RecoverySourceRecord] | None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return None
    decision_id, decision_bytes, entry, batch = selected[0]
    evidence = validate_selected(runtime, entry, batch)
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    if anchored is None:
        raise ValueError("selected original resolver invocation commitment anchor is absent")
    historical_suffix = False
    if historical and actual != anchored:
        from chiplog.composition.r14_historical_suffix import (
            authenticate_selected_r14_source_cut_held,
        )

        historical_suffix = authenticate_selected_r14_source_cut_held(
            runtime, actual, anchored, target_commitment=str(entry.get("resulting"))
        )
    if (
        state != "COMPLETE"
        or (actual != anchored and not historical_suffix)
        or (not historical and actual != entry.get("resulting"))
    ):
        raise ValueError("selected original resolver invocation selected/physical mismatch")
    _validate_physical_catalog(runtime, batch)
    record = batch.records[0]
    digest = hashlib.sha256(record.canonical_bytes).hexdigest()
    selected_digest = hashlib.sha256(
        decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    ).hexdigest()
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.recovery_contracts import Present

    source = RecoverySourceRecord(
        owner=record.owner,
        subject=CallSubjectHead(
            subject_id=evidence.prepared.invocation.invocation_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
        schema_id=record.schema_id,
        canonical_record_bytes=record.canonical_bytes,
        selected_decision=CallSubjectHead(
            subject_id=decision_id,
            revision=Present(head=decision_id, fingerprint=selected_digest),
        ),
        physical_record=CallSubjectHead(
            subject_id=record.record_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        ),
    )
    return evidence.prepared.invocation, source


def _startup_state(
    runtime: R14PlanningRuntime, entry: dict[str, object], batch: PhysicalPublicationCommand
) -> None:
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    if anchored is None:
        raise ValueError("selected original resolver invocation commitment anchor is absent")
    predecessor, resulting = str(entry["predecessor"]), str(entry["resulting"])
    materialized = any(
        json.loads(raw).get("kind") == "MATERIALIZED"
        and json.loads(raw).get("operation_id") == batch.idempotency_key
        for _, _, raw in runtime._loop_decisions().entries()
    )
    if state == "ABSENT" and not materialized and actual == predecessor and anchored == predecessor:
        _validate_physical_catalog(runtime, None)
        return
    if (
        state == "COMPLETE"
        and actual == resulting
        and (anchored == resulting or (not materialized and anchored == predecessor))
    ):
        _validate_physical_catalog(runtime, batch)
        return
    if state == "COMPLETE":
        from chiplog.composition.r14_historical_suffix import (
            authenticate_selected_r14_startup_suffix_held,
        )

        if authenticate_selected_r14_startup_suffix_held(
            runtime, actual, anchored, target_commitment=resulting
        ):
            _validate_physical_catalog(runtime, batch)
            return
    raise ValueError("selected original resolver invocation has partial physical state")


def validate_invocation_startup(runtime: R14PlanningRuntime) -> None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return
    _, _, entry, batch = selected[0]
    validate_selected(runtime, entry, batch)
    _startup_state(runtime, entry, batch)


async def _owner_exchange(
    runtime: R14PlanningRuntime, request: PrepareOriginalResolverInvocationV1
) -> tuple[PreparedOriginalResolverInvocationV1, PublicPortCall, PublicPortSuccess]:
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    sent = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="original-resolver-invocation:" + secrets.token_hex(16),
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id=f"broker:{callee.generation_id}",
        ),
        callee=callee,
        schema_id=ORIGINAL_RECOVERY_REQUEST_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )
    reply = await engine.call(sent)
    if (
        not isinstance(reply, PublicPortSuccess)
        or reply.request_id != sent.request_id
        or reply.responder != sent.callee
        or reply.schema_id != ORIGINAL_RECOVERY_RESULT_SCHEMA
        or time.monotonic_ns() >= sent.budget.absolute_deadline_ns
    ):
        raise LoopRejected("original resolver invocation owner IPC unavailable")
    result = decode_original_recovery_result(reply.schema_id, reply.canonical_payload).value
    if not isinstance(result, PreparedOriginalResolverInvocationV1):
        raise LoopRejected("original resolver invocation owner rejected preparation")
    return result, sent, reply


async def _finish(
    runtime: R14PlanningRuntime, entry: dict[str, object]
) -> OriginalResolverInvocationV1 | None:
    batch = runtime._publication(entry)
    with runtime._authority_gate().hold():
        validate_selected(runtime, entry, batch)
    await runtime._recover_exact(
        batch,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(batch.idempotency_key, entry),
    )
    runtime._finish_decision(batch.idempotency_key, expected=entry)
    with runtime._authority_gate().hold():
        selected = read_selected_original_resolver_invocation_source_held(runtime)
        return None if selected is None else selected[0]


async def publish_original_resolver_invocation(
    runtime: R14ExecutionRuntime, peer: str = "hermetic-ingress"
) -> OriginalResolverInvocationV1 | None:
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if selected:
            validate_selected(runtime, selected[0][2], selected[0][3])
            selected_entry = selected[0][2]
        else:
            selected_entry = None
    if selected_entry is not None:
        return await _finish(runtime, selected_entry)
    observed, actor_budget = await issue_execution_recovery_actor_v1(runtime, peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        selected = _selected_entries(runtime)
        if selected:
            validate_selected(runtime, selected[0][2], selected[0][3])
            selected_entry = selected[0][2]
        else:
            selected_entry = None
        sources = None if selected_entry is not None else _current_sources(runtime)
        if selected_entry is None:
            if sources is None:
                raise LoopRejected("original resolver invocation sources are incomplete")
            opening, terminal, resolver_source, reducer_source = sources
            from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
                AcceptedOpenRecoveryOpeningV1,
            )

            original = AcceptedOpenRecoveryOpeningV1.model_validate_json(
                opening.canonical_record_bytes
            ).original
            invocation_id = (
                "r14-resolver-invocation:" + runtime._tenant_id + ":" + original.obligation_id
            )
            identity = json.loads(
                runtime._decode_trust_result(
                    actor_budget.response.canonical_payload
                ).reference_bytes
                or b"{}"
            )
            invocation = OriginalResolverInvocationV1(
                invocation_id=invocation_id,
                tenant_id=runtime._tenant_id,
                original=original,
                accepted_opening_source=opening,
                accepted_terminal_source=terminal,
                resolver_policy_source=resolver_source,
                reducer_policy_source=reducer_source,
                principal_id=identity.get("principal_id"),
                trust_journal_head=actor_budget.trust_journal_head,
                trust_response_fingerprint=hashlib.sha256(
                    actor_budget.response.canonical_payload
                ).hexdigest(),
            )
            request = PrepareOriginalResolverInvocationV1(
                command_id=invocation_id,
                invocation=invocation,
            )
            _validate_invocation_actor_fields(actor_budget, invocation)
            anchored_predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            if anchored_predecessor is None:
                raise LoopRejected("original resolver invocation predecessor is not anchored")
            predecessor = anchored_predecessor
            with read_connection(runtime._database) as connection:
                actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                row = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                ).fetchone()
                sequence = 0 if row is None else int(row[0])
            if actual != predecessor:
                raise LoopRejected(
                    "original resolver invocation predecessor differs from physical state"
                )
            worker = runtime.current_worker()
            session = runtime._supervisor.runtime().session("agent_loop")
    if selected_entry is not None:
        return await _finish(runtime, selected_entry)
    prepared, sent, reply = await _owner_exchange(runtime, request)
    evidence = RetainedOriginalResolverInvocationPublication(
        tenant_id=runtime._tenant_id,
        database_id=runtime._database_identity[0],
        request=request,
        prepared=prepared,
        owner_call=sent,
        owner_reply=reply,
        actor_budget_v1=actor_budget,
        expected_head=sequence,
        predecessor=predecessor,
    )
    try:
        command_value = command(evidence)
    except (TypeError, ValueError) as error:
        raise LoopRejected("original resolver invocation evidence failed validation") from error

    admission_passed = False

    def guard() -> Literal["STALE"] | None:
        nonlocal admission_passed
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_entries(runtime)
                    or _current_sources(runtime)
                    != (
                        invocation.accepted_opening_source,
                        invocation.accepted_terminal_source,
                        invocation.resolver_policy_source,
                        invocation.reducer_policy_source,
                    )
                ):
                    return "STALE"
            except ValueError, TypeError, RuntimeError:
                return "STALE"
            admission_passed = True
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            # _publish keeps this gate and transaction from admission through commit:
            # source/trust journals cannot change here, so only monotonic expiry and
            # supervisor identity need a final check after the full admission scan.
            deadline = (
                observed.request.budget.absolute_deadline_ns
                if observed.admission_valid_until_ns is None
                else observed.admission_valid_until_ns
            )
            current_trust_session = runtime._supervisor.runtime().session("deployment_trust")
            trust_state = runtime._read_ledger.current_state(runtime._tenant_id)
            if (
                not admission_passed
                or time.monotonic_ns() >= deadline
                or current_trust_session != observed.request.callee
                or trust_state.owner_draining
                or trust_state.broker_epoch != current_trust_session.broker_epoch
                or trust_state.owner_generation != current_trust_session.generation_id
                or runtime.current_worker() != worker
                or runtime._supervisor.runtime().session("agent_loop") != session
            ):
                raise LoopRejected("original resolver invocation sources changed at selection")
            runtime._require_no_pending()
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": command_value.idempotency_key,
                    "operation_kind": OPERATION,
                    "expected_head": command_value.expected_head,
                    "fingerprint": command_value.request_fingerprint,
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
                        for record in command_value.records
                    ],
                    EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(command_value, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("original resolver invocation publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if len(selected) != 1:
            raise LoopRejected("original resolver invocation selected identity missing")
    return await _finish(runtime, selected[0][2])
