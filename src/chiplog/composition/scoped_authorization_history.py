"""Pure decoding for the closed V3 scoped-authorization publication envelope."""

from __future__ import annotations

import hashlib
import json

from chiplog.composition.h1_scoped_authorization_issuance_v3 import (
    SCHEMA,
    ScopedAuthorizationIssuanceV3,
    decode_scoped_authorization_issuance_v3,
)
from chiplog.platform._owner_publication_contracts import (
    SingleOwnerBatch,
    WorkerAuthentication,
)

_OPERATION = "effects.authorize_scoped_delivery_v3"
_REQUEST_SCHEMA = "chiplog.effects.scoped-dispatch-request.v3"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode_scoped_authorization_batch_v3(
    batch: SingleOwnerBatch,
) -> ScopedAuthorizationIssuanceV3:
    """Return only a V3 issuance whose complete outer batch is its exact witness.

    This is a structural decoder.  It does not authenticate historical sources,
    access a runtime, or authorize delivery.
    """
    if type(batch) is not SingleOwnerBatch:
        raise TypeError("scoped authorization history requires SingleOwnerBatch")
    authentication = batch.authentication
    if (
        batch.operation != _OPERATION
        or type(authentication) is not WorkerAuthentication
        or authentication.applicability_schema != SCHEMA
        or _digest(authentication.applicability_bytes) != authentication.applicability_fingerprint
    ):
        raise ValueError("scoped authorization batch lacks exact V3 applicability")
    issuance = decode_scoped_authorization_issuance_v3(authentication.applicability_bytes)
    if issuance.canonical_bytes() != authentication.applicability_bytes:
        raise ValueError("scoped authorization issuance bytes are noncanonical")
    if (
        batch.identity != issuance.identity
        or batch.expected != issuance.expected
        or authentication.invocation != issuance.invocation
    ):
        raise ValueError("scoped authorization batch outer witness differs from issuance")

    request_bytes = issuance.sent.canonical_payload
    request_fingerprint = _digest(request_bytes)
    if (
        batch.command.owner != "effects"
        or batch.command.schema_id != _REQUEST_SCHEMA
        or batch.command.canonical_bytes != request_bytes
        or batch.command.fingerprint != request_fingerprint
        or batch.identity.command_fingerprint != request_fingerprint
    ):
        raise ValueError("scoped authorization batch command differs from issuance request")
    if len(batch.complete_records) != 2 or batch.complete_records != issuance.output_records:
        raise ValueError("scoped authorization batch records differ from issuance")
    expected_batch_fingerprint = _digest(
        _canonical([record.model_dump(mode="json") for record in batch.complete_records])
    )
    if batch.complete_batch_fingerprint != expected_batch_fingerprint:
        raise ValueError("scoped authorization batch record digest differs")
    return issuance


__all__ = ["decode_scoped_authorization_batch_v3"]
