"""V3 capture fan-out containment; classification and execution happen later."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    FanOutPreparationRequest,
    PreparedCallFanOut,
)
from .execution_history_contracts import ExecutionRunRecordV3
from .fan_out_contracts import FanOutToolRegistry
from .recovery_contracts import Digest, RecoveryDTO


class ExecutionCapturedFanOutRequestV3(RecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_CAPTURED_FAN_OUT_V3"] = "PREPARE_EXECUTION_CAPTURED_FAN_OUT_V3"
    request: FanOutPreparationRequest
    captured_run: ExecutionRunRecordV3
    tool_registry: FanOutToolRegistry
    tool_registry_head: CallSubjectHead


class ExecutionCapturedFanOutProposalV3(RecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_CAPTURED_FAN_OUT_V3"] = (
        "PREPARED_EXECUTION_CAPTURED_FAN_OUT_V3"
    )
    source_request_fingerprint: Digest
    fan_out: PreparedCallFanOut
    sealed_run: ExecutionRunRecordV3
    proposal_fingerprint: Digest


ExecutionCapturedFanOutResultV3 = Annotated[
    ExecutionCapturedFanOutProposalV3 | CallPreparationRejected, Field(discriminator="kind")
]


class ExecutionCapturedFanOutPreparationV3Port(Protocol):
    async def prepare_execution_captured_fan_out_v3(
        self, request: ExecutionCapturedFanOutRequestV3
    ) -> ExecutionCapturedFanOutResultV3: ...
