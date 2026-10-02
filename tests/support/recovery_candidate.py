"""A valid inert recovery candidate with an independently predicted SQL delta."""

import base64
import sqlite3
from contextlib import closing

from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PrepareExecutionAccounting,
    PrepareExecutionContinuation,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_execution_accounting,
    prepare_execution_continuation,
)
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
)
from chiplog.composition.r14_recovery_publication import (
    OPERATION,
    RetainedRecoveryPublication,
    command,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.workspace_snapshot import read_connection


def inert_recovery_candidate(runtime, cut):
    identity = "execution-accounting-readiness:" + cut.digest()
    a = PrepareExecutionAccounting(command_id=identity, cut=cut)
    ar = prepare_execution_accounting(a)
    c = PrepareExecutionContinuation(
        command_id=identity, cut=cut, complete_accounting=ar.complete_accounting
    )
    cr = prepare_execution_continuation(c)
    callee = runtime._supervisor.runtime().session("agent_loop")
    caller = BrokerSession(
        tenant_id=cut.tenant_id,
        broker_epoch=callee.broker_epoch,
        generation_id=callee.generation_id,
        owner_id="broker",
        session_id="broker:" + callee.generation_id,
    )

    def exchange(request, result, operation):
        sent = PublicPortCall(
            operation_id=operation,
            request_id="inert:" + operation,
            caller=caller,
            callee=callee,
            schema_id=EXECUTION_RECOVERY_REQUEST_SCHEMA,
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1, remaining_depth=1, policy_version=1, absolute_deadline_ns=1
            ),
        )
        reply = PublicPortSuccess(
            request_id=sent.request_id,
            responder=callee,
            schema_id=EXECUTION_RECOVERY_RESULT_SCHEMA,
            canonical_payload=result.canonical_bytes(),
        )
        return sent, reply

    ac, ap = exchange(a, ar, "agent_loop.prepare_execution_accounting.v1")
    cc, cp = exchange(c, cr, "agent_loop.prepare_execution_continuation.v1")
    evidence = RetainedRecoveryPublication(
        accounting_request=a,
        accounting_result=ar,
        accounting_call=ac,
        accounting_reply=ap,
        continuation_request=c,
        continuation_result=cr,
        continuation_call=cc,
        continuation_reply=cp,
    )
    batch = command(evidence)
    sequence = cut.tenant_commit_sequence + 1
    with (
        read_connection(runtime._database) as actual,
        closing(sqlite3.connect(":memory:")) as predicted,
    ):
        actual.backup(predicted)
        for row in batch.records:
            predicted.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?)",
                (
                    cut.tenant_id,
                    row.record_id,
                    row.owner,
                    row.schema_id,
                    row.canonical_bytes,
                    sequence,
                ),
            )
        predicted.execute(
            "INSERT INTO publications VALUES(?,?,?,?,?,?)",
            (
                cut.tenant_id,
                batch.operation_kind,
                batch.idempotency_key,
                batch.request_fingerprint,
                sequence,
                "\n".join(row.record_id for row in batch.records),
            ),
        )
        predicted.execute(
            "UPDATE tenant_heads SET head=? WHERE tenant_id=?", (sequence, cut.tenant_id)
        )
        resulting = capture_authority_snapshot_commitment(predicted, cut.tenant_id)
    entry = dict(
        version=1,
        kind="DECIDED",
        operation_id=identity,
        operation_kind=OPERATION,
        expected_head=batch.expected_head,
        fingerprint=batch.request_fingerprint,
        predecessor=cut.materialization_commitment,
        resulting=resulting,
        records=[
            dict(
                record_id=row.record_id,
                owner=row.owner,
                schema=row.schema_id,
                payload=base64.b64encode(row.canonical_bytes).decode(),
                digest=row.fingerprint,
            )
            for row in batch.records
        ],
        execution_recovery_evidence=evidence.canonical_bytes().decode(),
    )
    assert runtime._publication(entry) == batch
    return evidence, entry
