"""Fixed physical record contract for one scoped external-action intent.

This owner-local codec only proves exact immutable inputs agree.  It does not
authenticate them, select a batch, or issue a provider send.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chiplog.capabilities.agent_loop.delivery_contracts import (
    ProviderRecipient as LoopProviderRecipient,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryAcceptanceProposal,
    DeliveryCompletion,
    DeliveryObservation,
    prepare_completion,
)

from .contracts import ExactHead, ProviderRecipient
from .dispatch_authority_contracts import CapturedSource, DispatchSourceInventory
from .scoped_intent_contracts import (
    SCOPED_AUTHORITY_MATRIX,
    ExternalActionIntentV3,
    HumanScopedAdoption,
    PreparedDeliveryAuthority,
    PreparedDeliveryOriginV3,
    PreparedScopedIntentPublication,
    PrepareScopedIntentPublication,
    RegisteredCompensationAuthority,
    ScopedAuthorityRecord,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)

RECORD_KIND: Literal["ExternalActionIntent"] = "ExternalActionIntent"
SCHEMA_ID: Literal["chiplog.effects.external-action-intent.v3"] = (
    "chiplog.effects.external-action-intent.v3"
)
_INTENT_DOMAIN = b"chiplog.effects.intent.v3\x00"
_COMMITMENT_DOMAIN = b"chiplog.effects.scoped-intent-publication.v1\x00"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ScopedIntentRecordIntegrityError(ValueError):
    """A bounded failure while interpreting one immutable effects member."""


class ScopedIntentCanonicalMemberV3(BaseModel):
    """The physical envelope for the native immutable V3 intent identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_kind: Literal["ExternalActionIntent"] = RECORD_KIND
    schema_id: Literal["chiplog.effects.external-action-intent.v3"] = SCHEMA_ID
    record_id: str = Field(min_length=1)
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetainedOriginalSourceV3(BaseModel):
    """One fixed retained source descriptor; source authentication remains T/I."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    role: str = Field(min_length=1)
    reference: ExactHead
    schema_id: str = Field(min_length=1)
    canonical_record_bytes: bytes = Field(min_length=1)
    selected_decision: ExactHead


@dataclass(frozen=True)
class ScopedIntentRetainedExchangeV3:
    """The exact retained preimages and source descriptors for one exchange.

    ``original_sources`` has a fixed role order derived from the request.  Its
    reference projection is the only legal ``result.original_references``.
    """

    precursor_request_bytes: bytes
    precursor_result_bytes: bytes
    mandate_bytes: bytes
    acquisition_bytes: bytes
    original_sources: tuple[RetainedOriginalSourceV3, ...]

    @property
    def original_references(self) -> tuple[ExactHead, ...]:
        return tuple(source.reference for source in self.original_sources)


def scoped_intent_fingerprint(intent: ExternalActionIntentV3) -> str:
    """Return the V3 semantic digest, excluding only its self field."""
    body = intent.model_dump(mode="json", exclude={"fingerprint"})
    return _sha256(_INTENT_DOMAIN + _canonical_json(body))


def make_scoped_intent_member(intent: ExternalActionIntentV3) -> ScopedIntentCanonicalMemberV3:
    """Wrap one native intent in its fixed physical envelope."""
    canonical_record_bytes = intent.canonical_bytes()
    return ScopedIntentCanonicalMemberV3(
        record_id=intent.intent_id,
        canonical_record_bytes=canonical_record_bytes,
        fingerprint=_sha256(canonical_record_bytes),
    )


def decode_scoped_intent_member(
    member: ScopedIntentCanonicalMemberV3,
) -> ExternalActionIntentV3:
    """Decode one V3 intent only when its physical and semantic hashes agree."""
    if member.record_kind != RECORD_KIND or member.schema_id != SCHEMA_ID:
        raise ScopedIntentRecordIntegrityError("unexpected scoped-intent member envelope")
    if _sha256(member.canonical_record_bytes) != member.fingerprint:
        raise ScopedIntentRecordIntegrityError("member fingerprint differs from bytes")
    try:
        intent = ExternalActionIntentV3.model_validate_json(member.canonical_record_bytes)
    except Exception as error:
        raise ScopedIntentRecordIntegrityError("malformed external-action intent bytes") from error
    if intent.canonical_bytes() != member.canonical_record_bytes:
        raise ScopedIntentRecordIntegrityError("intent bytes are not canonical")
    if intent.schema_id != member.schema_id or intent.intent_id != member.record_id:
        raise ScopedIntentRecordIntegrityError("member envelope differs from intent identity")
    if scoped_intent_fingerprint(intent) != intent.fingerprint:
        raise ScopedIntentRecordIntegrityError("semantic intent fingerprint differs")
    _validate_intent_graph(intent)
    return intent


def scoped_intent_complete_owner_commitment(
    member: ScopedIntentCanonicalMemberV3,
    original_sources: tuple[RetainedOriginalSourceV3, ...],
) -> str:
    """Bind this one physical member and its ordered retained source heads."""
    decoded = decode_scoped_intent_member(member)
    descriptor = {
        "record_kind": member.record_kind,
        "schema_id": member.schema_id,
        "record_id": member.record_id,
        "fingerprint": member.fingerprint,
    }
    # Keep the decode observable above: commitment callers cannot hash a malformed
    # descriptor that only happens to have a valid physical SHA-256.
    if decoded.intent_id != member.record_id:
        raise ScopedIntentRecordIntegrityError("member identity differs after decoding")
    return _sha256(
        _COMMITMENT_DOMAIN
        + _canonical_json(
            {
                "members": [descriptor],
                "original_sources": [source.model_dump(mode="json") for source in original_sources],
            }
        )
    )


def validate_scoped_intent_publication(
    request: PrepareScopedIntentPublication,
    result: PreparedScopedIntentPublication,
    retained: ScopedIntentRetainedExchangeV3,
) -> ScopedIntentCanonicalMemberV3:
    """Verify the pure request/result and retained-preimage join for one intent."""
    intent = result.intent
    if request.intent != intent:
        raise ScopedIntentRecordIntegrityError("request and result intents differ")
    if result.source_request_fingerprint != _sha256(request.canonical_bytes()):
        raise ScopedIntentRecordIntegrityError("result source request fingerprint differs")
    expected_retained = make_scoped_intent_retained_exchange(request)
    if retained.original_sources != expected_retained.original_sources:
        raise ScopedIntentRecordIntegrityError("retained original source roles differ")
    if result.original_references != retained.original_references:
        raise ScopedIntentRecordIntegrityError(
            "result original references differ from retained order"
        )
    if retained.mandate_bytes != intent.mandate.canonical_bytes():
        raise ScopedIntentRecordIntegrityError("retained mandate bytes differ")
    if retained.acquisition_bytes != intent.acquisition.canonical_bytes():
        raise ScopedIntentRecordIntegrityError("retained acquisition bytes differ")
    if retained.precursor_request_bytes != intent.acquisition.precursor_request.canonical_bytes():
        raise ScopedIntentRecordIntegrityError("retained precursor request bytes differ")
    if retained.precursor_result_bytes != intent.acquisition.precursor_result.canonical_bytes():
        raise ScopedIntentRecordIntegrityError("retained precursor result bytes differ")
    _validate_intent_graph(intent)
    member = make_scoped_intent_member(intent)
    if result.complete_owner_commitment != scoped_intent_complete_owner_commitment(
        member, retained.original_sources
    ):
        raise ScopedIntentRecordIntegrityError("complete owner commitment differs")
    return member


def make_scoped_intent_retained_exchange(
    request: PrepareScopedIntentPublication,
) -> ScopedIntentRetainedExchangeV3:
    """Derive the complete fixed retained-source manifest from one request."""
    intent = request.intent
    acquisition = intent.acquisition
    sources: list[RetainedOriginalSourceV3] = []
    sources.extend(_authority_sources("current-origin", request.complete_current_origin_sources))
    sources.append(
        _authority_source(
            "precursor-interpretation-policy", acquisition.precursor_request.interpretation_policy
        )
    )
    sources.extend(
        _authority_sources(
            "precursor-preexisting", acquisition.precursor_request.preexisting_sources
        )
    )
    authority = acquisition.authority
    if isinstance(authority, HumanScopedAdoption):
        sources.extend(
            (
                _authority_source("human-adoption", authority.adoption_act),
                _authority_source(
                    "human-authenticated-invocation", authority.authenticated_invocation
                ),
            )
        )
    elif isinstance(authority, RegisteredCompensationAuthority):
        sources.extend(
            (
                _authority_source("compensation-authority", authority.authority_or_bounded_mandate),
                _authority_source("compensation-applicability", authority.current_applicability),
                _authority_source(
                    "compensation-adoption-requirement", authority.adoption_requirement
                ),
            )
        )
    else:
        sources.extend(
            (
                _authority_source(
                    "delivery-communication-authority",
                    authority.preexisting_communication_authority,
                ),
                _authority_source(
                    "delivery-disclosure-authority", authority.current_disclosure_authority
                ),
            )
        )
    sources.extend(
        _captured_inventory_sources("request-inventory", request.current.observation.sources)
    )
    sources.extend(
        _captured_inventory_sources("acquisition-inventory", acquisition.original_sources)
    )
    _validate_retained_source_fingerprints(tuple(sources))
    return ScopedIntentRetainedExchangeV3(
        precursor_request_bytes=acquisition.precursor_request.canonical_bytes(),
        precursor_result_bytes=acquisition.precursor_result.canonical_bytes(),
        mandate_bytes=intent.mandate.canonical_bytes(),
        acquisition_bytes=acquisition.canonical_bytes(),
        original_sources=tuple(sources),
    )


def _captured_inventory_sources(
    prefix: str, inventory: DispatchSourceInventory
) -> tuple[RetainedOriginalSourceV3, ...]:
    sources: list[RetainedOriginalSourceV3] = []
    for role in type(inventory).model_fields:
        observed = getattr(inventory, role)
        if isinstance(observed, CapturedSource):
            sources.append(
                RetainedOriginalSourceV3(
                    role=f"{prefix}-{role}",
                    reference=observed.head,
                    schema_id=observed.source_version,
                    canonical_record_bytes=observed.canonical_value,
                    selected_decision=observed.head,
                )
            )
    return tuple(sources)


def _authority_sources(
    prefix: str, values: tuple[ScopedAuthorityRecord, ...]
) -> tuple[RetainedOriginalSourceV3, ...]:
    return tuple(
        _authority_source(f"{prefix}-{index}", value) for index, value in enumerate(values)
    )


def _authority_source(role: str, value: ScopedAuthorityRecord) -> RetainedOriginalSourceV3:
    return RetainedOriginalSourceV3(
        role=role,
        reference=value.head,
        schema_id=value.schema_id,
        canonical_record_bytes=value.canonical_record_bytes,
        selected_decision=value.selected_decision,
    )


def _validate_retained_source_fingerprints(
    sources: tuple[RetainedOriginalSourceV3, ...],
) -> None:
    roles = [source.role for source in sources]
    if len(set(roles)) != len(roles):
        raise ScopedIntentRecordIntegrityError("duplicate retained original source role")
    for source in sources:
        if _sha256(source.canonical_record_bytes) != source.reference.fingerprint:
            raise ScopedIntentRecordIntegrityError("retained original source fingerprint differs")


def _validate_intent_graph(intent: ExternalActionIntentV3) -> None:
    mandate = intent.mandate
    acquisition = intent.acquisition
    precursor_request: ScopedPrecursorRequest = acquisition.precursor_request
    precursor_result: ScopedPrecursorResult = acquisition.precursor_result
    if precursor_request.mandate != mandate:
        raise ScopedIntentRecordIntegrityError("precursor request mandate differs")
    if precursor_result.source_request_fingerprint != _sha256(precursor_request.canonical_bytes()):
        raise ScopedIntentRecordIntegrityError("precursor result request fingerprint differs")
    if precursor_result.mandate_fingerprint != _sha256(mandate.canonical_bytes()):
        raise ScopedIntentRecordIntegrityError("precursor result mandate fingerprint differs")
    if precursor_result.interpretation_policy != precursor_request.interpretation_policy.head:
        raise ScopedIntentRecordIntegrityError("precursor interpretation policy differs")
    authority = acquisition.authority
    if authority.exact_mandate_bytes != mandate.canonical_bytes():
        raise ScopedIntentRecordIntegrityError("acquisition authority mandate bytes differ")
    permitted = dict(SCOPED_AUTHORITY_MATRIX).get(mandate.origin.kind, ())
    if authority.kind not in permitted:
        raise ScopedIntentRecordIntegrityError("origin and acquisition authority differ")
    if isinstance(authority, PreparedDeliveryAuthority):
        if not isinstance(mandate.origin, PreparedDeliveryOriginV3):
            raise ScopedIntentRecordIntegrityError("delivery authority has a non-delivery origin")
        _validate_delivery_basis(intent, authority, mandate.origin)


def _validate_delivery_basis(
    intent: ExternalActionIntentV3,
    authority: PreparedDeliveryAuthority,
    origin: PreparedDeliveryOriginV3,
) -> None:
    basis = authority.basis
    try:
        completion = DeliveryCompletion.model_validate_json(basis.completion_command_bytes)
        observation = DeliveryObservation.model_validate_json(basis.delivery_observation_bytes)
        proposal = DeliveryAcceptanceProposal.model_validate_json(basis.loop_proposal_bytes)
    except Exception as error:
        raise ScopedIntentRecordIntegrityError("malformed retained delivery basis bytes") from error
    if (
        completion.canonical_bytes() != basis.completion_command_bytes
        or observation.canonical_bytes() != basis.delivery_observation_bytes
        or proposal.canonical_bytes() != basis.loop_proposal_bytes
        or prepare_completion(completion, observation) != proposal
    ):
        raise ScopedIntentRecordIntegrityError("delivery basis differs from its accepted proposal")
    accepted = tuple(
        item
        for item in proposal.manifest.ordered_deliveries
        if item.delivery_id == origin.binding.delivery_id
    )
    if len(accepted) != 1:
        raise ScopedIntentRecordIntegrityError("delivery binding has no unique accepted delivery")
    delivery = accepted[0]
    expected_acceptance = proposal.acceptance
    if (
        basis.acceptance.subject_id != expected_acceptance.identity
        or basis.acceptance.head != expected_acceptance.head
        or basis.acceptance.fingerprint != expected_acceptance.fingerprint
        or origin.binding.acceptance != basis.acceptance
        or origin.binding.render_digest != delivery.render_digest
        or origin.binding.manifest_digest != delivery.manifest_digest
        or intent.mandate.payload != delivery.rendered_bytes
        or origin.binding.selection.kind != delivery.selection.kind
        or not _same_recipient(origin.binding.selection.recipient, delivery.selection.recipient)
        or not _same_recipient(intent.mandate.recipient, delivery.selection.recipient)
    ):
        raise ScopedIntentRecordIntegrityError("intent does not bind its accepted delivery")


def _same_recipient(effect: ProviderRecipient, delivery: LoopProviderRecipient) -> bool:
    """Compare the intentionally distinct effects and loop recipient DTOs."""
    return (
        effect.provider == delivery.provider_id
        and effect.account == delivery.account_id
        and effect.recipient == delivery.recipient_id
        and effect.canonical_address == delivery.canonical_address
        and effect.endpoint.subject_id == delivery.endpoint.identity
        and effect.endpoint.head == delivery.endpoint.head
        and effect.endpoint.fingerprint == delivery.endpoint.fingerprint
        and effect.credential_binding.subject_id == delivery.credential_binding.identity
        and effect.credential_binding.head == delivery.credential_binding.head
        and effect.credential_binding.fingerprint == delivery.credential_binding.fingerprint
    )
