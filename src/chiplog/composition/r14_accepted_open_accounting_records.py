"""Retained selected envelope for effects-specific accepted/open accounting."""

from __future__ import annotations

from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    AcceptedOpenAccountingRecordV1,
    PrepareAcceptedOpenAccounting,
    PrepareAcceptedOpenRecoveryV1,
    PreparedAcceptedOpenAccounting,
    PreparedAcceptedOpenRecoveryV1,
)
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.agent_loop.recovery_wire_contracts import (
    EXECUTION_RECOVERY_REQUEST_SCHEMA,
    EXECUTION_RECOVERY_RESULT_SCHEMA,
)
from chiplog.composition.r14_recovery_publication import RecoveryActorBudgetV1
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

ACCEPTED_OPEN_ACCOUNTING_OPERATION = "agent_loop.accepted-open-accounting.v1"
ACCEPTED_OPEN_ACCOUNTING_OWNER_OPERATION = "agent_loop.prepare_accepted_open_accounting.v1"
ACCEPTED_OPEN_ACCOUNTING_SCHEMA = "chiplog.execution.accepted-open-accounting.v1"
ACCEPTED_OPEN_RECOVERY_OPERATION = "agent_loop.accepted-open-recovery.v1"
ACCEPTED_OPEN_RECOVERY_OWNER_OPERATION = "agent_loop.prepare_accepted_open_recovery.v1"


class RetainedAcceptedOpenAccountingPublication(RecoveryDTO):
    """One owner-prepared account record, with no continuation-ready companion."""

    request: PrepareAcceptedOpenAccounting
    result: PreparedAcceptedOpenAccounting
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


class RetainedAcceptedOpenRecoveryPublication(RecoveryDTO):
    """Retained exchange for the atomic account, terminal and obligation batch."""

    request: PrepareAcceptedOpenRecoveryV1
    result: PreparedAcceptedOpenRecoveryV1
    owner_call: PublicPortCall
    owner_reply: PublicPortSuccess
    actor_budget_v1: RecoveryActorBudgetV1


def accepted_open_accounting_command(
    evidence: RetainedAcceptedOpenAccountingPublication,
) -> PhysicalPublicationCommand:
    """Reconstruct the sole physical record only from the retained owner exchange."""
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
        prepare_accepted_open_accounting,
    )
    from chiplog.composition.r14_recovery_publication import validate_recovery_actor_budget

    request, result = evidence.request, evidence.result
    if (
        prepare_accepted_open_accounting(request) != result
        or result.source_request_fingerprint != request.digest()
        or result.accounting.kind != "RECOVERY_REQUIRED"
    ):
        raise ValueError("accepted/open owner result differs from exact request")
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if evidence.actor_budget_v1.request.callee.tenant_id != request.cut.tenant_id:
        raise ValueError("accepted/open actor budget tenant differs from source cut")
    sent, reply = evidence.owner_call, evidence.owner_reply
    if (
        sent.operation_id != ACCEPTED_OPEN_ACCOUNTING_OWNER_OPERATION
        or sent.schema_id != EXECUTION_RECOVERY_REQUEST_SCHEMA
        or sent.canonical_payload != request.canonical_bytes()
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "agent_loop"
        or sent.caller.tenant_id != request.cut.tenant_id
        or sent.callee.tenant_id != request.cut.tenant_id
        or reply.responder != sent.callee
        or reply.request_id != sent.request_id
        or reply.schema_id != EXECUTION_RECOVERY_RESULT_SCHEMA
        or reply.canonical_payload != result.canonical_bytes()
    ):
        raise ValueError("accepted/open retained owner exchange differs")
    accounting: AcceptedOpenAccountingRecordV1 = result.accounting
    expected_identity = "accepted-open-accounting:" + request.cut.cut_fingerprint
    if request.command_id != expected_identity:
        raise ValueError("accepted/open command identity differs from cut")
    return PhysicalPublicationCommand(
        tenant_id=request.cut.tenant_id,
        operation_kind=ACCEPTED_OPEN_ACCOUNTING_OPERATION,
        idempotency_key=request.command_id,
        request_fingerprint=evidence.digest(),
        expected_head=request.cut.tenant_commit_sequence,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                accounting.accounting_id,
                "agent_loop",
                ACCEPTED_OPEN_ACCOUNTING_SCHEMA,
                accounting.canonical_bytes(),
                accounting.digest(),
            ),
        ),
    )


