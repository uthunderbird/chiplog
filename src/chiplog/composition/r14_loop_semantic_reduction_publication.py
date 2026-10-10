"""Select one appendable semantic reduction through the mounted R14 writer."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    LoopRecoveryStream,
    PreparedLoopSemanticReduction,
    PrepareLoopSemanticReduction,
    SelectedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present, RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    ORIGINAL_RECOVERY_REQUEST_SCHEMA,
    ORIGINAL_RECOVERY_RESULT_SCHEMA,
    decode_original_recovery_result,
)
from chiplog.composition.r14_original_call_resolution_source import (
    capture_original_call_resolution_request,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

OPERATION = "agent_loop.loop-semantic-reduction.v1"
OWNER_OPERATION = "agent_loop.prepare_loop_semantic_reduction.v1"
EVIDENCE_FIELD = "loop_semantic_reduction_evidence"
SCHEMA = "chiplog.loop.semantic-reduction.v1"
RECORD_KIND = "LOOP_SEMANTIC_REDUCTION"


class RetainedLoopSemanticReductionPublication(RecoveryDTO):
    tenant_id: str
    database_id: str
    request: PrepareLoopSemanticReduction
    prepared: PreparedLoopSemanticReduction
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    expected_head: int
    predecessor: str


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _record_id(prepared: PreparedLoopSemanticReduction) -> str:
    return SCHEMA + ":" + _sha(prepared.record.canonical_bytes())


def command(evidence: RetainedLoopSemanticReductionPublication) -> PhysicalPublicationCommand:
    request = PrepareLoopSemanticReduction.model_validate_json(evidence.request.canonical_bytes())
    prepared = prepare_loop_semantic_reduction(request)
    reply = decode_original_recovery_result(
        ORIGINAL_RECOVERY_RESULT_SCHEMA, evidence.owner_reply.canonical_payload
    ).value
    if (
        request != evidence.request
        or prepared != evidence.prepared
        or not isinstance(prepared, PreparedLoopSemanticReduction)
        or reply != prepared
        or evidence.owner_call.operation_id != OWNER_OPERATION
        or evidence.owner_call.schema_id != ORIGINAL_RECOVERY_REQUEST_SCHEMA
        or evidence.owner_call.canonical_payload != request.canonical_bytes()
        or evidence.owner_reply.request_id != evidence.owner_call.request_id
        or evidence.owner_reply.responder != evidence.owner_call.callee
        or evidence.owner_reply.schema_id != ORIGINAL_RECOVERY_RESULT_SCHEMA
        or evidence.owner_reply.canonical_payload != prepared.canonical_bytes()
        or evidence.owner_call.caller.owner_id != "broker"
        or evidence.owner_call.callee.owner_id != "agent_loop"
        or evidence.owner_call.caller.tenant_id != evidence.tenant_id
        or evidence.owner_call.callee.tenant_id != evidence.tenant_id
        or request.tenant_id != evidence.tenant_id
        or request.database_id != evidence.database_id
        or request.tenant_commit_sequence != evidence.expected_head
        or request.materialization_commitment != evidence.predecessor
        or prepared.source_request_fingerprint != request.digest()
    ):
        raise ValueError("semantic reduction owner exchange or source cut differs")
    raw = prepared.record.canonical_bytes()
    digest = _sha(raw)
    member = PhysicalRecord(_record_id(prepared), "agent_loop", SCHEMA, raw, digest)
    return PhysicalPublicationCommand(
        tenant_id=evidence.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=member.record_id,
        request_fingerprint=evidence.digest(),
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(member,),
    )


def _selected_entries(runtime: CommonCliExecutionRuntime):
    found = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        rows = entry.get("records")
        schema_match = isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("schema") == SCHEMA for row in rows
        )
        if (
            entry.get("operation_kind") == OPERATION or EVIDENCE_FIELD in entry or schema_match
        ) and entry.get("kind") == "DECIDED":
            found.append((decision_id, raw, entry, runtime._publication(entry)))
    found.sort(key=lambda value: value[3].expected_head)
    if len({value[3].expected_head for value in found}) != len(found):
        raise ValueError("competing semantic reductions at one predecessor")
    return found


def validate_selected_command(runtime, entry, batch):
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected semantic reduction evidence missing")
    evidence = RetainedLoopSemanticReductionPublication.model_validate_json(raw)
    runtime._check_database_identity()
    resulting = entry.get("resulting")
    if (
        evidence.canonical_bytes().decode() != raw
        or evidence.tenant_id != runtime._tenant_id
        or evidence.database_id != runtime._database_identity[0]
        or evidence.predecessor != entry.get("predecessor")
        or command(evidence) != batch
        or entry.get("operation_kind") != OPERATION
        or entry.get("operation_id") != batch.idempotency_key
        or type(entry.get("expected_head")) is not int
        or entry.get("expected_head") != batch.expected_head
        or entry.get("fingerprint") != batch.request_fingerprint
        or not isinstance(resulting, str)
        or len(resulting) != 64
        or any(char not in "0123456789abcdef" for char in resulting)
    ):
        raise ValueError("selected semantic reduction command differs from retained evidence")
    return evidence


def _selected_anchor_sources(runtime, selected, retained, current_witness=None):
    decision_id, decision_bytes, _, batch = selected
    decision_raw = decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
    decision_digest = _sha(decision_raw)
    expected = {
        "chiplog.loop.original-resolution-basis.v1",
        "chiplog.loop.original-obligation-closure.v1",
        "chiplog.loop.recovered-call-outcome.v1",
        "chiplog.loop.original-resolver-batch.v1",
    }
    if {record.schema_id for record in batch.records} != expected or len(batch.records) != 4:
        raise ValueError("selected original resolver batch has an invalid anchor inventory")
    sources = []
    for record in batch.records:
        digest = _sha(record.canonical_bytes)
        physical = CallSubjectHead(
            subject_id=record.record_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )
        sources.append(
            RecoverySourceRecord(
                owner=record.owner,
                subject=physical,
                schema_id=record.schema_id,
                canonical_record_bytes=record.canonical_bytes,
                selected_decision=CallSubjectHead(
                    subject_id=decision_id,
                    revision=Present(head=decision_id, fingerprint=decision_digest),
                ),
                physical_record=physical,
            )
        )
    # Resolver identity is fixed at the accepted basis. The final source is the
    # current selected R16 outcome and carries the evidence visible at this cut.
    return (*sources, current_witness or retained.request.cut.selected_witness)


def _selection_prefix(selected, current) -> bool:
    """Require the full previously authenticated selection chain unchanged."""
    return len(current) >= len(selected) and all(
        before[0] == after[0] and before[1] == after[1] and before[3] == after[3]
        for before, after in zip(selected, current, strict=False)
    )


def validate_selected_witness_at_cut(
    runtime, source, tenant_commit_sequence: int, *, snapshot=None
) -> None:
    """Bind a historical R16 witness to the latest selected outcome at its cut."""
    from chiplog.capabilities.effects.dispatch_outcome_contracts import (
        DispatchOutcomeRecordV2,
    )
    from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot

    if snapshot is None:
        snapshot = runtime._owner_decisions().snapshot()
    if not isinstance(snapshot, OwnerJournalSnapshot):
        raise ValueError("semantic reduction witness has no authenticated owner history")
    decisions = snapshot.decisions
    selected_decisions = tuple(
        decision
        for decision in decisions
        if decision.decision_id == source.selected_decision.subject_id
        and decision.decision_id == source.selected_decision.revision.head
        and decision.decision_fingerprint == source.selected_decision.revision.fingerprint
        and decision.tenant_commit_sequence <= tenant_commit_sequence
    )
    if len(selected_decisions) != 1:
        raise ValueError("semantic reduction witness decision is not selected at its cut")

    def outcome_members(decision):
        members = []
        for ordinal, record in enumerate(decision.prepared.request.complete_records):
            if record.owner != "effects" or record.schema_id != source.schema_id:
                continue
            try:
                outcome = DispatchOutcomeRecordV2.model_validate_json(record.canonical_bytes)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "selected semantic reduction witness has invalid R16 bytes"
                ) from error
            if outcome.canonical_bytes() != record.canonical_bytes:
                raise ValueError("selected semantic reduction witness is noncanonical")
            members.append((decision, ordinal, record, outcome))
        return members

    expected_members = outcome_members(selected_decisions[0])
    exact = tuple(
        item
        for item in expected_members
        if item[2].record_id == source.physical_record.subject_id
        and item[2].canonical_bytes == source.canonical_record_bytes
        and item[2].fingerprint == source.physical_record.revision.fingerprint
        and item[2].record_id == source.subject.subject_id
        and item[2].fingerprint == source.subject.revision.fingerprint
    )
    if len(exact) != 1:
        raise ValueError("semantic reduction witness differs from its selected physical member")
    intent_id = exact[0][3].snapshot.intent.intent_id
    same_intent = [
        item
        for decision in decisions
        if decision.tenant_commit_sequence <= tenant_commit_sequence
        for item in outcome_members(decision)
        if item[3].snapshot.intent.intent_id == intent_id
    ]
    if not same_intent:
        raise ValueError("semantic reduction witness has no selected R16 outcome history")
    latest = max(same_intent, key=lambda item: (item[0].tenant_commit_sequence, item[1]))
    if latest != exact[0]:
        raise ValueError("semantic reduction witness is not latest at its authenticated cut")


def _require_absent(runtime: CommonCliExecutionRuntime) -> None:
    if _selected_entries(runtime):
        raise LoopRejected("selected semantic reduction already exists")
    with read_connection(runtime._database) as connection:
        records = connection.execute(
            "SELECT record_id FROM records WHERE tenant_id=? AND owner='agent_loop' "
            "AND schema_id=? LIMIT 1",
            (runtime._tenant_id, SCHEMA),
        ).fetchall()
        publications = connection.execute(
            "SELECT idempotency_key FROM publications "
            "WHERE tenant_id=? AND operation_kind=? LIMIT 1",
            (runtime._tenant_id, OPERATION),
        ).fetchall()
    if records or publications:
        raise LoopRejected("unselected semantic reduction physical rows exist")


def _validate_physical_catalog(runtime, batches):
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
    if batches is None:
        if records or publications:
            raise ValueError("unselected semantic reduction physical records exist")
        return
    expected_records = tuple(
        sorted(
            (
                record.record_id,
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                batch.expected_head + 1,
            )
            for batch in batches
            for record in batch.records
        )
    )
    expected_publications = tuple(
        sorted(
            (
                batch.idempotency_key,
                batch.request_fingerprint,
                batch.expected_head + 1,
                "\n".join(record.record_id for record in batch.records),
            )
            for batch in batches
        )
    )
    if records != expected_records or publications != expected_publications:
        raise ValueError("semantic reduction physical catalog differs from selection")


def _selected_value(runtime, selected):
    decision_id, decision_raw, entry, batch = selected
    evidence = validate_selected_command(runtime, entry, batch)
    raw = decision_raw.encode() if isinstance(decision_raw, str) else decision_raw
    digest = _sha(raw)
    record = evidence.prepared.record
    record_raw = record.canonical_bytes()
    physical = CallSubjectHead(
        subject_id=batch.records[0].record_id,
        revision=Present(head="record:" + _sha(record_raw), fingerprint=_sha(record_raw)),
    )
    source = RecoverySourceRecord(
        owner="agent_loop",
        subject=physical,
        schema_id=SCHEMA,
        canonical_record_bytes=record_raw,
        selected_decision=CallSubjectHead(
            subject_id=decision_id,
            revision=Present(head=decision_id, fingerprint=digest),
        ),
        physical_record=physical,
    )
    return evidence, SelectedLoopSemanticReduction(record=record, source=source)


def validate_loop_semantic_reduction_startup(runtime) -> None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _require_absent(runtime)
        return
    from chiplog.composition import (
        r14_original_call_resolution_publication as resolution_publication,
    )
    from chiplog.composition.r14_original_call_resolution_source import (
        _authenticate_append_evidence_suffix,
    )

    resolutions = resolution_publication._selected_entries(runtime)
    if len(resolutions) != 1:
        raise ValueError("semantic reduction chain has no unique original resolution")
    resolution = resolution_publication.validate_selected_command(
        runtime, resolutions[0][2], resolutions[0][3]
    )
    owner_snapshot = runtime._owner_decisions().snapshot()
    loop_entries = runtime._loop_decisions().entries()
    prior_reduction = None
    prior_request = None
    base_sequence = resolutions[0][3].expected_head + 1
    base_commitment = str(resolutions[0][2].get("resulting"))
    base_witness = resolution.request.cut.selected_witness
    for item in selected:
        evidence = validate_selected_command(runtime, item[2], item[3])
        expected = Absent() if prior_reduction is None else prior_reduction
        if evidence.request.expected != expected or (
            prior_request is not None
            and evidence.request.complete_ordered_evidence[
                : len(prior_request.request.complete_ordered_evidence)
            ]
            != prior_request.request.complete_ordered_evidence
        ):
            raise ValueError("selected semantic reduction chain has a stale predecessor")
        _authenticate_append_evidence_suffix(
            runtime,
            base_sequence=base_sequence,
            base_commitment=base_commitment,
            end_sequence=evidence.request.tenant_commit_sequence,
            end_commitment=evidence.request.materialization_commitment,
            base_witness=base_witness,
            owner_snapshot=owner_snapshot,
            loop_entries=loop_entries,
        )
        _, prior_reduction = _selected_value(runtime, item)
        prior_request = evidence
        base_sequence = item[3].expected_head + 1
        base_commitment = str(item[2].get("resulting"))
        base_witness = evidence.request.complete_anchor_sources[-1]
    _, _, entry, batch = selected[-1]
    resulting = str(entry["resulting"])
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
        head = connection.execute(
            "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
        ).fetchone()
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    materialized = runtime._loop_decision_materialized(batch.idempotency_key, entry)
    predecessor = str(entry["predecessor"])
    if state == "ABSENT" and not materialized and actual == predecessor and anchored == predecessor:
        _validate_physical_catalog(runtime, tuple(item[3] for item in selected[:-1]) or None)
        return
    current_sequence = 0 if head is None else int(head[0])
    if state == "COMPLETE":
        if actual == anchored:
            _authenticate_append_evidence_suffix(
                runtime,
                base_sequence=batch.expected_head + 1,
                base_commitment=resulting,
                end_sequence=current_sequence,
                end_commitment=actual,
                base_witness=prior_request.request.complete_anchor_sources[-1],
                owner_snapshot=owner_snapshot,
                loop_entries=loop_entries,
            )
        elif not (
            not materialized
            and anchored == predecessor
            and actual == resulting
            and current_sequence == batch.expected_head + 1
        ):
            raise ValueError("selected semantic reduction has a partial current suffix")
        _validate_physical_catalog(runtime, tuple(item[3] for item in selected))
        return
    raise ValueError("selected semantic reduction has partial physical state")


def read_selected_loop_semantic_reduction_source_held(runtime):
    runtime._pending()
    validate_loop_semantic_reduction_startup(runtime)
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return None
    decision = selected[-1]
    _, selected_reduction = _selected_value(runtime, decision)
    batch = decision[3]
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    if state != "COMPLETE" or actual != anchored:
        raise ValueError("selected semantic reduction selection/physical mismatch")
    _validate_physical_catalog(runtime, tuple(item[3] for item in selected))
    return selected_reduction


def capture_request(runtime: CommonCliExecutionRuntime) -> PrepareLoopSemanticReduction:
    from chiplog.composition import (
        r14_original_call_resolution_publication as resolution_publication,
    )

    runtime._pending()
    with runtime._authority_gate().hold():
        runtime._check_database_identity()
        selected = resolution_publication._selected_entries(runtime)
        if len(selected) != 1:
            raise LoopRejected("semantic reduction requires one selected original resolution")
        retained = resolution_publication.validate_selected(runtime, selected[0][2], selected[0][3])
        prepared = resolution_publication.read_selected_original_call_resolution_source_held(
            runtime
        )
        if prepared is None or prepared != retained.prepared:
            raise LoopRejected("selected original resolver anchors are unavailable")
        existing_reductions = _selected_entries(runtime)
        expected = Absent()
        if existing_reductions:
            validate_loop_semantic_reduction_startup(runtime)
            current_reduction = runtime.read_selected_loop_semantic_reduction()
            if current_reduction is None:
                raise LoopRejected("selected current semantic reduction is unavailable")
            expected = current_reduction
        else:
            _require_absent(runtime)
        source_request = capture_original_call_resolution_request(
            runtime,
            allow_selected_resolution=True,
            expected_cut=retained.request.cut,
        )
        with read_connection(runtime._database) as connection:
            commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            row = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
        anchored = runtime._commitment_journal.load(runtime._tenant_id)
        if commitment != anchored:
            raise LoopRejected("semantic reduction source cut differs from authority anchor")
        cut = source_request.cut
        original = cut.original
        command_id = (
            "r14-loop-semantic-reduction:"
            + runtime._tenant_id
            + ":"
            + original.obligation_id
            + ":"
            + cut.selected_witness.physical_record.revision.fingerprint
        )
        return PrepareLoopSemanticReduction(
            command_id=command_id,
            tenant_id=runtime._tenant_id,
            database_id=runtime._database_identity[0],
            tenant_commit_sequence=0 if row is None else int(row[0]),
            materialization_commitment=commitment,
            stream=LoopRecoveryStream(
                stream_id=original.evidence_stream_id,
                registry=cut.reducer_policy.physical_record,
                subject=cut.current_obligation.subject,
                schema_id=SCHEMA,
            ),
            expected=expected,
            original=original,
            complete_ordered_evidence=cut.complete_ordered_evidence,
            complete_anchor_sources=_selected_anchor_sources(
                runtime, selected[0], retained, source_request.cut.selected_witness
            ),
            reducer_policy=cut.reducer_policy,
            authority=cut.authority,
        )


async def _owner_exchange(runtime, request):
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    sent = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="semantic-reduction:" + secrets.token_hex(16),
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
        raise LoopRejected("semantic reduction owner IPC unavailable")
    prepared = decode_original_recovery_result(reply.schema_id, reply.canonical_payload).value
    if not isinstance(prepared, PreparedLoopSemanticReduction):
        raise LoopRejected("semantic reduction owner rejected preparation")
    return prepared, sent, reply


async def _finish(runtime, entry):
    batch = runtime._publication(entry)
    evidence = validate_selected_command(runtime, entry, batch)
    await runtime._recover_exact(
        batch,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(batch.idempotency_key, entry),
    )
    runtime._finish_decision(batch.idempotency_key, expected=entry)
    with read_connection(runtime._database) as connection:
        if inspect_publication(connection, batch, batch.expected_head + 1) != "COMPLETE":
            raise LoopRejected("selected semantic reduction failed physical readback")
    return evidence.prepared


async def publish_loop_semantic_reduction(
    runtime: CommonCliExecutionRuntime, peer: str = "hermetic-ingress"
) -> PreparedLoopSemanticReduction:
    """Publish one current reduction with a fresh source cut and independent CAS."""
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if selected:
            validate_loop_semantic_reduction_startup(runtime)
            replay_entry = selected[-1][2]
        else:
            replay_entry = None
    if replay_entry is not None:
        # A durable decision may outlive its physical append or acknowledgement.
        # Finish that exact retained command before considering a successor.
        await _finish(runtime, replay_entry)

    with runtime._authority_gate().hold():
        selected_before = tuple(_selected_entries(runtime))
        request = capture_request(runtime)
        if selected_before:
            latest = selected_before[-1]
            _, current_reduction = _selected_value(runtime, latest)
            current_evidence = tuple(
                source.physical_record for source in request.complete_ordered_evidence
            )
            if current_reduction.record.complete_ordered_evidence == current_evidence:
                return current_reduction
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor != request.materialization_commitment:
            raise LoopRejected("semantic reduction predecessor differs from current source cut")
        expected_head = request.tenant_commit_sequence
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")
    prepared, sent, reply = await _owner_exchange(runtime, request)
    evidence = RetainedLoopSemanticReductionPublication(
        tenant_id=runtime._tenant_id,
        database_id=runtime._database_identity[0],
        request=request,
        prepared=prepared,
        owner_call=sent,
        owner_reply=reply,
        expected_head=expected_head,
        predecessor=predecessor,
    )
    batch = command(evidence)

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or not _selection_prefix(selected_before, _selected_entries(runtime))
                    or capture_request(runtime) != request
                ):
                    return "STALE"
            except LoopRejected, ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting: str) -> None:
        with runtime._authority_gate().hold():
            runtime._require_no_pending()
            runtime._append_decision(
                {
                    "version": 1,
                    "kind": "DECIDED",
                    "operation_id": batch.idempotency_key,
                    "operation_kind": OPERATION,
                    "expected_head": batch.expected_head,
                    "fingerprint": batch.request_fingerprint,
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
                        for record in batch.records
                    ],
                    EVIDENCE_FIELD: evidence.canonical_bytes().decode(),
                }
            )

    result = await runtime._appender.submit(
        replace(batch, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("semantic reduction publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if (
            not _selection_prefix(selected_before, selected)
            or len(selected) != len(selected_before) + 1
        ):
            raise LoopRejected("selected semantic reduction successor identity missing")
    return await _finish(runtime, selected[-1][2])
