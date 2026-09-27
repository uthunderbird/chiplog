"""Bounded, inert native-v2 proposals for model-attempt recovery.

The caller supplies a broker-retained source member.  Decoding it binds structure
only; broker selection, issuer authentication, no-exposure observation, and the
writer's final compare-and-swap remain outside this producer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import ValidationError

from .call_acceptance_contracts import CallPreparationRejected, CallSubjectHead
from .execution_contracts import (
    ExecutionModelAttempt,
    ExecutionRunRecord,
    ExecutionTurn,
    ExecutionVisibilityManifest,
)
from .model_attempt_recovery_contracts import (
    LateExecutionResponseRecord,
    PreparedLateExecutionResponse,
    PreparedModelAttemptReplacement,
    ReplaceExecutionModelAttempt,
    RetainLateExecutionResponse,
)
from .recovery_contracts import NonSchedulerFence, Present, RecoveryDTO
from .recovery_source_contracts import (
    RecoveryProofSourceMember,
    RecoverySourceIntegrityError,
    decode_model_pre_emission_proof,
)

FailureCode = Literal["DENIED", "STALE", "CONFLICT", "UNSUPPORTED", "INTEGRITY_FAULT", "HOLD"]
_MAX_UINT64 = 2**64 - 1


class _Rejected(ValueError):
    def __init__(self, code: FailureCode, reason: str) -> None:
        self.code = code
        super().__init__(reason)


def _require(condition: bool, reason: str, code: FailureCode = "INTEGRITY_FAULT") -> None:
    if not condition:
        raise _Rejected(code, reason)


def _proposal_digest(proposal: RecoveryDTO) -> str:
    wire = json.loads(proposal.canonical_bytes())
    del wire["proposal_fingerprint"]
    return hashlib.sha256(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _rejected(request: object, error: Exception) -> CallPreparationRejected:
    command_id = getattr(request, "command_id", None)
    return CallPreparationRejected(
        command_id=command_id if isinstance(command_id, str) and command_id else "invalid-command",
        code=error.code if isinstance(error, _Rejected) else "INTEGRITY_FAULT",
        reason=str(error) if isinstance(error, _Rejected) else "malformed model recovery request",
    )


def _head(subject_id: str, head: str, fingerprint: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject_id, revision=Present(head=head, fingerprint=fingerprint)
    )


def _run_ref(run: ExecutionRunRecord) -> CallSubjectHead:
    return _head(run.run_id, run.head, run.digest())


def _attempt_ref(attempt: ExecutionModelAttempt) -> CallSubjectHead:
    return _head(attempt.attempt_id, attempt.head, attempt.digest())


def _request_ref(attempt: ExecutionModelAttempt) -> CallSubjectHead:
    fingerprint = hashlib.sha256(attempt.request.encode()).hexdigest()
    return _head(attempt.request, "request:" + fingerprint, fingerprint)


def _manifest_ref(manifest: ExecutionVisibilityManifest) -> CallSubjectHead:
    digest = manifest.digest()
    return _head("manifest:" + digest, "record:" + digest, digest)


def _selected(request: ReplaceExecutionModelAttempt) -> ExecutionModelAttempt:
    run = request.run
    if not isinstance(run, ExecutionRunRecord):
        raise _Rejected("UNSUPPORTED", "only native v2 model replacement is mounted")
    _require(
        run.state == "ACTIVE"
        and bool(run.turns)
        and run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "missing active self-bound execution Run",
        "STALE",
    )
    _require(
        request.fence.run_head == run.head, "execution fence differs from submitted Run", "STALE"
    )
    if not isinstance(request.fence, NonSchedulerFence):
        raise _Rejected(
            "UNSUPPORTED", "scheduler model replacement requires the registered lease checker"
        )
    _require(
        request.fence.run_id == run.run_id
        and request.fence.worker_session_id == run.worker_session,
        "non-scheduler execution fence differs from submitted Run",
        "STALE",
    )
    turn = run.turns[-1]
    _require(
        turn.state == "CALL_ACTIVE"
        and bool(turn.attempts)
        and turn.selector == len(turn.attempts) - 1
        and turn.response_seal is None
        and turn.initialized_calls is None,
        "missing unsealed current execution attempt",
        "STALE",
    )
    attempt = turn.attempts[turn.selector]
    _require(
        turn.head == "execution-turn:" + turn.model_copy(update={"head": "pending"}).digest()
        and attempt.head
        == "execution-attempt:" + attempt.model_copy(update={"head": "pending"}).digest(),
        "current attempt or Turn head differs from native bytes",
    )
    _require(
        request.expected_selector == turn.selector == attempt.generation
        and attempt.state == "PREPARED_NOT_EMITTED"
        and attempt.response_base64 is None
        and attempt.receipt is None
        and attempt.rejection is None,
        "selected attempt is not exactly prepared and un-emitted",
        "STALE",
    )
    manifest = attempt.manifest
    _require(
        manifest.tenant == run.tenant
        and manifest.principal == run.principal
        and manifest.run_id == run.run_id
        and manifest.turn_id == turn.turn_id
        and manifest.generation == attempt.generation
        and manifest.contour_head == run.contour_head
        and manifest.worker_session == run.worker_session == attempt.worker_session,
        "selected attempt manifest differs from active Run identity",
    )
    return attempt


def _bound_proof(
    request: ReplaceExecutionModelAttempt,
    source_member: RecoveryProofSourceMember,
    attempt: ExecutionModelAttempt,
    *,
    expected_tenant_id: str,
    expected_database_id: str,
) -> None:
    source = decode_model_pre_emission_proof(
        source_member,
        request.no_exposure,
        expected_tenant_id=expected_tenant_id,
        expected_database_id=expected_database_id,
    )
    run = request.run
    assert isinstance(run, ExecutionRunRecord)
    proof = request.no_exposure
    _require(
        expected_tenant_id == run.tenant
        and proof.original_run == _run_ref(run)
        and proof.original_attempt == request.selected_attempt == _attempt_ref(attempt)
        and proof.immutable_request == _request_ref(attempt)
        and proof.visibility_manifest == _manifest_ref(attempt.manifest)
        and proof.lineage_id == attempt.lineage_id
        and proof.selector_generation == attempt.generation
        and proof.provider_contract == attempt.provider_contract
        and proof.recipient == attempt.recipient,
        "no-exposure proof differs from selected native attempt",
        "STALE",
    )
    _require(
        source.selector_generation == source.permanently_fenced_generation == attempt.generation
        and source.model_worker_session.subject_id == attempt.worker_session,
        "pre-emission source differs from selected generation or worker session",
        "STALE",
    )


def _replacement_run(run: ExecutionRunRecord, attempt: ExecutionModelAttempt) -> ExecutionRunRecord:
    turn = run.turns[-1]
    _require(attempt.generation < _MAX_UINT64, "attempt generation overflows uint64", "DENIED")
    generation = attempt.generation + 1
    manifest = attempt.manifest.model_copy(update={"generation": generation})
    replacement = attempt.model_copy(
        update={
            "attempt_id": attempt.lineage_id + "/generation/" + str(generation),
            "generation": generation,
            "manifest": manifest,
            "head": "pending",
        }
    )
    replacement = replacement.model_copy(
        update={"head": "execution-attempt:" + replacement.digest()}
    )
    new_turn = ExecutionTurn.model_validate(
        turn.model_copy(
            update={
                "attempts": (*turn.attempts, replacement),
                "selector": generation,
                "head": "pending",
            }
        ).model_dump()
    )
    new_turn = new_turn.model_copy(update={"head": "execution-turn:" + new_turn.digest()})
    pending = run.model_copy(
        update={
            "head": "pending",
            "predecessor": run.head,
            "event": "ModelAttemptReplacedWithoutExposure",
            "turns": (*run.turns[:-1], new_turn),
        }
    )
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def prepare_model_attempt_replacement(
    request: ReplaceExecutionModelAttempt,
    source_member: RecoveryProofSourceMember,
    *,
    expected_tenant_id: str,
    expected_database_id: str,
) -> PreparedModelAttemptReplacement | CallPreparationRejected:
    """Prepare a v2 candidate; this does not authenticate or publish its source."""
    try:
        request = ReplaceExecutionModelAttempt.model_validate_json(request.canonical_bytes())
        source_member = RecoveryProofSourceMember.model_validate_json(
            source_member.canonical_bytes()
        )
        attempt = _selected(request)
        _bound_proof(
            request,
            source_member,
            attempt,
            expected_tenant_id=expected_tenant_id,
            expected_database_id=expected_database_id,
        )
        assert isinstance(request.run, ExecutionRunRecord)
        replacement_run = _replacement_run(request.run, attempt)
        replacement = replacement_run.turns[-1].attempts[-1]
        proposal = PreparedModelAttemptReplacement(
            source_request_fingerprint=request.digest(),
            original_attempt=request.selected_attempt,
            superseded_attempt=_attempt_ref(attempt),
            replacement_attempt=_attempt_ref(replacement),
            run=replacement_run,
            proposal_fingerprint="0" * 64,
        )
        return proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
    except (
        _Rejected,
        RecoverySourceIntegrityError,
        ValidationError,
        ValueError,
        TypeError,
    ) as error:
        return _rejected(request, error)


def prepare_late_execution_response(
    request: RetainLateExecutionResponse,
) -> PreparedLateExecutionResponse | CallPreparationRejected:
    """Retain independent late evidence without selecting a new Run or capture."""
    try:
        request = RetainLateExecutionResponse.model_validate_json(request.canonical_bytes())
        fingerprint = request.digest()
        record = LateExecutionResponseRecord(
            evidence_id="late-execution-response:" + fingerprint,
            request=request,
        )
        proposal = PreparedLateExecutionResponse(
            source_request_fingerprint=fingerprint,
            record=record,
            proposal_fingerprint="0" * 64,
        )
        return proposal.model_copy(update={"proposal_fingerprint": _proposal_digest(proposal)})
    except (ValidationError, ValueError, TypeError) as error:
        return _rejected(request, error)
