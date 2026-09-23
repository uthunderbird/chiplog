"""Closed owner commands for execution initialization and model transport.

Requests are inert. In particular, CAPTURE transports already received bytes;
it supplies no proof that a model request was durably emitted or authenticated.
"""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import CallPreparationRejected
from .contracts import BudgetPolicy, VisibilityMember
from .delivery_contracts import OriginSelection
from .execution_contracts import ExecutionRunRecord, ExecutionVisibilityManifest
from .recovery_contracts import Digest, Identity, RecoveryDTO


class CreateExecutionRun(RecoveryDTO):
    kind: Literal["CREATE_EXECUTION_RUN_V2"] = "CREATE_EXECUTION_RUN_V2"
    command_id: Identity
    tenant: Identity
    principal: Identity
    run_id: Identity
    prompt: Identity
    policy: BudgetPolicy
    origin: OriginSelection
    contour_head: Identity
    policy_head: Identity
    worker_session: Identity


class ActivateExecutionRun(RecoveryDTO):
    kind: Literal["ACTIVATE_EXECUTION_RUN_V2"] = "ACTIVATE_EXECUTION_RUN_V2"
    command_id: Identity
    run: ExecutionRunRecord


class StartInitialExecutionTurn(RecoveryDTO):
    kind: Literal["START_INITIAL_EXECUTION_TURN_V2"] = "START_INITIAL_EXECUTION_TURN_V2"
    command_id: Identity
    run: ExecutionRunRecord


class AccumulateExecutionVisibility(RecoveryDTO):
    kind: Literal["ACCUMULATE_EXECUTION_VISIBILITY_V2"] = "ACCUMULATE_EXECUTION_VISIBILITY_V2"
    command_id: Identity
    run: ExecutionRunRecord
    members: tuple[VisibilityMember, ...]


class PrepareExecutionRequest(RecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_REQUEST_V2"] = "PREPARE_EXECUTION_REQUEST_V2"
    command_id: Identity
    run: ExecutionRunRecord
    manifest: ExecutionVisibilityManifest


class EmitExecutionAttempt(RecoveryDTO):
    kind: Literal["EMIT_EXECUTION_ATTEMPT_V2"] = "EMIT_EXECUTION_ATTEMPT_V2"
    command_id: Identity
    run: ExecutionRunRecord


class CaptureExecutionResponse(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["CAPTURE_EXECUTION_RESPONSE_V2"] = "CAPTURE_EXECUTION_RESPONSE_V2"
    command_id: Identity
    run: ExecutionRunRecord
    raw: bytes
    receipt: Identity


ExecutionTransitionRequest = Annotated[
    CreateExecutionRun
    | ActivateExecutionRun
    | StartInitialExecutionTurn
    | AccumulateExecutionVisibility
    | PrepareExecutionRequest
    | EmitExecutionAttempt
    | CaptureExecutionResponse,
    Field(discriminator="kind"),
]


class ExecutionTransitionProposal(RecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_TRANSITION_V2"] = "PREPARED_EXECUTION_TRANSITION_V2"
    source_request_fingerprint: Digest
    run: ExecutionRunRecord
    proposal_fingerprint: Digest


ExecutionTransitionResult = Annotated[
    ExecutionTransitionProposal | CallPreparationRejected, Field(discriminator="kind")
]
