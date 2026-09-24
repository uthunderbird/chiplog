"""Closed v3 executable-history transition route; runtime remains a later concern."""

from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import CallPreparationRejected
from .contracts import BudgetPolicy, VisibilityMember
from .delivery_contracts import OriginSelection
from .execution_history_contracts import ExecutionRunRecordV3, ExecutionVisibilityManifestV3
from .recovery_contracts import Digest, Identity, RecoveryDTO


class CreateExecutionRunV3(RecoveryDTO):
    kind: Literal["CREATE_EXECUTION_RUN_V3"] = "CREATE_EXECUTION_RUN_V3"
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


class ActivateExecutionRunV3(RecoveryDTO):
    kind: Literal["ACTIVATE_EXECUTION_RUN_V3"] = "ACTIVATE_EXECUTION_RUN_V3"
    command_id: Identity
    run: ExecutionRunRecordV3


class StartInitialExecutionTurnV3(RecoveryDTO):
    kind: Literal["START_INITIAL_EXECUTION_TURN_V3"] = "START_INITIAL_EXECUTION_TURN_V3"
    command_id: Identity
    run: ExecutionRunRecordV3


class AccumulateExecutionVisibilityV3(RecoveryDTO):
    kind: Literal["ACCUMULATE_EXECUTION_VISIBILITY_V3"] = "ACCUMULATE_EXECUTION_VISIBILITY_V3"
    command_id: Identity
    run: ExecutionRunRecordV3
    members: tuple[VisibilityMember, ...]


class PrepareExecutionRequestV3(RecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_REQUEST_V3"] = "PREPARE_EXECUTION_REQUEST_V3"
    command_id: Identity
    run: ExecutionRunRecordV3
    manifest: ExecutionVisibilityManifestV3


class EmitExecutionAttemptV3(RecoveryDTO):
    kind: Literal["EMIT_EXECUTION_ATTEMPT_V3"] = "EMIT_EXECUTION_ATTEMPT_V3"
    command_id: Identity
    run: ExecutionRunRecordV3


class CaptureExecutionResponseV3(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["CAPTURE_EXECUTION_RESPONSE_V3"] = "CAPTURE_EXECUTION_RESPONSE_V3"
    command_id: Identity
    run: ExecutionRunRecordV3
    raw: bytes
    receipt: Identity


ExecutionTransitionRequestV3 = Annotated[
    CreateExecutionRunV3
    | ActivateExecutionRunV3
    | StartInitialExecutionTurnV3
    | AccumulateExecutionVisibilityV3
    | PrepareExecutionRequestV3
    | EmitExecutionAttemptV3
    | CaptureExecutionResponseV3,
    Field(discriminator="kind"),
]


class ExecutionTransitionProposalV3(RecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_TRANSITION_V3"] = "PREPARED_EXECUTION_TRANSITION_V3"
    source_request_fingerprint: Digest
    run: ExecutionRunRecordV3
    proposal_fingerprint: Digest


ExecutionTransitionResultV3 = Annotated[
    ExecutionTransitionProposalV3 | CallPreparationRejected, Field(discriminator="kind")
]
