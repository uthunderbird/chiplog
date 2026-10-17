"""Authenticated, historical source for one selected scoped V3 delivery intent.

This is deliberately only a reader.  Its output preserves the selected intent
and the observation that no registered first-SEND material is present; it does
not obtain current authority, prepare a SEND, or expose an append operation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import cast

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_authorization_record_contracts import (
    AUTHORIZATION_SCHEMA,
    REVISION_SCHEMA,
)
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import ScopedPreSendRevision
from chiplog.capabilities.effects.scoped_intent_contracts import (
    ExternalActionIntentV3,
    ScopedDispatchAuthorizationRecord,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    SCHEMA_ID as SCOPED_INTENT_SCHEMA,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    make_scoped_intent_member,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import (
    H1CompletionIssuanceV3,
    H1CompletionIssuanceV4,
)
from chiplog.composition.h1_historical_selected_sources import (
    H1HistoricalSelection,
    bind_selected_h1_completion,
)
from chiplog.composition.r16_dispatch_history import verify_dispatch_history
from chiplog.composition.r16_effects import _read_materialized_effects_with_history
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.owner_publications import SelectedOwnerDecision


class ScopedDeliverySelectedSourcesError(ValueError):
    """The selected V3 history cannot safely be used as an absent-SEND source."""


@dataclass(frozen=True, slots=True)
class ScopedDeliveryFirstSendAbsent:
    """Complete selected history contains no first-SEND material for this intent."""

    intent_id: str
    act_id: str | None
    kind: str = "FIRST_SEND_ABSENT"


@dataclass(frozen=True, slots=True)
class ScopedDeliveryInitialAuthorizationAbsent:
    """Complete selected history has no authorization or pre-send revision for one intent."""

    intent_id: str
    kind: str = "INITIAL_AUTHORIZATION_ABSENT"


@dataclass(frozen=True, slots=True)
class ScopedDeliveryFrontierObservation:
    tenant_id: str
    tenant_frontier: int
    materialization_commitment: str
    owner_journal_head: str | None
    physical_path: str
    physical_device: int
    physical_inode: int


@dataclass(frozen=True, slots=True)
class ScopedDeliverySelectedSourcesResult:
    """Inert selected V3 intent evidence and a complete first-SEND absence observation."""

    intent: SelectedEffectsSource
    selected_h1: H1HistoricalSelection
    initial_authorization: ScopedDeliveryInitialAuthorizationAbsent
    first_send: ScopedDeliveryFirstSendAbsent
    frontier: ScopedDeliveryFrontierObservation


def _decision_head(decision: SelectedOwnerDecision) -> ExactHead:
    return ExactHead(
        subject_id=decision.decision_id,
        head=decision.decision_head,
        fingerprint=decision.decision_fingerprint,
    )


def _is_send_like(record: OwnerRecordBytes) -> bool:
    """Classify any prospective V3 SEND wire conservatively until its operation exists."""
    fields = " ".join(
        str(getattr(record, name, "")) for name in ("schema_id", "record_kind", "owner")
    ).upper()
    return "SEND" in fields or ("SCOPED" in fields and ".V3" in fields)


def _require_initial_authorization_absent(
    *, cut: object, intent_id: str
) -> ScopedDeliveryInitialAuthorizationAbsent:
    """Decode every known authorization row before declaring this intent initial."""
    rows = getattr(cut, "rows", None)
    if type(rows) is not tuple:
        raise ScopedDeliverySelectedSourcesError("selected V3 authorization cut is unavailable")
    for row in rows:
        record = getattr(row, "record", None)
        schema = getattr(record, "schema_id", None)
        raw = getattr(record, "canonical_bytes", None)
        if type(raw) is not bytes:
            raise ScopedDeliverySelectedSourcesError("selected V3 authorization row is malformed")
        try:
            if schema == AUTHORIZATION_SCHEMA:
                authorization = ScopedDispatchAuthorizationRecord.model_validate_json(raw)
                if authorization.canonical_bytes() != raw:
                    raise ValueError("noncanonical authorization")
                original_intent = authorization.original_intent.subject_id
            elif schema == REVISION_SCHEMA:
                revision = ScopedPreSendRevision.model_validate_json(raw)
                if revision.canonical_bytes() != raw:
                    raise ValueError("noncanonical pre-send revision")
                original_intent = revision.original_intent.subject_id
            else:
                continue
        except ValueError as error:
            raise ScopedDeliverySelectedSourcesError(
                "selected V3 authorization row is malformed"
            ) from error
        if original_intent == intent_id:
            raise ScopedDeliverySelectedSourcesError(
                "selected V3 intent already has authorization or pre-send revision"
            )
    return ScopedDeliveryInitialAuthorizationAbsent(intent_id)


class ScopedDeliverySelectedSources:
    """Acquire the installed gate and reconstruct one selected V3 intent source."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("selected scoped delivery sources require the installed runtime")
        self._runtime = runtime
        self._gate = runtime._authority_gate()

    def read(
        self, *, intent_id: str, act_id: str | None = None
    ) -> ScopedDeliverySelectedSourcesResult:
        if type(intent_id) is not str or not intent_id:
            raise TypeError("intent_id must be a nonempty string")
        if act_id is not None and (type(act_id) is not str or not act_id):
            raise TypeError("act_id must be a nonempty string when supplied")
        with self._gate.hold():
            return self._read_held(intent_id=intent_id, act_id=act_id)

    def _read_held(
        self, *, intent_id: str, act_id: str | None = None
    ) -> ScopedDeliverySelectedSourcesResult:
        self._gate.require_held()
        cut, journal = _read_materialized_effects_with_history(
            self._runtime, self._runtime._owner_decisions()
        )
        # This checks complete SQL membership and the registered V3 effects
        # member route.  It also rejects unknown effects schemas before absence.
        verify_dispatch_history(cut, journal)

        matches: list[tuple[SelectedOwnerDecision, OwnerRecordBytes]] = []
        decisions_by_sequence: dict[int, tuple[SelectedOwnerDecision, ...]] = {}
        for candidate in journal.decisions:
            decisions_by_sequence.setdefault(candidate.tenant_commit_sequence, ())
            decisions_by_sequence[candidate.tenant_commit_sequence] += (candidate,)
        for row in cut.rows:
            record = row.record
            if record.schema_id != SCOPED_INTENT_SCHEMA:
                continue
            candidates = decisions_by_sequence.get(row.commit_sequence, ())
            if len(candidates) != 1:
                raise ScopedDeliverySelectedSourcesError("selected V3 record lacks its decision")
            decision = candidates[0]
            if record.record_id == intent_id:
                matches.append((decision, record))
        if len(matches) != 1:
            raise ScopedDeliverySelectedSourcesError("selected V3 intent is absent or ambiguous")
        decision, record = matches[0]
        batch = decision.prepared.request
        if type(batch) is not CompleteDeliveryBatchV2:
            raise ScopedDeliverySelectedSourcesError(
                "selected V3 intent has another publication envelope"
            )
        bound = bind_selected_h1_completion(batch, self._runtime)
        if bound.decision != decision:
            raise ScopedDeliverySelectedSourcesError("selected V3 intent differs from H1 selection")
        issuance = bound.issuance
        if type(issuance) in (H1CompletionIssuanceV3, H1CompletionIssuanceV4):
            scoped_issuance = cast(H1CompletionIssuanceV3 | H1CompletionIssuanceV4, issuance)
            expected = make_scoped_intent_member(
                scoped_issuance.effects_result.intent_result.intent
            )
        else:
            raise ScopedDeliverySelectedSourcesError(
                "selected V3 intent has unsupported H1 selection"
            )
        if (
            record.record_kind != expected.record_kind
            or record.schema_id != expected.schema_id
            or record.record_id != expected.record_id
            or record.canonical_bytes != expected.canonical_record_bytes
            or record.fingerprint != expected.fingerprint
        ):
            raise ScopedDeliverySelectedSourcesError(
                "selected V3 physical member differs from issuance"
            )
        source = SelectedEffectsSource(
            owner="effects",
            subject=ExactHead(
                subject_id=intent_id,
                head=intent_id,
                fingerprint=hashlib.sha256(record.canonical_bytes).hexdigest(),
            ),
            schema_id=record.schema_id,
            canonical_record_bytes=record.canonical_bytes,
            selected_decision=_decision_head(decision),
            physical_record=ExactHead(
                subject_id=intent_id, head=intent_id, fingerprint=record.fingerprint
            ),
        )
        intent: ExternalActionIntentV3 = decode_selected_delivery_intent(source)
        if intent.intent_id != intent_id:
            raise ScopedDeliverySelectedSourcesError("selected V3 intent identity differs")
        initial_authorization = _require_initial_authorization_absent(cut=cut, intent_id=intent_id)
        # The full selected effects cut has already been authenticated.  Scan all
        # selected batch members as well: a future broker consumption is not an
        # effects row. Until an exact registered decoder can prove a candidate
        # belongs to another intent, every SEND-shaped member holds this result.
        for selected in journal.decisions:
            operation = str(getattr(selected.prepared.request, "operation", "")).upper()
            if "SEND" in operation:
                raise ScopedDeliverySelectedSourcesError(
                    "selected V3 first-SEND operation is unknown, partial, or already present"
                )
            for member in selected.prepared.request.complete_records:
                if _is_send_like(member):
                    raise ScopedDeliverySelectedSourcesError(
                        "selected V3 first-SEND material is unknown, partial, or already present"
                    )
        return ScopedDeliverySelectedSourcesResult(
            intent=source,
            selected_h1=bound,
            initial_authorization=initial_authorization,
            first_send=ScopedDeliveryFirstSendAbsent(intent_id, act_id),
            frontier=ScopedDeliveryFrontierObservation(
                cut.tenant_id,
                cut.tenant_frontier,
                cut.materialization_commitment,
                cut.owner_journal_head,
                cut.physical_path,
                cut.physical_device,
                cut.physical_inode,
            ),
        )


__all__ = [
    "ScopedDeliveryFirstSendAbsent",
    "ScopedDeliveryFrontierObservation",
    "ScopedDeliveryInitialAuthorizationAbsent",
    "ScopedDeliverySelectedSources",
    "ScopedDeliverySelectedSourcesError",
    "ScopedDeliverySelectedSourcesResult",
]
