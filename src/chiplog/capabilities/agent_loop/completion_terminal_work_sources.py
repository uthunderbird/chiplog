"""Registered completion-only interpretations of terminal-work source bytes."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .completion_owner_record_contracts import completion_request_fingerprint
from .execution_completion_contracts import (
    PreparedExecutionCompletion,
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from .execution_recovery_observations import ExecutionTerminalManifest
from .execution_run_versions import ExecutionRun
from .recovery_contracts import OriginalObligationBinding, RecoveryDTO
from .rejected_completion_terminalization_contracts import (
    PreparedRejectedCompletionTerminalizationV1,
    PrepareRejectedCompletionTerminalizationV1,
    manifest_ref,
    run_ref,
    validate_rejected_terminalization_exchange,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _exact(raw: bytes, model: type[RecoveryDTO]) -> RecoveryDTO:
    decoded = model.model_validate_json(raw)
    if decoded.canonical_bytes() != raw:
        raise ValueError("completion source contains noncanonical owner bytes")
    return decoded


class AcceptedCompletionWorkSourceV1(RecoveryDTO):
    kind: Literal["ACCEPTED_COMPLETION_WORK_SOURCE_V1"] = "ACCEPTED_COMPLETION_WORK_SOURCE_V1"
    schema_id: Literal["chiplog.agent-loop.accepted-completion-work-source.v1"] = (
        "chiplog.agent-loop.accepted-completion-work-source.v1"
    )
    original_completion_request_bytes: bytes = Field(min_length=1)
    prepared_completion_bytes: bytes = Field(min_length=1)
    terminal_run: ExecutionRun
    terminal_run_head: CallSubjectHead
    terminal_manifest: ExecutionTerminalManifest
    terminal_manifest_head: CallSubjectHead
    ordered_open_obligations: tuple[OriginalObligationBinding, ...]

    @model_validator(mode="after")
    def accepted_exchange_matches(self) -> Self:
        request = _exact(self.original_completion_request_bytes, PrepareExecutionCompletion)
        prepared = _exact(self.prepared_completion_bytes, PreparedExecutionCompletion)
        assert isinstance(request, PrepareExecutionCompletion)
        assert isinstance(prepared, PreparedExecutionCompletion)
        if (
            prepared.source_request_fingerprint != completion_request_fingerprint(request)
            or prepared.complete_earlier_continuations != request.complete_earlier_continuations
            or prepared.run != self.terminal_run
            or prepared.terminal_manifest != self.terminal_manifest
            or self.terminal_run_head != run_ref(self.terminal_run)
            or self.terminal_manifest_head != manifest_ref(self.terminal_manifest)
            or self.terminal_run.state != "SUCCEEDED"
            or self.terminal_manifest.target != "SUCCEEDED"
            or self.terminal_manifest.command_id != request.command_id
            or self.terminal_manifest.prior_run != run_ref(request.run)
            or self.terminal_manifest.source_cut_fingerprint != request.cut.digest()
            or self.terminal_run.schema_id != request.run.schema_id
            or self.terminal_run.tenant != request.run.tenant
            or self.terminal_run.principal != request.run.principal
            or self.terminal_run.run_id != request.run.run_id
            or self.terminal_run.predecessor != request.run.head
            or self.ordered_open_obligations
            != self.terminal_manifest.complete_open_original_obligations
        ):
            raise ValueError("accepted work source differs from completion exchange")
        return self


class RejectedCompletionWorkSourceV1(RecoveryDTO):
    kind: Literal["REJECTED_COMPLETION_WORK_SOURCE_V1"] = "REJECTED_COMPLETION_WORK_SOURCE_V1"
    schema_id: Literal["chiplog.agent-loop.rejected-completion-work-source.v1"] = (
        "chiplog.agent-loop.rejected-completion-work-source.v1"
    )
    original_completion_request_bytes: bytes = Field(min_length=1)
    original_completion_reject_bytes: bytes = Field(min_length=1)
    rejected_terminalization_request_bytes: bytes = Field(min_length=1)
    rejected_terminalization_result_bytes: bytes = Field(min_length=1)
    terminal_manifest: ExecutionTerminalManifest
    terminal_manifest_head: CallSubjectHead
    terminal_run: ExecutionRun
    terminal_run_head: CallSubjectHead
    ordered_open_obligations: tuple[OriginalObligationBinding, ...]

    @model_validator(mode="after")
    def rejected_exchange_matches(self) -> Self:
        request = _exact(self.original_completion_request_bytes, PrepareExecutionCompletion)
        rejected = _exact(self.original_completion_reject_bytes, PreparedExecutionCompletionReject)
        terminalization = _exact(
            self.rejected_terminalization_request_bytes,
            PrepareRejectedCompletionTerminalizationV1,
        )
        result = _exact(
            self.rejected_terminalization_result_bytes,
            PreparedRejectedCompletionTerminalizationV1,
        )
        assert isinstance(request, PrepareExecutionCompletion)
        assert isinstance(rejected, PreparedExecutionCompletionReject)
        assert isinstance(terminalization, PrepareRejectedCompletionTerminalizationV1)
        assert isinstance(result, PreparedRejectedCompletionTerminalizationV1)
        validate_rejected_terminalization_exchange(terminalization, result)
        if (
            terminalization.original_completion_request_bytes
            != self.original_completion_request_bytes
            or terminalization.original_completion_reject_bytes
            != self.original_completion_reject_bytes
            or terminalization.original_completion_reject_bytes != rejected.canonical_bytes()
            or result.terminal_manifest != self.terminal_manifest
            or result.terminal_run != self.terminal_run
            or result.terminal_manifest_head != self.terminal_manifest_head
            or result.terminal_run_head != self.terminal_run_head
            or self.terminal_manifest_head != manifest_ref(self.terminal_manifest)
            or self.terminal_run_head != run_ref(self.terminal_run)
            or self.terminal_manifest.target != "ABORTED"
            or self.terminal_run.state != "ABORTED"
            or rejected.run != self.terminal_run
            or self.ordered_open_obligations
            != self.terminal_manifest.complete_open_original_obligations
            or self.ordered_open_obligations != terminalization.complete_open_original_obligations
        ):
            raise ValueError("rejected work source differs from loop terminalization exchange")
        return self


CompletionTerminalWorkSourceV1 = Annotated[
    AcceptedCompletionWorkSourceV1 | RejectedCompletionWorkSourceV1,
    Field(discriminator="kind"),
]


def completion_terminal_work_source_bytes(source: CompletionTerminalWorkSourceV1) -> bytes:
    """The only registered completion decoder input for PrepareTerminalWork."""
    return source.canonical_bytes()


def decode_completion_terminal_work_source(raw: bytes) -> CompletionTerminalWorkSourceV1:
    try:
        adapter: TypeAdapter[CompletionTerminalWorkSourceV1] = TypeAdapter(
            CompletionTerminalWorkSourceV1
        )
        decoded: CompletionTerminalWorkSourceV1 = adapter.validate_json(raw)
    except Exception as error:
        raise ValueError("unknown completion terminal-work source") from error
    if decoded.canonical_bytes() != raw:
        raise ValueError("completion terminal-work source is not canonical JSON")
    return decoded


def completion_terminal_work_source_fingerprint(source: CompletionTerminalWorkSourceV1) -> str:
    value = source.model_dump(mode="json")
    return hashlib.sha256(
        _canonical(
            {
                "domain": "chiplog.agent-loop.completion-terminal-work-source.v1",
                "source": value,
            }
        )
    ).hexdigest()
