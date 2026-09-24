"""Closed physical completion-record members owned by ``agent_loop``.

These codecs prove byte-for-byte member consistency.  They do not select a
decision, authenticate a caller, or grant publication authority.
"""

from __future__ import annotations

import hashlib
from base64 import b64decode
from binascii import Error as BinasciiError
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from .call_acceptance_contracts import CallSubjectHead
from .delivery_contracts import ExactHead
from .delivery_preparation import (
    DeliveryAcceptanceProposal,
    DeliveryCompletion,
    DeliveryObservation,
    prepare_completion,
)
from .execution_completion_contracts import (
    PreparedExecutionCompletion,
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from .execution_contracts import ExecutionTurn
from .execution_history_contracts import ExecutionTurnV3
from .execution_recovery_observations import ExecutionTerminalManifest
from .execution_run_versions import ExecutionRun
from .recovery_contracts import Digest, Identity, Present, RecoveryDTO
from .recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
    decode_recovery_record_member,
)

DELIVERY_ACCEPTANCE_SCHEMA = "chiplog.agent-loop.delivery-acceptance-record.v1"
COMPLETION_REJECTION_SCHEMA = "chiplog.agent-loop.completion-rejection-record.v1"


class CompletionRecordIntegrityError(ValueError):
    """A closed completion physical member has inconsistent bytes or identity."""


class CompletionCanonicalMember(RecoveryDTO):
    """One closed, owner-local completion member envelope."""

    record_kind: Identity
    schema_id: Identity
    record_id: Identity
    canonical_record_bytes: bytes
    fingerprint: Digest


class CompletionRejectionRecordV1(RecoveryDTO):
    """The non-circular physical projection of a semantic completion rejection."""

    schema_id: Literal["chiplog.agent-loop.completion-rejection-record.v1"] = (
        "chiplog.agent-loop.completion-rejection-record.v1"
    )
    source_request_fingerprint: Digest
    original_captured_attempt: CallSubjectHead
    preserved_trace: CallSubjectHead
    visibility_manifest: CallSubjectHead
    reasons: tuple[
        Literal[
            "SCHEMA",
            "ASSERTION_EVIDENCE",
            "AUTHORITY",
            "POLICY",
            "DISCLOSURE",
            "ENDPOINT",
        ],
        ...,
    ] = Field(min_length=1)
    terminal_run: CallSubjectHead


@dataclass(frozen=True)
class DecodedCompletionCanonicalMember:
    member: CompletionCanonicalMember
    record: (
        ExecutionRun
        | ExecutionTerminalManifest
        | DeliveryAcceptanceProposal
        | CompletionRejectionRecordV1
    )


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _content_id(schema_id: str, raw: bytes) -> str:
    return schema_id + ":" + _sha(raw)


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise CompletionRecordIntegrityError(reason)


def _member(kind: str, schema_id: str, record_id: str, raw: bytes) -> CompletionCanonicalMember:
    return CompletionCanonicalMember(
        record_kind=kind,
        schema_id=schema_id,
        record_id=record_id,
        canonical_record_bytes=raw,
        fingerprint=_sha(raw),
    )


def validate_completion_request_source(
    request: PrepareExecutionCompletion,
) -> ExecutionTurn | ExecutionTurnV3:
    """Bind a completion request to its exact native captured Run and selected Turn.

    This deliberately does not parse the captured payload: a semantic rejection
    may preserve malformed raw bytes under its original source bindings.
    """
    run = request.run
    _require(run.state, "ACTIVE", "completion source Run is not ACTIVE")
    _require(
        run.head,
        "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "completion source Run native head differs",
    )
    raw_run = run.canonical_bytes()
    run_fingerprint = _sha(raw_run)
    _require(request.delivery.tenant, run.tenant, "delivery tenant differs from native Run")
    _require(
        request.delivery.run,
        ExactHead(identity=run.run_id, head=run.head, fingerprint=run_fingerprint),
        "delivery Run differs from native Run",
    )
    _require(
        request.delivery.captured_response,
        request.exact_captured_response,
        "delivery captured response differs from request",
    )
    _require(
        request.cut.current_run,
        CallSubjectHead(
            subject_id=run.run_id,
            revision=Present(head=run.head, fingerprint=run_fingerprint),
        ),
        "completion cut current Run differs from native Run",
    )

    selected: list[ExecutionTurn | ExecutionTurnV3] = []
    for turn in run.turns:
        if turn.selector >= len(turn.attempts):
            continue
        attempt = turn.attempts[turn.selector]
        attempt_ref = CallSubjectHead(
            subject_id=attempt.attempt_id,
            revision=Present(head=attempt.head, fingerprint=attempt.digest()),
        )
        if (
            request.selected_attempt == attempt_ref
            and request.selector_generation == attempt.generation
        ):
            selected.append(turn)
    if len(selected) != 1:
        raise CompletionRecordIntegrityError(
            "completion request has no unique selected native Turn"
        )
    turn = selected[0]
    _require(
        request.delivery.turn_id, turn.turn_id, "delivery Turn differs from selected native Turn"
    )
    attempt = turn.attempts[turn.selector]
    if attempt.response_base64 is None:
        raise CompletionRecordIntegrityError("selected native attempt has no captured response")
    try:
        captured = b64decode(attempt.response_base64, validate=True)
    except (ValueError, BinasciiError) as error:
        raise CompletionRecordIntegrityError(
            "selected native attempt response is not base64"
        ) from error
    _require(
        captured,
        request.exact_captured_response,
        "selected native attempt response differs from completion request",
    )
    return turn


