"""Retained transition exchange and exact physical command, without write authority."""

import hashlib
import json
from typing import Literal

from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    CreateExecutionRun,
    ExecutionTransitionProposal,
    ExecutionTransitionRequest,
    PrepareExecutionRequest,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.broker import BrokerSession

from .r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
from .r14_execution_transition_verification import verify_execution_transition
from .r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1

EXECUTION_TRANSITION_OPERATION = "agent_loop.execution-transition.v2"


class ExecutionHistorySnapshot(RecoveryDTO):
    """Full ordered Run projection, retaining legacy and executable schemas explicitly."""

    tenant_head: UInt64
    records: tuple[RunRecord | ExecutionRunRecord, ...]


class RetainedExecutionTransition(RecoveryDTO):
    kind: Literal["R14_SELECTED_EXECUTION_TRANSITION_V2"] = "R14_SELECTED_EXECUTION_TRANSITION_V2"
    request: ExecutionTransitionRequest
    proposal: ExecutionTransitionProposal
    expected_head: UInt64
    predecessor_commitment: Digest
    expected_snapshot_fingerprint: Digest
    caller: BrokerSession
    callee: BrokerSession
    request_id: Identity
    deadline_ns: UInt64
    response_schema: Literal["chiplog.execution.transition-result.v2"] = (
        "chiplog.execution.transition-result.v2"
    )


class RetainedExecutionTransitionV3(RecoveryDTO):
    """Selected H1 Prepare evidence; native owner command remains V2.

    This is deliberately a new retained envelope rather than an optional V2
    field.  The physical publication and the native Run continue to use their
    registered V2 encodings.
    """

    kind: Literal["R14_SELECTED_EXECUTION_TRANSITION_V3"] = "R14_SELECTED_EXECUTION_TRANSITION_V3"
    request: ExecutionTransitionRequest
    proposal: ExecutionTransitionProposal
    expected_head: UInt64
    predecessor_commitment: Digest
    expected_snapshot_fingerprint: Digest
    caller: BrokerSession
    callee: BrokerSession
    request_id: Identity
    deadline_ns: UInt64
    response_schema: Literal["chiplog.execution.transition-result.v2"] = (
        "chiplog.execution.transition-result.v2"
    )
    workspace_issuance: H1WorkspaceIssuanceRefV1


RetainedExecutionTransitionEvidence = RetainedExecutionTransition | RetainedExecutionTransitionV3


def transition_command(evidence: RetainedExecutionTransitionEvidence) -> PhysicalPublicationCommand:
    raw = evidence.canonical_bytes()
    kind = json.loads(raw).get("kind")
    if kind == "R14_SELECTED_EXECUTION_TRANSITION_V2":
        evidence = RetainedExecutionTransition.model_validate_json(raw)
    elif kind == "R14_SELECTED_EXECUTION_TRANSITION_V3":
        evidence = RetainedExecutionTransitionV3.model_validate_json(raw)
        if not isinstance(evidence.request, PrepareExecutionRequest):
            raise ValueError("V3 retained execution transition must prepare")
    else:
        raise ValueError("unregistered retained execution transition")
    verify_execution_transition(evidence.request, evidence.proposal)
    run = evidence.proposal.run
    caller, callee = evidence.caller, evidence.callee
    if (
        caller.owner_id != "broker"
        or callee.owner_id != "agent_loop"
        or caller.tenant_id != run.tenant
        or callee.tenant_id != run.tenant
        or caller.broker_epoch != callee.broker_epoch
        or caller.generation_id != callee.generation_id
        or not caller.session_id
        or not callee.session_id
        or not callee.generation_id
        or run.worker_session != f"{callee.broker_epoch}:{callee.generation_id}:{callee.session_id}"
        or evidence.deadline_ns == 0
    ):
        raise ValueError("retained execution exchange identity differs")
    if (
        not isinstance(evidence.request, CreateExecutionRun)
        and evidence.request.run.tenant != run.tenant
    ):
        raise ValueError("foreign retained execution predecessor")
    raw = run.canonical_bytes()
    return PhysicalPublicationCommand(
        run.tenant,
        EXECUTION_TRANSITION_OPERATION,
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
