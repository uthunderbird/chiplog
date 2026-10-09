"""Select, materialize, and reopen one original R14 resolution batch."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, Protocol

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    PreparedOriginalCallResolution,
    PrepareOriginalCallResolution,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
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


class OriginalResolutionHistoryRuntime(Protocol):
    _tenant_id: str
    _database_identity: tuple[str, int, int]

    def _check_database_identity(self) -> None: ...

OPERATION = "agent_loop.original-call-resolution.v1"
OWNER_OPERATION = "agent_loop.prepare_original_call_resolution.v1"
EVIDENCE_FIELD = "original_call_resolution_evidence"
_RECORDS = (
    (
        "ORIGINAL_RESOLUTION_BASIS",
        "chiplog.loop.original-resolution-basis.v1",
        "basis",
    ),
    (
        "ORIGINAL_OBLIGATION_CLOSURE",
        "chiplog.loop.original-obligation-closure.v1",
        "closure",
    ),
    (
        "RECOVERED_CALL_OUTCOME",
        "chiplog.loop.recovered-call-outcome.v1",
        "recovered_outcome",
    ),
    (
        "ORIGINAL_RESOLVER_BATCH",
        "chiplog.loop.original-resolver-batch.v1",
        "batch",
    ),
)
_SCHEMAS = frozenset(schema for _, schema, _ in _RECORDS)
type SelectedEntry = tuple[str, str | bytes, dict[str, object], PhysicalPublicationCommand]


class RetainedOriginalCallResolutionPublication(RecoveryDTO):
    tenant_id: str
    database_id: str
    request: PrepareOriginalCallResolution
    prepared: PreparedOriginalCallResolution
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    expected_head: int
    predecessor: str


def command(evidence: RetainedOriginalCallResolutionPublication) -> PhysicalPublicationCommand:
    request = PrepareOriginalCallResolution.model_validate_json(evidence.request.canonical_bytes())
    prepared = prepare_original_call_resolution(request)
    reply = decode_original_recovery_result(
        ORIGINAL_RECOVERY_RESULT_SCHEMA, evidence.owner_reply.canonical_payload
    ).value
    if (
        request != evidence.request
        or prepared != evidence.prepared
        or not isinstance(prepared, PreparedOriginalCallResolution)
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
        or request.cut.tenant_id != evidence.tenant_id
        or request.cut.database_id != evidence.database_id
        or request.cut.tenant_commit_sequence != evidence.expected_head
        or request.cut.materialization_commitment != evidence.predecessor
        or prepared.source_request_fingerprint != request.digest()
    ):
        raise ValueError("original resolution owner exchange or source cut differs")

    rows = tuple(
        PhysicalRecord(
            getattr(prepared, attr).basis_id
            if attr == "basis"
            else getattr(prepared, attr).closure_id
            if attr == "closure"
            else getattr(prepared, attr).outcome_id
            if attr == "recovered_outcome"
            else getattr(prepared, attr).batch_id,
            "agent_loop",
            schema,
            getattr(prepared, attr).canonical_bytes(),
            hashlib.sha256(getattr(prepared, attr).canonical_bytes()).hexdigest(),
        )
        for _, schema, attr in _RECORDS
    )
    return PhysicalPublicationCommand(
        tenant_id=evidence.tenant_id,
        operation_kind=OPERATION,
        idempotency_key=prepared.batch.batch_id,
        request_fingerprint=evidence.digest(),
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=rows,
    )


def _selected_entries(runtime: CommonCliExecutionRuntime) -> list[SelectedEntry]:
    matches: list[SelectedEntry] = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        rows = entry.get("records")
        schema_match = isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("schema") in _SCHEMAS for row in rows
        )
        if (
            entry.get("operation_kind") == OPERATION
            or EVIDENCE_FIELD in entry
            or schema_match
        ) and entry.get("kind") == "DECIDED":
            matches.append(
                (
                    decision_id,
                    raw,
                    entry,
                    runtime._publication(entry),
                )
            )
    if len(matches) > 1:
        raise ValueError("ambiguous original call resolution selection")
    return matches


def _validate_physical_catalog(
    runtime: CommonCliExecutionRuntime, batch: PhysicalPublicationCommand | None
) -> None:
    placeholders = ",".join("?" for _ in _SCHEMAS)
    with read_connection(runtime._database) as connection:
        records = tuple(
            connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence "
                "FROM records WHERE tenant_id=? AND schema_id IN ("
                + placeholders
                + ") ORDER BY record_id",
                (runtime._tenant_id, *_SCHEMAS),
            ).fetchall()
        )
        publications = tuple(
            connection.execute(
                "SELECT idempotency_key, request_fingerprint, commit_sequence, record_ids "
                "FROM publications WHERE tenant_id=? AND operation_kind=?",
                (runtime._tenant_id, OPERATION),
            ).fetchall()
        )
    if batch is None:
        if records or publications:
            raise ValueError("unselected original resolution physical records exist")
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
            for record in batch.records
        )
    )
    actual_publications = tuple(
        (key, fingerprint, sequence, tuple(record_ids.split("\n")))
        for key, fingerprint, sequence, record_ids in publications
    )
    expected_publication = (
        batch.idempotency_key,
        batch.request_fingerprint,
        batch.expected_head + 1,
        tuple(record.record_id for record in batch.records),
    )
    if records != expected_records or actual_publications != (expected_publication,):
        raise ValueError("original resolution physical catalog differs from selection")


def _validate_current_sources(
    runtime: CommonCliExecutionRuntime,
    evidence: RetainedOriginalCallResolutionPublication,
) -> None:
    current = capture_original_call_resolution_request(
        runtime, allow_selected_resolution=True
    )
    current = current.model_copy(
        update={
            "cut": current.cut.model_copy(
                update={
                    "tenant_commit_sequence": evidence.request.cut.tenant_commit_sequence,
                    "materialization_commitment": evidence.request.cut.materialization_commitment,
                }
            )
        }
    )
    if current != evidence.request:
        raise ValueError("selected original resolution differs from current source cut")


def validate_selected_command(
    runtime: OriginalResolutionHistoryRuntime,
    entry: dict[str, object],
    batch: PhysicalPublicationCommand,
) -> RetainedOriginalCallResolutionPublication:
    raw = entry.get(EVIDENCE_FIELD)
    if not isinstance(raw, str):
        raise ValueError("selected original resolution evidence missing")
    evidence = RetainedOriginalCallResolutionPublication.model_validate_json(raw)
    runtime._check_database_identity()
    resulting = entry.get("resulting")
    if not isinstance(resulting, str) or len(resulting) != 64 or any(
        char not in "0123456789abcdef" for char in resulting
    ):
        raise ValueError("selected original resolution resulting commitment is invalid")
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
        or evidence.prepared.batch.command_id != evidence.request.command_id
    ):
        raise ValueError("selected original resolution command differs from retained evidence")
    return evidence


def validate_selected(
    runtime: CommonCliExecutionRuntime,
    entry: dict[str, object],
    batch: PhysicalPublicationCommand,
) -> RetainedOriginalCallResolutionPublication:
    evidence = validate_selected_command(runtime, entry, batch)
    _validate_current_sources(runtime, evidence)
    return evidence


def _startup_state(
    runtime: CommonCliExecutionRuntime,
    entry: dict[str, object],
    batch: PhysicalPublicationCommand,
) -> None:
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    predecessor, resulting = str(entry["predecessor"]), str(entry["resulting"])
    materialized = any(
        json.loads(raw).get("kind") == "MATERIALIZED"
        and json.loads(raw).get("operation_id") == batch.idempotency_key
        for _, _, raw in runtime._loop_decisions().entries()
    )
    if state == "ABSENT" and not materialized and actual == predecessor and anchored == predecessor:
        _validate_physical_catalog(runtime, None)
        return
    if state == "COMPLETE" and actual == resulting and (
        anchored == resulting or (not materialized and anchored == predecessor)
    ):
        _validate_physical_catalog(runtime, batch)
        return
    raise ValueError("selected original resolution has partial physical state")


def read_selected_original_call_resolution_source_held(
    runtime: CommonCliExecutionRuntime,
) -> PreparedOriginalCallResolution | None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return None
    _, _, entry, batch = selected[0]
    evidence = validate_selected(runtime, entry, batch)
    with read_connection(runtime._database) as connection:
        state = inspect_publication(connection, batch, batch.expected_head + 1)
        actual = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
    anchored = runtime._commitment_journal.load(runtime._tenant_id)
    if state != "COMPLETE" or actual != anchored or actual != entry.get("resulting"):
        raise ValueError("selected original resolution selected/physical mismatch")
    _validate_physical_catalog(runtime, batch)
    return evidence.prepared


def validate_original_call_resolution_startup(
    runtime: CommonCliExecutionRuntime,
) -> None:
    runtime._pending()
    selected = _selected_entries(runtime)
    if not selected:
        _validate_physical_catalog(runtime, None)
        return
    _, _, entry, batch = selected[0]
    validate_selected(runtime, entry, batch)
    _startup_state(runtime, entry, batch)


async def _owner_exchange(
    runtime: CommonCliExecutionRuntime, request: PrepareOriginalCallResolution
) -> tuple[PreparedOriginalCallResolution, PublicPortCall, PublicPortSuccess]:
    engine = runtime._supervisor.runtime()
    callee = engine.session("agent_loop")
    sent = PublicPortCall(
        operation_id=OWNER_OPERATION,
        request_id="original-call-resolution:" + secrets.token_hex(16),
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
        raise LoopRejected("original call resolution owner IPC unavailable")
    prepared = decode_original_recovery_result(reply.schema_id, reply.canonical_payload).value
    if not isinstance(prepared, PreparedOriginalCallResolution):
        raise LoopRejected("original call resolution owner rejected preparation")
    return prepared, sent, reply


async def _finish(
    runtime: CommonCliExecutionRuntime, entry: dict[str, object]
) -> PreparedOriginalCallResolution:
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
        selected = read_selected_original_call_resolution_source_held(runtime)
        if selected is None:
            raise LoopRejected("selected original resolution result was not observed")
        return selected


async def publish_original_call_resolution(
    runtime: CommonCliExecutionRuntime, peer: str = "hermetic-ingress"
) -> PreparedOriginalCallResolution:
    """Resolve the selected original obligation from its exact closed R16 outcome."""
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if selected:
            validate_selected(runtime, selected[0][2], selected[0][3])
            selected_entry = selected[0][2]
        else:
            selected_entry = None
    if selected_entry is not None:
        return await _finish(runtime, selected_entry)

    with runtime._authority_gate().hold():
        request = capture_original_call_resolution_request(runtime)
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        if predecessor != request.cut.materialization_commitment:
            raise LoopRejected("original resolution predecessor differs from source cut")
        expected_head = request.cut.tenant_commit_sequence
        worker = runtime.current_worker()
        session = runtime._supervisor.runtime().session("agent_loop")

    prepared, sent, reply = await _owner_exchange(runtime, request)
    evidence = RetainedOriginalCallResolutionPublication(
        tenant_id=runtime._tenant_id,
        database_id=runtime._database_identity[0],
        request=request,
        prepared=prepared,
        owner_call=sent,
        owner_reply=reply,
        expected_head=expected_head,
        predecessor=predecessor,
    )
    try:
        batch = command(evidence)
    except (TypeError, ValueError) as error:
        raise LoopRejected("original resolution owner evidence failed validation") from error

    def guard() -> Literal["STALE"] | None:
        with runtime._authority_gate().hold():
            try:
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                    or _selected_entries(runtime)
                    or capture_original_call_resolution_request(runtime) != request
                ):
                    return "STALE"
            except (LoopRejected, ValueError, TypeError, RuntimeError):
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
        raise LoopRejected("original call resolution publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = _selected_entries(runtime)
        if len(selected) != 1:
            raise LoopRejected("original call resolution selected identity missing")
    return await _finish(runtime, selected[0][2])