def _decode_run(member: CompletionCanonicalMember) -> ExecutionRun:
    if member.record_kind != "Run" or member.schema_id not in {
        "chiplog.agent-loop.execution-record.v2",
        "chiplog.agent-loop.execution-record.v3",
    }:
        raise CompletionRecordIntegrityError("unregistered terminal Run row")
    try:
        run: ExecutionRun = TypeAdapter(ExecutionRun).validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise CompletionRecordIntegrityError("invalid terminal Run bytes") from error
    _require(
        run.canonical_bytes(), member.canonical_record_bytes, "terminal Run bytes are not canonical"
    )
    _require(run.schema_id, member.schema_id, "terminal Run schema differs")
    _require(run.head, member.record_id, "terminal Run physical ID differs")
    if run.state not in {"SUCCEEDED", "ABORTED"}:
        raise CompletionRecordIntegrityError("completion Run is not terminal")
    expected_head = "loop:" + run.model_copy(update={"head": "pending"}).digest()
    _require(run.head, expected_head, "terminal Run native head differs")
    return run


def _decode_manifest(member: CompletionCanonicalMember) -> ExecutionTerminalManifest:
    recovery = RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=member.record_kind,
        schema_id=member.schema_id,
        record_id=member.record_id,
        canonical_record_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    try:
        decoded = decode_recovery_record_member(recovery)
    except RecoveryRecordIntegrityError as error:
        raise CompletionRecordIntegrityError(str(error)) from error
    if not isinstance(decoded.record, ExecutionTerminalManifest):
        raise CompletionRecordIntegrityError("completion terminal manifest row has wrong type")
    return decoded.record


def _decode_proposal(member: CompletionCanonicalMember) -> DeliveryAcceptanceProposal:
    if (
        member.record_kind != "DELIVERY_ACCEPTANCE"
        or member.schema_id != DELIVERY_ACCEPTANCE_SCHEMA
    ):
        raise CompletionRecordIntegrityError("unregistered delivery acceptance row")
    try:
        proposal = DeliveryAcceptanceProposal.model_validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise CompletionRecordIntegrityError("invalid delivery acceptance bytes") from error
    _require(
        proposal.canonical_bytes(),
        member.canonical_record_bytes,
        "delivery bytes are not canonical",
    )
    _require(
        member.record_id,
        _content_id(DELIVERY_ACCEPTANCE_SCHEMA, member.canonical_record_bytes),
        "delivery physical ID differs",
    )
    _require(
        proposal.proposal_digest,
        _sha(proposal.manifest.canonical_bytes()),
        "delivery proposal manifest digest differs",
    )
    return proposal


def _decode_rejection(member: CompletionCanonicalMember) -> CompletionRejectionRecordV1:
    if (
        member.record_kind != "COMPLETION_REJECTION"
        or member.schema_id != COMPLETION_REJECTION_SCHEMA
    ):
        raise CompletionRecordIntegrityError("unregistered completion rejection row")
    try:
        record = CompletionRejectionRecordV1.model_validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise CompletionRecordIntegrityError("invalid completion rejection bytes") from error
    _require(
        record.canonical_bytes(), member.canonical_record_bytes, "rejection bytes are not canonical"
    )
    _require(bool(record.reasons), True, "completion rejection has no reasons")
    _require(
        member.record_id,
        _content_id(COMPLETION_REJECTION_SCHEMA, member.canonical_record_bytes),
        "rejection physical ID differs",
    )
    return record


def make_terminal_run_member(run: ExecutionRun) -> CompletionCanonicalMember:
    """Wrap a native v2/v3 Run only after checking its native self-derived head."""
    raw = run.canonical_bytes()
    member = _member("Run", run.schema_id, run.head, raw)
    _decode_run(member)
    return member


