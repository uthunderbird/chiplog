"""Exact selected-source codecs for the prepared-delivery scoped V3 route.

These checks establish immutable wire agreement only. The reader must independently
join selected_decision to the journal batch and physical_record to retained SQL
bytes. No decoder authenticates selection, rechecks current resources or grants SEND.
"""

import hashlib
from typing import Literal

from .contracts import ExactHead
from .dispatch_authority_contracts import DispatchObservationDTO
from .lifecycle_transition_contracts import SelectedEffectsSource
from .scoped_dispatch_contracts import ScopedFirstSendDecision
from .scoped_intent_contracts import (
    ExternalActionIntentV3,
    PreparedDeliveryAuthority,
    PreparedDeliveryOriginV3,
)
from .scoped_intent_record_contracts import (
    SCHEMA_ID,
    ScopedIntentCanonicalMemberV3,
    decode_scoped_intent_member,
)

FIRST_SEND_SCHEMA_ID = "chiplog.effects.scoped-first-send-decision.v3"


class ScopedDeliveryRouteV3(DispatchObservationDTO):
    """A fixed contract route; construction does not register executable handlers."""

    intent_schema: Literal["chiplog.effects.external-action-intent.v3"] = SCHEMA_ID
    origin: Literal["PREPARED_DELIVERY"] = "PREPARED_DELIVERY"
    authority: Literal["DELIVERY_PREPARATION"] = "DELIVERY_PREPARATION"
    authorization_request: Literal["PREPARE_SCOPED_AUTHORIZATION_V3"] = (
        "PREPARE_SCOPED_AUTHORIZATION_V3"
    )
    first_send_request: Literal["PREPARE_SCOPED_FIRST_SEND_V3"] = "PREPARE_SCOPED_FIRST_SEND_V3"
    first_send_schema: Literal["chiplog.effects.scoped-first-send-decision.v3"] = (
        "chiplog.effects.scoped-first-send-decision.v3"
    )


SCOPED_DELIVERY_ROUTE_V3 = ScopedDeliveryRouteV3()


class ScopedDeliverySourceIntegrityError(ValueError):
    """One retained source cannot be interpreted through this exact route."""


def scoped_first_send_head(decision: ScopedFirstSendDecision) -> ExactHead:
    fingerprint = hashlib.sha256(decision.canonical_bytes()).hexdigest()
    return ExactHead(
        subject_id=decision.identity.command_id,
        head="scoped-first-send:" + fingerprint,
        fingerprint=fingerprint,
    )


def _check_source(source: SelectedEffectsSource, schema: str, expected: ExactHead) -> None:
    if (
        source.owner != "effects"
        or source.schema_id != schema
        or source.subject != expected
        or source.physical_record != expected
        or hashlib.sha256(source.canonical_record_bytes).hexdigest() != expected.fingerprint
    ):
        raise ScopedDeliverySourceIntegrityError("selected source envelope differs")


def decode_selected_delivery_intent(source: SelectedEffectsSource) -> ExternalActionIntentV3:
    """Reject legacy intent schemas and non-delivery V3 origins."""
    if source.schema_id != SCHEMA_ID:
        raise ScopedDeliverySourceIntegrityError("invalid selected delivery intent schema")
    try:
        intent = decode_scoped_intent_member(
            ScopedIntentCanonicalMemberV3(
                schema_id=SCHEMA_ID,
                record_id=source.subject.subject_id,
                canonical_record_bytes=source.canonical_record_bytes,
                fingerprint=source.physical_record.fingerprint,
            )
        )
    except ValueError as error:
        raise ScopedDeliverySourceIntegrityError("invalid selected delivery intent") from error
    expected = ExactHead(
        subject_id=intent.intent_id,
        head=intent.intent_id,
        fingerprint=hashlib.sha256(source.canonical_record_bytes).hexdigest(),
    )
    _check_source(source, SCHEMA_ID, expected)
    if not isinstance(intent.mandate.origin, PreparedDeliveryOriginV3) or not isinstance(
        intent.acquisition.authority, PreparedDeliveryAuthority
    ):
        raise ScopedDeliverySourceIntegrityError("selected intent is not prepared delivery")
    return intent


def decode_selected_delivery_first_send(
    source: SelectedEffectsSource, *, selected_intent: SelectedEffectsSource
) -> ScopedFirstSendDecision:
    """Read a decision row, never a PreparedScopedFirstSend aggregate or V2 record."""
    decode_selected_delivery_intent(selected_intent)
    try:
        decision = ScopedFirstSendDecision.model_validate_json(source.canonical_record_bytes)
    except ValueError as error:
        raise ScopedDeliverySourceIntegrityError("invalid selected first-send decision") from error
    _check_source(source, FIRST_SEND_SCHEMA_ID, scoped_first_send_head(decision))
    if (
        source.canonical_record_bytes != decision.canonical_bytes()
        or decision.original_intent != selected_intent.subject
    ):
        raise ScopedDeliverySourceIntegrityError("first-send bytes or original intent differ")
    return decision
