"""Retained owner exchange and exact physical batch for R14 direct terminal."""

from __future__ import annotations

from chiplog.capabilities.agent_loop.direct_terminal_contracts import (
    AcceptedDirectTerminalAccountingV1,
    AcceptedDirectTerminalDispositionV1,
    AcceptedDirectTerminalResultV1,
    PrepareAcceptedDirectTerminalV1,
    PreparedAcceptedDirectTerminalV1,
)
from chiplog.capabilities.agent_loop.direct_terminal_wire_contracts import (
    REQUEST_SCHEMA as DIRECT_TERMINAL_REQUEST_SCHEMA,
)
from chiplog.capabilities.agent_loop.direct_terminal_wire_contracts import (
    RESULT_SCHEMA as DIRECT_TERMINAL_RESULT_SCHEMA,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.composition.r14_recovery_publication import RecoveryActorBudgetV1
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

ACCEPTED_DIRECT_TERMINAL_OPERATION = "agent_loop.accepted-direct-terminal.v1"
ACCEPTED_DIRECT_TERMINAL_OWNER_OPERATION = "agent_loop.prepare_accepted_direct_terminal.v1"
ACCEPTED_DIRECT_TERMINAL_ACCOUNTING_SCHEMA = (
    "chiplog.execution.accepted-direct-terminal-accounting.v1"
)
ACCEPTED_DIRECT_TERMINAL_SCHEMA = "chiplog.execution.accepted-direct-terminal-disposition.v1"
ACCEPTED_DIRECT_TERMINAL_RESULT_SCHEMA = "chiplog.execution.accepted-direct-terminal-result.v1"


class RetainedAcceptedDirectTerminalPublication(RecoveryDTO):
    """Exact V30 request/result exchange selected by the publication journal."""

    request: PrepareAcceptedDirectTerminalV1
    result: PreparedAcceptedDirectTerminalV1
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


def accepted_direct_terminal_command(
    evidence: RetainedAcceptedDirectTerminalPublication,
) -> PhysicalPublicationCommand:
    """Rebuild the atomic accounting + terminal + result batch from retained bytes."""
    from chiplog.capabilities.agent_loop.direct_terminal_preparation import (
        prepare_accepted_direct_terminal_v1,
    )
    from chiplog.composition.r14_recovery_publication import validate_recovery_actor_budget

    request, result = evidence.request, evidence.result
    if (
        prepare_accepted_direct_terminal_v1(request) != result
        or result.source_request_fingerprint != request.digest()
        or result.accounting.original_call_id != request.cut.original_call_id
        or result.terminal.original_call_id != request.cut.original_call_id
        or result.result.original_call_id != request.cut.original_call_id
        or result.accounting.terminal.subject_id != result.terminal.terminal_id
        or result.accounting.result.subject_id != result.result.result_id
    ):
        raise ValueError("accepted/direct terminal owner result differs from exact request")
    validate_recovery_actor_budget(evidence.actor_budget_v1)  # type: ignore[no-untyped-call]
    if evidence.actor_budget_v1.request.callee.tenant_id != request.cut.tenant_id:
        raise ValueError("accepted/direct terminal actor budget tenant differs from source cut")
    sent, reply = evidence.owner_call, evidence.owner_reply
    if (
        sent.operation_id != ACCEPTED_DIRECT_TERMINAL_OWNER_OPERATION
        or sent.schema_id != DIRECT_TERMINAL_REQUEST_SCHEMA
        or sent.canonical_payload != request.canonical_bytes()
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "agent_loop"
        or sent.caller.tenant_id != request.cut.tenant_id
        or sent.callee.tenant_id != request.cut.tenant_id
        or reply.responder != sent.callee
        or reply.request_id != sent.request_id
        or reply.schema_id != DIRECT_TERMINAL_RESULT_SCHEMA
        or reply.canonical_payload != result.canonical_bytes()
    ):
        raise ValueError("accepted/direct terminal retained owner exchange differs")
    if request.command_id != "accepted-direct-terminal:" + request.cut.cut_fingerprint:
        raise ValueError("accepted/direct terminal command identity differs from cut")

    accounting: AcceptedDirectTerminalAccountingV1 = result.accounting
    terminal: AcceptedDirectTerminalDispositionV1 = result.terminal
    terminal_result: AcceptedDirectTerminalResultV1 = result.result
    records = (
        PhysicalRecord(
            accounting.accounting_id,
            "agent_loop",
            ACCEPTED_DIRECT_TERMINAL_ACCOUNTING_SCHEMA,
            accounting.canonical_bytes(),
            accounting.digest(),
        ),
        PhysicalRecord(
            terminal.terminal_id,
            "agent_loop",
            ACCEPTED_DIRECT_TERMINAL_SCHEMA,
            terminal.canonical_bytes(),
            terminal.digest(),
        ),
        PhysicalRecord(
            terminal_result.result_id,
            "agent_loop",
            ACCEPTED_DIRECT_TERMINAL_RESULT_SCHEMA,
            terminal_result.canonical_bytes(),
            terminal_result.digest(),
        ),
    )
    return PhysicalPublicationCommand(
        tenant_id=request.cut.tenant_id,
        operation_kind=ACCEPTED_DIRECT_TERMINAL_OPERATION,
        idempotency_key=request.command_id,
        request_fingerprint=evidence.digest(),
        expected_head=request.cut.tenant_commit_sequence,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=records,
    )


def retained_accepted_direct_terminal(
    entry: dict[str, object],
) -> RetainedAcceptedDirectTerminalPublication:
    raw = entry.get("accepted_direct_terminal_evidence")
    if not isinstance(raw, str):
        raise ValueError("accepted/direct terminal selected evidence is absent")
    evidence = RetainedAcceptedDirectTerminalPublication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("accepted/direct terminal selected evidence is noncanonical")
    return evidence


__all__ = [
    "ACCEPTED_DIRECT_TERMINAL_ACCOUNTING_SCHEMA",
    "ACCEPTED_DIRECT_TERMINAL_OPERATION",
    "ACCEPTED_DIRECT_TERMINAL_OWNER_OPERATION",
    "ACCEPTED_DIRECT_TERMINAL_RESULT_SCHEMA",
    "ACCEPTED_DIRECT_TERMINAL_SCHEMA",
    "RetainedAcceptedDirectTerminalPublication",
    "accepted_direct_terminal_command",
    "retained_accepted_direct_terminal",
]
