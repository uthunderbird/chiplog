"""Authenticated native facts for H1 post-seal COMPLETION recovery.

This adapter deliberately stops before ``PrepareExecutionCompletionFirstPathV2``.
That request also needs delivery and fence values, which only P can issue from
an opaque current receipt.  Historical V2 native evidence cannot replace that
owner boundary.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount
from chiplog.composition.h1_v2_recovery_native_source import (
    H1V2RecoveryNativeCut,
    H1V2RecoveryNativeSource,
    H1V2RecoveryNativeSourceError,
)
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA


class H1RecoveryStageSourceError(ValueError):
    """The installed native source cannot prove COMPLETION-stage facts."""


class H1RecoveryHistoricalPSourceUnavailable(H1RecoveryStageSourceError):
    """P has not supplied an authenticated historical delivery/fence bridge."""


@dataclass(frozen=True, slots=True)
class H1RecoveryCompletionNativeFacts:
    """Read-only native facts needed before P may build the completion request."""

    selected_response_seal: CallSubjectHead
    run: ExecutionRunRecord
    selected_attempt: CallSubjectHead
    selector_generation: int
    visibility_manifest: CallSubjectHead
    exact_captured_response: bytes


@dataclass(frozen=True, slots=True)
class _IssuedRecoveryCompletionNative:
    """Identity-held replay token; fields are evidence, never a request authority."""

    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    native_cut: H1V2RecoveryNativeCut
    facts: H1RecoveryCompletionNativeFacts


class H1RecoveryStageSource:
    """Issue and replay native, selected-seal-bounded COMPLETION facts."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("recovery stage source requires the installed runtime")
        mount = getattr(runtime, "_h1_recovery_mount", None)
        if type(mount) is not EnrolledH1RecoveryMount:
            raise H1RecoveryStageSourceError("recovery stage source requires an enrolled mount")
        gate = runtime._authority_gate()
        if mount.authority_gate is not gate:
            raise H1RecoveryStageSourceError("recovery stage source mount gate differs")
        self._runtime = runtime
        self._native = H1V2RecoveryNativeSource(runtime)
        self._issued: dict[int, _IssuedRecoveryCompletionNative] = {}

    def issue_completion_native(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> _IssuedRecoveryCompletionNative:
        """Issue facts after independently selecting the installed V2 native cut."""
        self._validate_inputs(original_identity, original_fingerprint, selected_seal)
        try:
            with self._runtime._authority_gate().hold():
                native = self._native.select(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                facts = self._facts(native, selected_seal)
                issued = _IssuedRecoveryCompletionNative(
                    original_identity, original_fingerprint, selected_seal, native, facts
                )
                self._issued[id(issued)] = issued
                return issued
        except H1RecoveryStageSourceError:
            raise
        except H1V2RecoveryNativeSourceError as error:
            raise H1RecoveryStageSourceError("selected native source differs") from error
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("selected native source differs") from error

    def replay_completion_native(
        self, issued: _IssuedRecoveryCompletionNative
    ) -> H1RecoveryCompletionNativeFacts:
        """Re-select all native evidence; copied tokens and changed cuts fail closed."""
        if (
            type(issued) is not _IssuedRecoveryCompletionNative
            or self._issued.get(id(issued)) is not issued
        ):
            raise H1RecoveryStageSourceError("completion native token is not issuer-owned")
        try:
            with self._runtime._authority_gate().hold():
                fresh = self._native.select(
                    original_identity=issued.original_identity,
                    original_fingerprint=issued.original_fingerprint,
                    selected_seal=issued.selected_seal,
                )
                facts = self._facts(fresh, issued.selected_seal)
                if fresh != issued.native_cut or facts != issued.facts:
                    raise H1RecoveryStageSourceError("selected native source differs on replay")
                return facts
        except H1RecoveryStageSourceError:
            raise
        except H1V2RecoveryNativeSourceError as error:
            raise H1RecoveryStageSourceError("selected native source differs on replay") from error
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("selected native source differs on replay") from error

    def reconstruct_completion_input(
        self, issued: _IssuedRecoveryCompletionNative
    ) -> PrepareExecutionCompletionFirstPathV2:
        """Reserve the full-request seam without accepting caller-shaped P evidence."""
        self.replay_completion_native(issued)
        raise H1RecoveryHistoricalPSourceUnavailable(
            "historical P delivery/fence source is unavailable"
        )

    @staticmethod
    def _validate_inputs(
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> None:
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("recovery stage source requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1RecoveryStageSourceError("original driver fingerprint is invalid")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("recovery stage source requires an exact selected seal")

    @staticmethod
    def _facts(
        native: H1V2RecoveryNativeCut, selected_seal: CallSubjectHead
    ) -> H1RecoveryCompletionNativeFacts:
        if type(native) is not H1V2RecoveryNativeCut:
            raise H1RecoveryStageSourceError("selected native cut differs")
        source = native.source
        lineage = source.complete_ordered_run_lineage
        if len(lineage) < 3:
            raise H1RecoveryStageSourceError("selected native Run lineage is incomplete")
        captured, run = lineage[-2:]
        if (
            source.selected_response_seal != selected_seal
            or run.event != "ModelCompletionPrepared"
            or captured.event != "ModelResponseCaptured"
            or len(run.turns) != 1
            or len(captured.turns) != 1
        ):
            raise H1RecoveryStageSourceError("selected native Run or capture differs")
        turn, captured_turn = run.turns[0], captured.turns[0]
        if (
            turn.turn_id != captured_turn.turn_id
            or turn.attempts != captured_turn.attempts
            or turn.selector != captured_turn.selector
            or len(turn.attempts) != 1
            or turn.response_seal != selected_seal
        ):
            raise H1RecoveryStageSourceError("selected native attempt or seal differs")
        attempt = turn.attempts[0]
        if attempt.response_base64 is None:
            raise H1RecoveryStageSourceError("selected native response is absent")
        try:
            response = base64.b64decode(attempt.response_base64, validate=True)
        except (TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("selected native response is invalid") from error
        manifest = attempt.manifest
        selected_attempt = CallSubjectHead(
            subject_id=attempt.attempt_id,
            revision=Present(head=attempt.head, fingerprint=attempt.digest()),
        )
        visibility_manifest = CallSubjectHead(
            subject_id="visibility",
            revision=Present(head="record:" + manifest.digest(), fingerprint=manifest.digest()),
        )
        H1RecoveryStageSource._require_physical_seal(native, selected_seal)
        return H1RecoveryCompletionNativeFacts(
            selected_seal,
            run,
            selected_attempt,
            attempt.generation,
            visibility_manifest,
            response,
        )

    @staticmethod
    def _require_physical_seal(
        native: H1V2RecoveryNativeCut, selected_seal: CallSubjectHead
    ) -> None:
        matches = [
            member
            for member in native.physical_members
            if member.schema_id == SEAL_SCHEMA
            and member.record_id == selected_seal.revision.head
            and hashlib.sha256(member.canonical_bytes).hexdigest()
            == selected_seal.revision.fingerprint
        ]
        if len(matches) != 1 or selected_seal.revision.head != (
            "record:" + selected_seal.revision.fingerprint
        ):
            raise H1RecoveryStageSourceError("selected native physical seal differs")
        try:
            seal = SealedResponseRecord.model_validate_json(matches[0].canonical_bytes)
        except ValueError as error:
            raise H1RecoveryStageSourceError("selected native physical seal is invalid") from error
        if (
            seal.canonical_bytes() != matches[0].canonical_bytes
            or seal.response_seal_id != selected_seal.subject_id
        ):
            raise H1RecoveryStageSourceError("selected native physical seal differs")


__all__ = [
    "H1RecoveryCompletionNativeFacts",
    "H1RecoveryHistoricalPSourceUnavailable",
    "H1RecoveryStageSource",
    "H1RecoveryStageSourceError",
]