def make_terminal_manifest_member(manifest: ExecutionTerminalManifest) -> CompletionCanonicalMember:
    """Adapt the existing R1 terminal-manifest registration without a new registry."""
    member = _member(
        "TERMINAL_MANIFEST", manifest.schema_id, manifest.manifest_id, manifest.canonical_bytes()
    )
    _decode_manifest(member)
    return member


def make_delivery_acceptance_member(
    completion: DeliveryCompletion, observation: DeliveryObservation
) -> CompletionCanonicalMember:
    """Recompute the exact loop proposal before deriving its physical envelope."""
    proposal = prepare_completion(completion, observation)
    raw = proposal.canonical_bytes()
    member = _member(
        "DELIVERY_ACCEPTANCE",
        DELIVERY_ACCEPTANCE_SCHEMA,
        _content_id(DELIVERY_ACCEPTANCE_SCHEMA, raw),
        raw,
    )
    _decode_proposal(member)
    return member


def make_prepared_delivery_acceptance_member(
    request: PrepareExecutionCompletion, prepared: PreparedExecutionCompletion
) -> CompletionCanonicalMember:
    """Bind an accepted result to a proposal recomputed from its exact request bytes."""
    selected_turn = validate_completion_request_source(request)
    try:
        completion = DeliveryCompletion.model_validate_json(request.exact_captured_response)
    except ValidationError as error:
        raise CompletionRecordIntegrityError(
            "completion request has invalid delivery bytes"
        ) from error
    _require(
        completion.tenant, request.run.tenant, "accepted delivery tenant differs from native Run"
    )
    _require(completion.run_id, request.run.run_id, "accepted delivery Run differs from native Run")
    _require(
        completion.turn_id,
        selected_turn.turn_id,
        "accepted delivery Turn differs from selected native Turn",
    )
    member = make_delivery_acceptance_member(completion, request.delivery)
    decoded = _decode_proposal(member)
    _require(decoded, prepared.delivery, "prepared delivery proposal differs from request")
    return member


def completion_request_fingerprint(request: PrepareExecutionCompletion) -> str:
    """Frozen completion-request convention for the rejection physical projection."""
    return _sha(request.canonical_bytes())


def make_completion_rejection_member(
    request: PrepareExecutionCompletion, rejected: PreparedExecutionCompletionReject
) -> CompletionCanonicalMember:
    """Project a rejection without its circular owner commitment or a TRACE row."""
    validate_completion_request_source(request)
    _require(
        rejected.source_request_fingerprint,
        completion_request_fingerprint(request),
        "rejection source request fingerprint differs",
    )
    _require(
        rejected.original_captured_attempt,
        request.selected_attempt,
        "rejection original captured attempt differs",
    )
    _require(
        rejected.visibility_manifest, request.visibility_manifest, "rejection visibility differs"
    )
    run_member = make_terminal_run_member(rejected.run)
    _require(rejected.run.state, "ABORTED", "rejection terminal Run is not ABORTED")
    record = CompletionRejectionRecordV1(
        source_request_fingerprint=rejected.source_request_fingerprint,
        original_captured_attempt=rejected.original_captured_attempt,
        preserved_trace=rejected.preserved_trace,
        visibility_manifest=rejected.visibility_manifest,
        reasons=rejected.reasons,
        terminal_run=CallSubjectHead(
            subject_id=rejected.run.run_id,
            revision=Present(head=rejected.run.head, fingerprint=run_member.fingerprint),
        ),
    )
    raw = record.canonical_bytes()
    member = _member(
        "COMPLETION_REJECTION",
        COMPLETION_REJECTION_SCHEMA,
        _content_id(COMPLETION_REJECTION_SCHEMA, raw),
        raw,
    )
    _decode_rejection(member)
    return member


def decode_completion_canonical_member(
    member: CompletionCanonicalMember,
) -> DecodedCompletionCanonicalMember:
    """Decode a member from the finite completion physical table."""
    _require(
        member.fingerprint,
        _sha(member.canonical_record_bytes),
        "completion record fingerprint differs",
    )
    record: (
        ExecutionRun
        | ExecutionTerminalManifest
        | DeliveryAcceptanceProposal
        | CompletionRejectionRecordV1
    )
    if member.record_kind == "Run":
        record = _decode_run(member)
    elif member.record_kind == "TERMINAL_MANIFEST":
        record = _decode_manifest(member)
    elif member.record_kind == "DELIVERY_ACCEPTANCE":
        record = _decode_proposal(member)
    elif member.record_kind == "COMPLETION_REJECTION":
        record = _decode_rejection(member)
    else:
        raise CompletionRecordIntegrityError("unregistered completion record kind")
    return DecodedCompletionCanonicalMember(member=member, record=record)
