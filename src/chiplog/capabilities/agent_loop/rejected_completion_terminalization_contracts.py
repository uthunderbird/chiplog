"""Loop-owned terminalization of a semantic completion rejection.

This is an inert preparation seam.  In particular, the terminal manifest is
produced here, rather than being attached by a composition caller to rejected
work.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Protocol, Self

from pydantic import Field, TypeAdapter, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .completion_owner_record_contracts import completion_request_fingerprint
from .execution_completion_contracts import (
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from .execution_recovery_observations import ExecutionTerminalManifest, SealedAccountingRecord
from .execution_run_versions import ExecutionRun
from .recovery_contracts import Digest, OriginalObligationBinding, Present, RecoveryDTO


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _exact(raw: bytes, adapter: TypeAdapter[object]) -> object:
    decoded = adapter.validate_json(raw)
    canonical = getattr(decoded, "canonical_bytes", None)
    if not callable(canonical) or canonical() != raw:
        raise ValueError("original owner bytes are not exact canonical bytes")
    return decoded


def run_ref(run: ExecutionRun) -> CallSubjectHead:
    """Native loop Run record reference, validated before it crosses an owner seam."""
    decoded: ExecutionRun = TypeAdapter(ExecutionRun).validate_json(run.canonical_bytes())
    pending = decoded.model_copy(update={"head": "pending"})
    if decoded.head != "loop:" + pending.digest():
        raise ValueError("Run head differs from native loop record head")
    return CallSubjectHead(
        subject_id=decoded.run_id,
        revision=Present(
            head=decoded.head, fingerprint=hashlib.sha256(decoded.canonical_bytes()).hexdigest()
        ),
    )


def manifest_ref(manifest: ExecutionTerminalManifest) -> CallSubjectHead:
    decoded = ExecutionTerminalManifest.model_validate_json(manifest.canonical_bytes())
    return CallSubjectHead(
        subject_id=decoded.manifest_id,
        revision=Present(
            head=decoded.manifest_id,
            fingerprint=hashlib.sha256(decoded.canonical_bytes()).hexdigest(),
        ),
    )


class PrepareRejectedCompletionTerminalizationV1(RecoveryDTO):
    kind: Literal["PREPARE_REJECTED_COMPLETION_TERMINALIZATION_V1"] = (
        "PREPARE_REJECTED_COMPLETION_TERMINALIZATION_V1"
    )
    schema_id: Literal["chiplog.agent-loop.prepare-rejected-completion-terminalization.v1"] = (
        "chiplog.agent-loop.prepare-rejected-completion-terminalization.v1"
    )
    original_completion_request_bytes: bytes = Field(min_length=1)
    original_completion_reject_bytes: bytes = Field(min_length=1)
    original_captured_run: CallSubjectHead
    original_selected_attempt: CallSubjectHead
    source_cut_fingerprint: Digest
    proposed_terminal_run: ExecutionRun
    preserved_trace: CallSubjectHead
    complete_accounting: tuple[SealedAccountingRecord, ...]
    complete_open_original_obligations: tuple[OriginalObligationBinding, ...]

    @model_validator(mode="after")
    def original_exchange_is_exact(self) -> Self:
        request = _exact(
            self.original_completion_request_bytes,
            TypeAdapter(PrepareExecutionCompletion),
        )
        rejected = _exact(
            self.original_completion_reject_bytes, TypeAdapter(PreparedExecutionCompletionReject)
        )
        assert isinstance(request, PrepareExecutionCompletion)
        assert isinstance(rejected, PreparedExecutionCompletionReject)
        if self.original_captured_run != run_ref(request.run):
            raise ValueError("original captured Run differs from original completion request")
        if self.original_selected_attempt != request.selected_attempt:
            raise ValueError("original selected attempt differs from completion request")
        if self.source_cut_fingerprint != request.cut.digest():
            raise ValueError("source cut fingerprint differs from completion request")
        if rejected.source_request_fingerprint != completion_request_fingerprint(request):
            raise ValueError("rejection source fingerprint differs from completion request")
        if rejected.original_captured_attempt != request.selected_attempt:
            raise ValueError("rejection captured attempt differs from completion request")
        if rejected.visibility_manifest != request.visibility_manifest:
            raise ValueError("rejection visibility differs from completion request")
        if rejected.run != self.proposed_terminal_run:
            raise ValueError("rejected terminal Run differs from the original loop exchange")
        if rejected.preserved_trace != self.preserved_trace:
            raise ValueError("preserved trace differs from original rejection")
        if self.proposed_terminal_run.state != "ABORTED":
            raise ValueError("rejected completion terminal Run must be ABORTED")
        if request.run.state != "ACTIVE":
            raise ValueError("rejected completion must retain the original active Run")
        if (
            self.proposed_terminal_run.schema_id != request.run.schema_id
            or self.proposed_terminal_run.tenant != request.run.tenant
            or self.proposed_terminal_run.principal != request.run.principal
            or self.proposed_terminal_run.run_id != request.run.run_id
            or self.proposed_terminal_run.predecessor != request.run.head
        ):
            raise ValueError("terminal Run loses original completion lineage")
        run_ref(request.run)
        run_ref(self.proposed_terminal_run)
        return self


class PreparedRejectedCompletionTerminalizationV1(RecoveryDTO):
    kind: Literal["PREPARED_REJECTED_COMPLETION_TERMINALIZATION_V1"] = (
        "PREPARED_REJECTED_COMPLETION_TERMINALIZATION_V1"
    )
    schema_id: Literal["chiplog.agent-loop.prepared-rejected-completion-terminalization.v1"] = (
        "chiplog.agent-loop.prepared-rejected-completion-terminalization.v1"
    )
    source_request_fingerprint: Digest
    terminal_manifest: ExecutionTerminalManifest
    terminal_manifest_head: CallSubjectHead
    terminal_run: ExecutionRun
    terminal_run_head: CallSubjectHead
    complete_owner_commitment: Digest

    @model_validator(mode="after")
    def aborted_manifest_and_run(self) -> Self:
        if self.terminal_manifest.target != "ABORTED" or self.terminal_run.state != "ABORTED":
            raise ValueError("rejected terminalization must produce ABORTED manifest and Run")
        return self


class RejectedCompletionTerminalizationPreparationFailureV1(RecoveryDTO):
    kind: Literal["REJECTED_COMPLETION_TERMINALIZATION_FAILURE_V1"] = (
        "REJECTED_COMPLETION_TERMINALIZATION_FAILURE_V1"
    )
    schema_id: Literal["chiplog.agent-loop.rejected-completion-terminalization-failure.v1"] = (
        "chiplog.agent-loop.rejected-completion-terminalization-failure.v1"
    )
    code: Literal["STALE", "CONFLICT", "DENIED", "INTEGRITY_FAULT", "SCHEMA"]
    reason: str = Field(min_length=1)


RejectedTerminalizationPreparationResultV1 = Annotated[
    PreparedRejectedCompletionTerminalizationV1
    | RejectedCompletionTerminalizationPreparationFailureV1,
    Field(discriminator="kind"),
]


class RejectedCompletionTerminalizationPreparationPort(Protocol):
    async def prepare_rejected_terminalization(
        self, request: PrepareRejectedCompletionTerminalizationV1
    ) -> RejectedTerminalizationPreparationResultV1: ...


def validate_rejected_terminalization_exchange(
    request: PrepareRejectedCompletionTerminalizationV1,
    result: PreparedRejectedCompletionTerminalizationV1,
) -> None:
    """Join result output to its retained request; construction proves no authority."""
    # Reparse this public input so a direct model_copy cannot skip its validators.
    request = PrepareRejectedCompletionTerminalizationV1.model_validate_json(
        request.canonical_bytes()
    )
    if result.source_request_fingerprint != rejected_terminalization_request_fingerprint(request):
        raise ValueError("terminalization result source fingerprint differs")
    if result.terminal_run != request.proposed_terminal_run or result.terminal_run_head != run_ref(
        result.terminal_run
    ):
        raise ValueError("terminalization result Run differs from request/native reference")
    if result.terminal_manifest_head != manifest_ref(result.terminal_manifest):
        raise ValueError("terminalization result manifest differs from native reference")
    original = _exact(
        request.original_completion_request_bytes,
        TypeAdapter(PrepareExecutionCompletion),
    )
    assert isinstance(original, PrepareExecutionCompletion)
    manifest = result.terminal_manifest
    if (
        manifest.target != "ABORTED"
        or manifest.prior_run != request.original_captured_run
        or manifest.source_cut_fingerprint != request.source_cut_fingerprint
        or manifest.complete_accounting != request.complete_accounting
        or manifest.complete_open_original_obligations != request.complete_open_original_obligations
        or manifest.command_id != original.command_id
    ):
        raise ValueError("terminalization manifest differs from retained completion inputs")


def rejected_terminalization_request_fingerprint(
    request: PrepareRejectedCompletionTerminalizationV1,
) -> str:
    """Domain-separated request fingerprint; it never includes downstream work."""
    return _fingerprint(
        {
            "domain": "chiplog.agent-loop.rejected-completion-terminalization.v1",
            "request": request.model_dump(mode="json"),
        }
    )
