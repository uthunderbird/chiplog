"""Captured fan-out inputs and proposals; supplied evidence grants no authority."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from .call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
    FanOutPreparationRequest,
    PreparedCallFanOut,
)
from .contracts import RunRecord
from .recovery_contracts import Digest, Identity, NotApplicable, RecoveryDTO


class ReadOnlyFanOutPolicy(RecoveryDTO):
    kind: Literal["READ_ONLY_FAN_OUT_POLICY_V1"] = "READ_ONLY_FAN_OUT_POLICY_V1"
    max_attempts: Annotated[int, Field(gt=0, le=2**64 - 1)]
    budget_version: Identity
    reducer_id: Identity
    reducer_version: Identity


class FanOutToolPolicy(RecoveryDTO):
    tool_name: Identity
    tool_version: Identity
    schema_id: Identity
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    classification: Literal["PROPOSAL_ONLY", "CONSEQUENTIAL", "READ_ONLY"]
    retry_policy: Annotated[NotApplicable | ReadOnlyFanOutPolicy, Field(discriminator="kind")]


class FanOutToolRegistry(RecoveryDTO):
    kind: Literal["FAN_OUT_TOOL_REGISTRY_V1"] = "FAN_OUT_TOOL_REGISTRY_V1"
    registry_id: Identity
    version: Identity
    entries: tuple[FanOutToolPolicy, ...] = Field(min_length=1)


class CapturedFanOutRequest(RecoveryDTO):
    kind: Literal["PREPARE_CAPTURED_CALL_FAN_OUT_V1"] = "PREPARE_CAPTURED_CALL_FAN_OUT_V1"
    request: FanOutPreparationRequest
    captured_run: RunRecord
    tool_registry: FanOutToolRegistry
    tool_registry_head: CallSubjectHead


class CapturedFanOutProposal(RecoveryDTO):
    kind: Literal["PREPARED_CAPTURED_CALL_FAN_OUT_V1"] = "PREPARED_CAPTURED_CALL_FAN_OUT_V1"
    source_request_fingerprint: Digest
    fan_out: PreparedCallFanOut
    proposal_fingerprint: Digest


CapturedFanOutResult = Annotated[
    CapturedFanOutProposal | CallPreparationRejected, Field(discriminator="kind")
]


class CapturedFanOutPreparationPort(Protocol):
    async def prepare_captured_fan_out(
        self, request: CapturedFanOutRequest
    ) -> CapturedFanOutResult: ...
