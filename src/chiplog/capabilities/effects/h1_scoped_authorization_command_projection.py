"""Canonical, inert H1 scoped-authorization command projection.

This value retains selected inputs for a later mounted composer.  Constructing
or decoding it establishes neither currentness nor authority, and reserves no
command or delivery work.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final, Literal

from pydantic import Field

from .contracts import ProviderRecipient
from .dispatch_authority_contracts import Digest, DispatchObservationDTO, Identity
from .h1_producer_source_contracts import H1PreparedDeliveryMandateCandidateV1

H1_SCOPED_AUTHORIZATION_COMMAND_SCHEMA_V1: Final[
    Literal["chiplog.effects.h1-scoped-authorization-command.v1"]
] = "chiplog.effects.h1-scoped-authorization-command.v1"
H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND: Final[
    Literal["H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND"]
] = "H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND"


class H1ScopedAuthorizationCommandProjectionV1(DispatchObservationDTO):
    """Selected H1 inputs retained for a later authority-owning composer only."""

    schema_id: Literal["chiplog.effects.h1-scoped-authorization-command.v1"] = (
        H1_SCOPED_AUTHORIZATION_COMMAND_SCHEMA_V1
    )
    kind: Literal["H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND"] = (
        H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND
    )
    command_id: Identity
    tenant_id: Identity
    intent_id: Identity
    selected_v4_decision_id: Identity
    selected_v4_decision_fingerprint: Digest
    selected_physical_intent_record_fingerprint: Digest
    selected_g1_claim_decision_id: Identity
    selected_g1_claim_decision_fingerprint: Digest
    selected_g1_canonical_record_fingerprint: Digest
    complete_mandate_candidate_fingerprint: Digest
    recipient: ProviderRecipient
    payload: bytes = Field(min_length=1)


def project_h1_scoped_authorization_command_v1(
    *,
    command_id: str,
    tenant_id: str,
    intent_id: str,
    selected_v4_decision_id: str,
    selected_v4_decision_fingerprint: str,
    selected_physical_intent_record_fingerprint: str,
    selected_g1_claim_decision_id: str,
    selected_g1_claim_decision_fingerprint: str,
    selected_g1_canonical_record_fingerprint: str,
    complete_mandate_candidate: H1PreparedDeliveryMandateCandidateV1,
    recipient: ProviderRecipient,
    payload: bytes,
) -> H1ScopedAuthorizationCommandProjectionV1:
    """Project exact selected values without authenticating or reserving them."""

    return H1ScopedAuthorizationCommandProjectionV1(
        command_id=command_id,
        tenant_id=tenant_id,
        intent_id=intent_id,
        selected_v4_decision_id=selected_v4_decision_id,
        selected_v4_decision_fingerprint=selected_v4_decision_fingerprint,
        selected_physical_intent_record_fingerprint=selected_physical_intent_record_fingerprint,
        selected_g1_claim_decision_id=selected_g1_claim_decision_id,
        selected_g1_claim_decision_fingerprint=selected_g1_claim_decision_fingerprint,
        selected_g1_canonical_record_fingerprint=selected_g1_canonical_record_fingerprint,
        complete_mandate_candidate_fingerprint=hashlib.sha256(
            complete_mandate_candidate.canonical_bytes()
        ).hexdigest(),
        recipient=recipient,
        payload=payload,
    )


def decode_h1_scoped_authorization_command_projection_v1(
    raw: bytes,
) -> H1ScopedAuthorizationCommandProjectionV1:
    """Decode only exact canonical UTF-8 JSON for this inert command wire."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("H1 command projection is not strict UTF-8 JSON") from error
    if (
        not isinstance(parsed, dict)
        or json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        != raw
    ):
        raise ValueError("H1 command projection is noncanonical")
    try:
        value = H1ScopedAuthorizationCommandProjectionV1.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("H1 command projection is invalid") from error
    if value.canonical_bytes() != raw:
        raise ValueError("H1 command projection differs from canonical DTO")
    return value


__all__ = [
    "H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND",
    "H1_SCOPED_AUTHORIZATION_COMMAND_SCHEMA_V1",
    "H1ScopedAuthorizationCommandProjectionV1",
    "decode_h1_scoped_authorization_command_projection_v1",
    "project_h1_scoped_authorization_command_v1",
]
