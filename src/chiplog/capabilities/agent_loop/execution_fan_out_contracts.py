"""Executable capture preparation boundary; inert inputs confer no writer authority.

The owner returns the sealed Run, complete initialized set and response seal,
never proposal terminals. Composition cannot reconstruct the Run transition.
For Complete the returned Run remains completion-pending: its selected attempt
stays RESPONSE_CAPTURED and its Turn stays unaccepted until CompleteAcceptance.
Composition must independently authenticate the capture, registry and current cut
before publishing the complete envelope. Legacy fan-out schemas remain unchanged.
"""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    FanOutPreparationRequest,
    PreparedCallFanOut,
)
from .execution_contracts import ExecutionRunRecord
from .fan_out_contracts import FanOutToolRegistry
from .recovery_contracts import Digest, RecoveryDTO


class ExecutionCapturedFanOutRequest(RecoveryDTO):
    kind: Literal["PREPARE_EXECUTION_CAPTURED_FAN_OUT_V2"] = "PREPARE_EXECUTION_CAPTURED_FAN_OUT_V2"
    request: FanOutPreparationRequest
    captured_run: ExecutionRunRecord
    tool_registry: FanOutToolRegistry
    tool_registry_head: CallSubjectHead


class ExecutionCapturedFanOutProposal(RecoveryDTO):
    kind: Literal["PREPARED_EXECUTION_CAPTURED_FAN_OUT_V2"] = (
        "PREPARED_EXECUTION_CAPTURED_FAN_OUT_V2"
    )
    source_request_fingerprint: Digest
    fan_out: PreparedCallFanOut
    sealed_run: ExecutionRunRecord
    proposal_fingerprint: Digest


ExecutionCapturedFanOutResult = Annotated[
    ExecutionCapturedFanOutProposal | CallPreparationRejected, Field(discriminator="kind")
]


class ExecutionCapturedFanOutPreparationPort(Protocol):
    async def prepare_execution_captured_fan_out(
        self, request: ExecutionCapturedFanOutRequest
    ) -> ExecutionCapturedFanOutResult: ...
