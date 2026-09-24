"""Retained owner exchange for one selected CLI inbox initialization.

The retained wrapper is evidence for the broker publication.  The sole physical
output remains the native execution Run, so ordinary Run history stays readable.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    AdmittedExecutionBinding,
    PreparedExecutionInitialization,
    PrepareInboxExecution,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import BrokerSession

from .r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA

EXECUTION_INBOX_INITIALIZATION_OPERATION = "agent_loop.inbox-initialization.v1"


class RetainedInboxExecutionInitialization(RecoveryDTO):
    """Exact selected input, owner exchange, and native Run publication cut."""

    kind: Literal["R17_SELECTED_INBOX_EXECUTION_INITIALIZATION_V1"] = (
        "R17_SELECTED_INBOX_EXECUTION_INITIALIZATION_V1"
    )
    driver_request_bytes: bytes
    driver_request_fingerprint: Digest
    dispatch_grant_bytes: bytes
    dispatch_credential_bytes: bytes
    dispatch_endpoint_bytes: bytes
    dispatch_clock_epoch: Identity
    dispatch_signature: Identity
    request: PrepareInboxExecution
    proposal: PreparedExecutionInitialization
    expected_head: UInt64
    predecessor_commitment: Digest
    caller: BrokerSession
    callee: BrokerSession
    request_id: Identity
    deadline_ns: UInt64
    response_schema: Literal["chiplog.execution.inbox-initialization-result.v1"] = (
        "chiplog.execution.inbox-initialization-result.v1"
    )


def inbox_initialization_command(
    evidence: RetainedInboxExecutionInitialization,
) -> PhysicalPublicationCommand:
    evidence = RetainedInboxExecutionInitialization.model_validate_json(evidence.canonical_bytes())
    request, proposal = evidence.request, evidence.proposal
    run = proposal.run
    if (
        request.create.run_id != run.run_id
        or request.create.tenant != run.tenant
        or request.create.prompt != run.prompt
        or request.create.origin != run.origin
        or request.create.worker_session != run.worker_session
        or proposal.source_request_fingerprint != request.digest()
        or evidence.driver_request_fingerprint
        != hashlib.sha256(
            b"chiplog.common-execution-driver.request.v1\x00" + evidence.driver_request_bytes
        ).hexdigest()
        or not evidence.dispatch_grant_bytes
        or not evidence.dispatch_credential_bytes
        or not evidence.dispatch_endpoint_bytes
        or not evidence.dispatch_clock_epoch
        or not evidence.dispatch_signature
    ):
        raise ValueError("retained inbox initialization differs from native Run")
    if not isinstance(proposal.input_binding, AdmittedExecutionBinding) or (
        proposal.input_binding.original_inbox != request.admitted.inbox
        or proposal.input_binding.original_custody != request.admitted.custody
        or proposal.input_binding.normalization != request.admitted.normalization
    ):
        raise ValueError("retained inbox initialization binding differs")
    caller, callee = evidence.caller, evidence.callee
    if (
        caller.owner_id != "broker"
        or callee.owner_id != "agent_loop"
        or caller.tenant_id != callee.tenant_id
        or caller.tenant_id != run.tenant
        or caller.broker_epoch != callee.broker_epoch
        or caller.generation_id != callee.generation_id
        or not caller.session_id
        or not callee.session_id
        or evidence.deadline_ns == 0
    ):
        raise ValueError("retained inbox owner exchange identity differs")
    raw = run.canonical_bytes()
    return PhysicalPublicationCommand(
        run.tenant,
        EXECUTION_INBOX_INITIALIZATION_OPERATION,
        run.head,
        evidence.digest(),
        evidence.expected_head,
        "r6",
        0,
        0,
        (
            PhysicalRecord(
                run.head, "agent_loop", EXECUTION_RUN_SCHEMA, raw, hashlib.sha256(raw).hexdigest()
            ),
        ),
    )