def accepted_open_recovery_command(
    evidence: RetainedAcceptedOpenRecoveryPublication,
) -> PhysicalPublicationCommand:
    """Rebuild the exact three-record accepted/open terminal publication."""
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
        prepare_accepted_open_recovery_v1,
    )
    from chiplog.composition.r14_recovery_publication import validate_recovery_actor_budget

    request, result = evidence.request, evidence.result
    if (
        prepare_accepted_open_recovery_v1(request) != result
        or result.source_request_fingerprint != request.digest()
        or result.accounting.kind != "RECOVERY_REQUIRED"
        or result.terminal.original_call_id != result.accounting.original_call_id
        or result.opening.original.original_call_id != result.accounting.original_call_id
    ):
        raise ValueError("accepted/open recovery owner result differs from exact request")
    validate_recovery_actor_budget(evidence.actor_budget_v1)
    if evidence.actor_budget_v1.request.callee.tenant_id != request.cut.tenant_id:
        raise ValueError("accepted/open recovery actor budget tenant differs from source cut")
    sent, reply = evidence.owner_call, evidence.owner_reply
    if (
        sent.operation_id != ACCEPTED_OPEN_RECOVERY_OWNER_OPERATION
        or sent.schema_id != EXECUTION_RECOVERY_REQUEST_SCHEMA
        or sent.canonical_payload != request.canonical_bytes()
        or sent.caller.owner_id != "broker"
        or sent.callee.owner_id != "agent_loop"
        or sent.caller.tenant_id != request.cut.tenant_id
        or sent.callee.tenant_id != request.cut.tenant_id
        or reply.responder != sent.callee
        or reply.request_id != sent.request_id
        or reply.schema_id != EXECUTION_RECOVERY_RESULT_SCHEMA
        or reply.canonical_payload != result.canonical_bytes()
    ):
        raise ValueError("accepted/open recovery retained owner exchange differs")
    expected_identity = "accepted-open-recovery:" + request.cut.cut_fingerprint
    if request.command_id != expected_identity:
        raise ValueError("accepted/open recovery command identity differs from cut")
    records = (
        PhysicalRecord(
            result.accounting.accounting_id,
            "agent_loop",
            ACCEPTED_OPEN_ACCOUNTING_SCHEMA,
            result.accounting.canonical_bytes(),
            result.accounting.digest(),
        ),
        PhysicalRecord(
            result.opening.opening_id,
            "agent_loop",
            result.opening.schema_id,
            result.opening.canonical_bytes(),
            result.opening.digest(),
        ),
        PhysicalRecord(
            result.terminal.terminal_id,
            "agent_loop",
            result.terminal.schema_id,
            result.terminal.canonical_bytes(),
            result.terminal.digest(),
        ),
    )
    return PhysicalPublicationCommand(
        tenant_id=request.cut.tenant_id,
        operation_kind=ACCEPTED_OPEN_RECOVERY_OPERATION,
        idempotency_key=request.command_id,
        request_fingerprint=evidence.digest(),
        expected_head=request.cut.tenant_commit_sequence,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=records,
    )


def retained_accepted_open_accounting(
    entry: dict[str, object],
) -> RetainedAcceptedOpenAccountingPublication:
    raw = entry.get("accepted_open_accounting_evidence")
    if not isinstance(raw, str):
        raise ValueError("accepted/open selected evidence is absent")
    evidence = RetainedAcceptedOpenAccountingPublication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("accepted/open selected evidence is noncanonical")
    return evidence


def retained_accepted_open_recovery(
    entry: dict[str, object],
) -> RetainedAcceptedOpenRecoveryPublication:
    raw = entry.get("accepted_open_recovery_evidence")
    if not isinstance(raw, str):
        raise ValueError("accepted/open recovery selected evidence is absent")
    evidence = RetainedAcceptedOpenRecoveryPublication.model_validate_json(raw)
    if evidence.canonical_bytes().decode() != raw:
        raise ValueError("accepted/open recovery selected evidence is noncanonical")
    return evidence


__all__ = [
    "ACCEPTED_OPEN_ACCOUNTING_OPERATION",
    "ACCEPTED_OPEN_ACCOUNTING_OWNER_OPERATION",
    "ACCEPTED_OPEN_ACCOUNTING_SCHEMA",
    "ACCEPTED_OPEN_RECOVERY_OPERATION",
    "ACCEPTED_OPEN_RECOVERY_OWNER_OPERATION",
    "RetainedAcceptedOpenAccountingPublication",
    "RetainedAcceptedOpenRecoveryPublication",
    "accepted_open_accounting_command",
    "accepted_open_recovery_command",
    "retained_accepted_open_accounting",
    "retained_accepted_open_recovery",
]
