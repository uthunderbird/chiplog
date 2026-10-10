"""Select the exact immutable native recovery interpretation before any cut."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    PROFILE_OPERATION,
    PROFILE_OPERATION_V2,
    PROFILE_OPERATION_V3,
    SCHEMA,
    SCHEMA_V2,
    SCHEMA_V3,
    decode_native_one_call_recovery_profile,
    decode_native_one_call_recovery_profile_v2,
    decode_native_one_call_recovery_profile_v3,
    native_one_call_recovery_profile_v2,
    native_one_call_recovery_profile_v3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present, RecoveryDTO
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.publication_readback import inspect_publication
from chiplog.platform.workspace_snapshot import read_connection


class RetainedNativeProfilePublication(RecoveryDTO):
    tenant_id: str
    database_id: str
    record_bytes: bytes
    expected_head: int
    predecessor: str


def profile_command(evidence):
    try:
        profile = decode_native_one_call_recovery_profile(evidence.record_bytes)
        operation, schema = PROFILE_OPERATION, SCHEMA
    except ValueError:
        try:
            profile = decode_native_one_call_recovery_profile_v2(evidence.record_bytes)
            operation, schema = PROFILE_OPERATION_V2, SCHEMA_V2
        except ValueError:
            profile = decode_native_one_call_recovery_profile_v3(evidence.record_bytes)
            operation, schema = PROFILE_OPERATION_V3, SCHEMA_V3
    digest = profile.digest()
    return PhysicalPublicationCommand(
        tenant_id=evidence.tenant_id,
        operation_kind=operation,
        idempotency_key="native-recovery-profile:" + digest,
        request_fingerprint=evidence.digest(),
        expected_head=evidence.expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                "native-recovery-profile:" + digest,
                "agent_loop",
                schema,
                evidence.record_bytes,
                digest,
            ),
        ),
    )


def _selected_v1_profile(runtime) -> bool:
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        batch = runtime._publication(entry)
        if batch.operation_kind == PROFILE_OPERATION or any(
            record.schema_id == SCHEMA for record in batch.records
        ):
            return True
    return False


def _assert_v2_precedes_covered_lineage(runtime, profile_sequence: int | None = None) -> None:
    """Reject V2 when V1 profile state exists or covered call lineage came first."""
    if _selected_v1_profile(runtime):
        raise ValueError("V1 recovery profile selection blocks V2")
    covered = (
        "chiplog.call.initialized.v1",
        "chiplog.call.response-seal.v1",
        "chiplog.call.cancelled-before-accept.v1",
        "chiplog.call.not-executed-result.v1",
        "chiplog.call.accepted.v1",
        "chiplog.call.execution-intent.v1",
        "chiplog.effects.record.v1",
    )
    placeholders = ",".join("?" for _ in covered)
    with read_connection(runtime._database) as connection:
        if connection.execute(
            "SELECT 1 FROM records WHERE tenant_id=? AND schema_id=? LIMIT 1",
            (runtime._tenant_id, SCHEMA),
        ).fetchone() is not None:
            raise ValueError("V1 recovery profile physical record blocks V2")
        if connection.execute(
            "SELECT 1 FROM publications WHERE tenant_id=? AND operation_kind=? LIMIT 1",
            (runtime._tenant_id, PROFILE_OPERATION),
        ).fetchone() is not None:
            raise ValueError("V1 recovery profile publication blocks V2")
        row = connection.execute(
            "SELECT MIN(commit_sequence) FROM records "
            f"WHERE tenant_id=? AND schema_id IN ({placeholders})",
            (runtime._tenant_id, *covered),
        ).fetchone()
    if (
        row is not None
        and row[0] is not None
        and (profile_sequence is None or int(row[0]) <= profile_sequence)
    ):
        raise ValueError("V2 recovery profile must precede covered call lineage")


def validate_profile_selected(runtime, entry, batch):
    raw = entry.get("native_recovery_profile_evidence")
    if not isinstance(raw, str):
        raise ValueError("native recovery profile evidence missing")
    evidence = RetainedNativeProfilePublication.model_validate_json(raw)
    if (
        evidence.canonical_bytes().decode() != raw
        or evidence.tenant_id != runtime._tenant_id
        or evidence.database_id != runtime._database_identity[0]
        or evidence.predecessor != entry.get("predecessor")
        or profile_command(evidence) != batch
        or entry.get("operation_kind") != batch.operation_kind
    ):
        raise ValueError("native recovery profile selection differs from exact configuration")
    return evidence


def read_native_recovery_profile_held(runtime):
    runtime._pending()
    selections = []
    for decision_id, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") != "DECIDED":
            continue
        batch = runtime._publication(entry)
        if (
            batch.operation_kind == PROFILE_OPERATION
            or batch.operation_kind == PROFILE_OPERATION_V2
            or batch.operation_kind == PROFILE_OPERATION_V3
            or "native_recovery_profile_evidence" in entry
            or any(r.schema_id in (SCHEMA, SCHEMA_V2, SCHEMA_V3) for r in batch.records)
        ):
            evidence = validate_profile_selected(runtime, entry, batch)
            selections.append((decision_id, raw, entry, batch, evidence))
    if not selections:
        return None
    if len(selections) != 1:
        raise ValueError("ambiguous native recovery profile selection")
    decision_id, raw, entry, batch, evidence = selections[0]
    with read_connection(runtime._database) as connection:
        if inspect_publication(connection, batch, batch.expected_head + 1) != "COMPLETE":
            raise ValueError("native recovery profile selected/physical mismatch")
    if batch.operation_kind in (PROFILE_OPERATION_V2, PROFILE_OPERATION_V3):
            _assert_v2_precedes_covered_lineage(runtime, batch.expected_head + 1)
    record = batch.records[0]
    digest = hashlib.sha256(raw).hexdigest()
    subject = CallSubjectHead(
        subject_id=record.record_id,
        revision=Present(head="record:" + record.fingerprint, fingerprint=record.fingerprint),
    )
    return RecoverySourceRecord(
        owner="agent_loop",
        subject=subject,
        schema_id=record.schema_id,
        canonical_record_bytes=evidence.record_bytes,
        selected_decision=CallSubjectHead(
            subject_id=batch.idempotency_key,
            revision=Present(head=str(decision_id), fingerprint=digest),
        ),
        physical_record=subject,
    )


async def _finish(runtime, entry):
    batch = runtime._publication(entry)
    with runtime._authority_gate().hold():
        validate_profile_selected(runtime, entry, batch)
    await runtime._recover_exact(
        batch,
        str(entry["predecessor"]),
        str(entry["resulting"]),
        is_materialized=lambda: runtime._loop_decision_materialized(batch.idempotency_key, entry),
    )
    runtime._finish_decision(batch.idempotency_key, expected=entry)
    with runtime._authority_gate().hold():
        return read_native_recovery_profile_held(runtime)


async def publish_native_recovery_profile(runtime, peer="hermetic-ingress", *, version="v2"):
    if version not in ("v2", "v3"):
        raise ValueError("native recovery profile version must be v2 or v3")
    operation = PROFILE_OPERATION_V2 if version == "v2" else PROFILE_OPERATION_V3
    observed = await runtime._execution_actor(peer)
    with runtime._authority_gate().hold():
        runtime._check_execution_actor(observed)
        runtime._pending()
        prior = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (e := json.loads(raw)).get("kind") == "DECIDED"
            and e.get("operation_kind")
            in (PROFILE_OPERATION, PROFILE_OPERATION_V2, PROFILE_OPERATION_V3)
        ]
        if len(prior) > 1:
            raise LoopRejected("ambiguous native profile history")
        if prior and prior[0].get("operation_kind") != operation:
            raise LoopRejected("native recovery profile version is already selected")
        if not prior:
            runtime._require_no_pending()
            predecessor = runtime._commitment_journal.load(runtime._tenant_id)
            from chiplog.platform.authority_reads import capture_authority_snapshot_commitment

            with read_connection(runtime._database) as connection:
                current = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
                row = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
                ).fetchone()
                sequence = 0 if row is None else int(row[0])
            if current != predecessor:
                raise LoopRejected("native profile predecessor not anchored")
            try:
                _assert_v2_precedes_covered_lineage(runtime)
            except ValueError as error:
                raise LoopRejected(str(error)) from error
            worker = runtime.current_worker()
            session = runtime._supervisor.runtime().session("agent_loop")
            evidence = RetainedNativeProfilePublication(
                tenant_id=runtime._tenant_id,
                database_id=runtime._database_identity[0],
                record_bytes=(
                    native_one_call_recovery_profile_v2()
                    if version == "v2"
                    else native_one_call_recovery_profile_v3()
                ).canonical_bytes(),
                expected_head=sequence,
                predecessor=predecessor,
            )
    if prior:
        return await _finish(runtime, prior[0])
    batch = profile_command(evidence)

    def guard():
        with runtime._authority_gate().hold():
            try:
                runtime._check_execution_actor(observed)
                if (
                    runtime.current_worker() != worker
                    or runtime._supervisor.runtime().session("agent_loop") != session
                    or runtime._commitment_journal.load(runtime._tenant_id) != predecessor
                ):
                    return "STALE"
            except ValueError, TypeError, RuntimeError:
                return "STALE"
        return None

    def decide(resulting):
        if guard() is not None:
            raise LoopRejected("native profile source changed at selection")
        runtime._require_no_pending()
        try:
            _assert_v2_precedes_covered_lineage(runtime)
        except ValueError as error:
            raise LoopRejected(str(error)) from error
        runtime._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": batch.idempotency_key,
                "operation_kind": operation,
                "expected_head": batch.expected_head,
                "fingerprint": batch.request_fingerprint,
                "predecessor": predecessor,
                "resulting": resulting,
                "records": [
                    {
                        "record_id": r.record_id,
                        "owner": r.owner,
                        "schema": r.schema_id,
                        "payload": base64.b64encode(r.canonical_bytes).decode(),
                        "digest": r.fingerprint,
                    }
                    for r in batch.records
                ],
                "native_recovery_profile_evidence": evidence.canonical_bytes().decode(),
            }
        )

    result = await runtime._appender.submit(
        replace(batch, admission_guard=guard, decision_guard=decide)
    )
    if result.disposition not in ("COMMITTED", "REPLAY"):
        raise LoopRejected("native profile publication " + result.disposition)
    with runtime._authority_gate().hold():
        selected = [
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if (e := json.loads(raw)).get("kind") == "DECIDED"
            and e.get("operation_id") == batch.idempotency_key
        ]
        if len(selected) != 1:
            raise LoopRejected("native profile selected identity missing")
    return await _finish(runtime, selected[0])


def validate_profile_startup(runtime):
    runtime._pending()
    for _, _, raw in runtime._loop_decisions().entries():
        entry = json.loads(raw)
        if entry.get("kind") == "DECIDED":
            batch = runtime._publication(entry)
            if (
                batch.operation_kind == PROFILE_OPERATION
                or batch.operation_kind == PROFILE_OPERATION_V2
                or batch.operation_kind == PROFILE_OPERATION_V3
                or "native_recovery_profile_evidence" in entry
                or any(r.schema_id in (SCHEMA, SCHEMA_V2, SCHEMA_V3) for r in batch.records)
            ):
                validate_profile_selected(runtime, entry, batch)
