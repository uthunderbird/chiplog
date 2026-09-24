"""Executable model-attempt recovery wire, distinct from selected capture.

All proofs are untrusted observations until independently authenticated by the
broker and checked by the owner. Timeout or possible emission never suffices.
Legacy NoExposureProof and ExecutionTransitionRequest remain unchanged.
"""

from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .execution_run_versions import ExecutionRun
from .recovery_contracts import Digest, Identity, RecoveryDTO, RunExecutionFence, UInt64


class RegisteredModelNoExposure(RecoveryDTO):
    """Exact registered pre-emission CAS, not an assertion that a timeout is safe.

    The proof covers the original attempt, immutable request and manifest, and current
    selector. Its independently retained source must establish no emitted request
    bytes. A later emitter race invalidates fresh replacement at the writer cut.
    """

    kind: Literal["REGISTERED_MODEL_PRE_EMISSION_CAS_V1"] = "REGISTERED_MODEL_PRE_EMISSION_CAS_V1"
    registry: CallSubjectHead
    proof: CallSubjectHead
    original_run: CallSubjectHead
    original_attempt: CallSubjectHead
    lineage_id: Identity
    selector_generation: UInt64
    immutable_request: CallSubjectHead
    visibility_manifest: CallSubjectHead
    provider_contract: Identity
    recipient: Identity
    observed_emission_head: CallSubjectHead
    source_schema: Identity
    canonical_source_bytes: bytes = Field(min_length=1)
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


class ReplaceExecutionModelAttempt(RecoveryDTO):
    kind: Literal["REPLACE_EXECUTION_MODEL_ATTEMPT_V1"] = "REPLACE_EXECUTION_MODEL_ATTEMPT_V1"
    command_id: Identity
    run: ExecutionRun
    selected_attempt: CallSubjectHead
    expected_selector: UInt64
    no_exposure: RegisteredModelNoExposure
    fence: RunExecutionFence


class RetainLateExecutionResponse(RecoveryDTO):
    """Independent evidence ingress; deliberately no live-worker lease requirement.

    The original superseded/terminal attempt stays immutable. The broker must bind
    actual source authentication and raw custody; supplied refs grant no authority.
    """

    kind: Literal["RETAIN_LATE_EXECUTION_RESPONSE_V1"] = "RETAIN_LATE_EXECUTION_RESPONSE_V1"
    command_id: Identity
    original_run: CallSubjectHead
    original_attempt: CallSubjectHead
    original_manifest: CallSubjectHead
    lineage_id: Identity
    generation: UInt64
    receipt_token: CallSubjectHead
    selected_custody: CallSubjectHead
    source_authentication: CallSubjectHead
    raw_response: bytes = Field(min_length=1)
    transport_receipt: bytes = Field(min_length=1)
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")


ModelAttemptRecoveryRequest = Annotated[
    ReplaceExecutionModelAttempt | RetainLateExecutionResponse, Field(discriminator="kind")
]


class PreparedModelAttemptReplacement(RecoveryDTO):
    kind: Literal["PREPARED_MODEL_ATTEMPT_REPLACEMENT_V1"] = "PREPARED_MODEL_ATTEMPT_REPLACEMENT_V1"
    source_request_fingerprint: Digest
    original_attempt: CallSubjectHead
    superseded_attempt: CallSubjectHead
    replacement_attempt: CallSubjectHead
    run: ExecutionRun
    proposal_fingerprint: Digest


class LateExecutionResponseRecord(RecoveryDTO):
    kind: Literal["LATE_EXECUTION_RESPONSE_RECORD_V1"] = "LATE_EXECUTION_RESPONSE_RECORD_V1"
    evidence_id: Identity
    request: RetainLateExecutionResponse


class PreparedLateExecutionResponse(RecoveryDTO):
    kind: Literal["PREPARED_LATE_EXECUTION_RESPONSE_V1"] = "PREPARED_LATE_EXECUTION_RESPONSE_V1"
    source_request_fingerprint: Digest
    record: LateExecutionResponseRecord
    proposal_fingerprint: Digest


ModelAttemptRecoveryResult = Annotated[
    PreparedModelAttemptReplacement | PreparedLateExecutionResponse | CallPreparationRejected,
    Field(discriminator="kind"),
]


class ModelAttemptRecoveryPreparationPort(Protocol):
    async def prepare_model_recovery(
        self, request: ModelAttemptRecoveryRequest
    ) -> ModelAttemptRecoveryResult: ...
