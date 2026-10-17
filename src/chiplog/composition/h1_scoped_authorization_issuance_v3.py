"""Inert retained evidence for one scoped-authorization owner exchange.

Decoding this value verifies that an already captured effects-owner exchange is
self-consistent.  It neither authenticates the historical sources nor calls a
current owner, opens a writer, selects a journal prefix, or permits publication.
Those are deliberately separate mounted-reader responsibilities.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryAuthorityEvidenceV1,
)
from chiplog.capabilities.effects.scoped_authorization_record_contracts import (
    decode_scoped_authorization_owner_records,
    make_scoped_authorization_owner_records,
    scoped_dispatch_authorization_head,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import (
    PreparedScopedAuthorization,
    PrepareScopedAuthorization,
    ScopedDispatchRequest,
    ScopedDispatchResult,
)
from chiplog.composition.h1_j7_current_scope_exchange import H1J7CurrentScopeExchangeV1
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    InvocationProofRef,
    OwnerRecordBytes,
    PublicationIdentity,
)
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import decode_trust_owner_call_canonical

SCHEMA: Literal["chiplog.composition.h1-scoped-authorization-issuance.v3"] = (
    "chiplog.composition.h1-scoped-authorization-issuance.v3"
)
_OPERATION = "effects.prepare_scoped_dispatch_v3"
_REQUEST_SCHEMA = "chiplog.effects.scoped-dispatch-request.v3"
_RESULT_SCHEMA = "chiplog.effects.scoped-dispatch-result.v3"
_BATCH_DOMAIN = b"chiplog.effects.scoped-authorization-batch.v3\x00"
_RETAINED_BATCH_DOMAIN = b"chiplog.composition.h1-scoped-authorization-issuance.v3\x00"
_REQUESTS: TypeAdapter[ScopedDispatchRequest] = TypeAdapter(ScopedDispatchRequest)
_RESULTS: TypeAdapter[ScopedDispatchResult] = TypeAdapter(ScopedDispatchResult)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _dto_bytes(value: object) -> bytes:
    if not hasattr(value, "model_dump"):
        raise TypeError("retained digest input is not a DTO")
    return json.dumps(
        value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def _framed(*parts: bytes) -> bytes:
    return b"".join(len(part).to_bytes(8, "big") + part for part in parts)


def _retained_fingerprint(
    *,
    identity: PublicationIdentity,
    invocation: InvocationProofRef,
    expected: AuthoritativeReadManifest,
    original_cut: PrepareScopedAuthorization,
    h1_authority_evidence: H1ScopedDeliveryAuthorityEvidenceV1,
    h1_current_scope_exchange: H1J7CurrentScopeExchangeV1,
    sent: PublicPortCall,
    returned: PublicPortSuccess,
    output_records: tuple[OwnerRecordBytes, OwnerRecordBytes],
    sent_at_ns: int,
    returned_at_ns: int,
) -> str:
    return _sha(
        _RETAINED_BATCH_DOMAIN
        + _framed(
            _dto_bytes(identity),
            _dto_bytes(invocation),
            _dto_bytes(expected),
            original_cut.canonical_bytes(),
            h1_authority_evidence.canonical_bytes(),
            h1_current_scope_exchange.canonical_bytes(),
            sent.canonical_payload,
            returned.canonical_payload,
            *(row.canonical_bytes for row in output_records),
            json.dumps([sent_at_ns, returned_at_ns], separators=(",", ":")).encode(),
        )
    )


def _canonical_json(raw: bytes) -> None:
    """Reject duplicate, non-object, and noncanonical JSON before DTO decode."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("owner frame payload is not strict UTF-8 JSON") from error
    if (
        not isinstance(value, dict)
        or json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        != raw
    ):
        raise ValueError("owner frame payload is not canonical JSON")


def _decode_request(raw: bytes) -> PrepareScopedAuthorization:
    _canonical_json(raw)
    try:
        decoded = _REQUESTS.validate_json(raw, strict=True)
    except ValueError as error:
        raise ValueError("owner request does not decode as scoped dispatch") from error
    if type(decoded) is not PrepareScopedAuthorization or decoded.canonical_bytes() != raw:
        raise ValueError("owner request is not canonical PrepareScopedAuthorization")
    return decoded


def _decode_result(raw: bytes) -> PreparedScopedAuthorization:
    _canonical_json(raw)
    try:
        decoded = _RESULTS.validate_json(raw, strict=True)
    except ValueError as error:
        raise ValueError("owner result does not decode as scoped dispatch") from error
    if type(decoded) is not PreparedScopedAuthorization or decoded.canonical_bytes() != raw:
        raise ValueError("owner result is not canonical PreparedScopedAuthorization")
    return decoded


class ScopedAuthorizationIssuanceV3(DispatchObservationDTO):
    """A versioned non-authorizing witness for exactly two effects output rows."""

    schema_id: Literal["chiplog.composition.h1-scoped-authorization-issuance.v3"] = SCHEMA
    operation: Literal["effects.authorize_scoped_delivery_v3"] = (
        "effects.authorize_scoped_delivery_v3"
    )
    identity: PublicationIdentity
    invocation: InvocationProofRef
    expected: AuthoritativeReadManifest
    original_cut: PrepareScopedAuthorization
    h1_authority_evidence: H1ScopedDeliveryAuthorityEvidenceV1
    h1_current_scope_exchange: H1J7CurrentScopeExchangeV1
    sent: PublicPortCall
    returned: PublicPortSuccess
    sent_at_ns: Annotated[int, Field(ge=0, le=2**64 - 1)]
    returned_at_ns: Annotated[int, Field(ge=0, le=2**64 - 1)]
    output_records: tuple[OwnerRecordBytes, OwnerRecordBytes]
    owner_batch_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_retained_exchange(self) -> Self:
        sent = self.sent
        returned = self.returned
        if (
            sent.operation_id != _OPERATION
            or sent.schema_id != _REQUEST_SCHEMA
            or returned.schema_id != _RESULT_SCHEMA
            or sent.caller.owner_id != "broker"
            or sent.callee.owner_id != "effects"
            or returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or self.returned_at_ns < self.sent_at_ns
            or self.returned_at_ns >= sent.budget.absolute_deadline_ns
        ):
            raise ValueError("retained effects exchange envelope differs")
        if (
            sent.caller.tenant_id != sent.callee.tenant_id
            or sent.caller.broker_epoch != sent.callee.broker_epoch
            or sent.caller.generation_id != sent.callee.generation_id
            or self.invocation.broker_epoch != str(sent.caller.broker_epoch)
            or self.invocation.broker_session != sent.caller.session_id
            or self.invocation.runtime_generation != sent.caller.generation_id
        ):
            raise ValueError("retained broker/effects session differs")
        scope_wire = self.h1_current_scope_exchange
        if (
            scope_wire.sent.caller != sent.caller
            or scope_wire.sent.caller.tenant_id != sent.caller.tenant_id
            or scope_wire.sent.caller.broker_epoch != sent.caller.broker_epoch
            or scope_wire.sent.caller.generation_id != sent.caller.generation_id
            or scope_wire.sent.callee.owner_id != "deployment_trust"
            or scope_wire.sent.callee.tenant_id != sent.caller.tenant_id
            or scope_wire.sent.callee.broker_epoch != sent.caller.broker_epoch
            or scope_wire.sent.callee.generation_id != sent.caller.generation_id
            or scope_wire.returned.responder != scope_wire.sent.callee
            or scope_wire.sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
            or scope_wire.returned.schema_id
            != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
        ):
            raise ValueError("retained current-scope exchange is not from this broker session")
        try:
            scope_call = decode_trust_owner_call_canonical(scope_wire.sent.canonical_payload)
            pinned_scope_call = H1OwnerCurrentCallV1.model_validate_json(scope_call.request_bytes)
            scope_request = ReadCurrentHermeticExecutionScopeV1.model_validate_json(
                pinned_scope_call.read_request_bytes
            )
            scope_candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                scope_wire.returned.canonical_payload
            )
        except ValueError as error:
            raise ValueError("retained current-scope wire is not canonical") from error
        if (
            scope_call.mode != "READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1"
            or scope_wire.sent.operation_id != pinned_scope_call.route.operation
            or scope_wire.sent.request_id != pinned_scope_call.route.request_id
            or scope_wire.returned.request_id != pinned_scope_call.route.request_id
            or pinned_scope_call.route.tenant_id != scope_wire.sent.caller.tenant_id
            or pinned_scope_call.route.broker_epoch != scope_wire.sent.caller.broker_epoch
            or pinned_scope_call.route.runtime_generation != scope_wire.sent.caller.generation_id
            or pinned_scope_call.route.broker_session_id != scope_wire.sent.caller.session_id
            or pinned_scope_call.route.owner_session_id != scope_wire.sent.callee.session_id
            or scope_request.canonical_bytes() != pinned_scope_call.read_request_bytes
            or scope_request != scope_wire.request
            or scope_candidate.current != scope_wire.result
            or scope_candidate.route != pinned_scope_call.route
            or scope_candidate.request_digest != pinned_scope_call.request_digest
        ):
            raise ValueError("retained current-scope wire differs from its decoded exchange")

        request = _decode_request(sent.canonical_payload)
        result = _decode_result(returned.canonical_payload)
        request_digest = _sha(sent.canonical_payload)
        if (
            sent.request_id != request.identity.command_id
            or result.source_request_fingerprint != request_digest
            or result.complete_batch_fingerprint
            != _sha(
                _BATCH_DOMAIN
                + request_digest.encode()
                + result.authorization.canonical_bytes()
                + result.parent.canonical_bytes()
            )
        ):
            raise ValueError("retained request/result digest differs")
        if request != self.original_cut:
            raise ValueError("retained original selected predecessor cut differs")

        records = self.output_records
        expected_rows = make_scoped_authorization_owner_records(result.authorization, result.parent)
        if records != expected_rows:
            raise ValueError("retained output records differ from owner result")
        authorization, parent = decode_scoped_authorization_owner_records(
            records,
            observed=request.observed,
            current=request.current,
            complete_current_origin_sources=request.complete_current_origin_sources,
        )
        mandate = request.observed.original_intent.mandate
        expected_mandate = reference(mandate.mandate_id, mandate.canonical_bytes())
        expected_authorization_id = "scoped-authorization/" + request.identity.command_id
        authorization_head = scoped_dispatch_authorization_head(authorization)
        if (
            authorization.command != request.identity
            or authorization.authorization_id != expected_authorization_id
            or authorization.immutable_mandate != expected_mandate
            or authorization.semantics != mandate.semantics
            or authorization.fence != request.fence
            or parent.authorization != authorization_head
            or self.identity.tenant_id != mandate.tenant_id
            or self.identity.command_id != request.identity.command_id
            or self.identity.command_fingerprint != request_digest
            or self.expected.tenant_id != mandate.tenant_id
            or self.invocation.operation_subject != request_digest
        ):
            raise ValueError("retained authorization is not fenced to its original request")
        frozen = _retained_fingerprint(
            identity=self.identity,
            invocation=self.invocation,
            expected=self.expected,
            original_cut=self.original_cut,
            h1_authority_evidence=self.h1_authority_evidence,
            h1_current_scope_exchange=self.h1_current_scope_exchange,
            sent=sent,
            returned=returned,
            output_records=records,
            sent_at_ns=self.sent_at_ns,
            returned_at_ns=self.returned_at_ns,
        )
        if self.owner_batch_fingerprint != frozen:
            raise ValueError("retained issuance batch digest differs")
        return self


def issuance_batch_fingerprint(
    *,
    identity: PublicationIdentity,
    invocation: InvocationProofRef,
    expected: AuthoritativeReadManifest,
    original_cut: PrepareScopedAuthorization,
    h1_authority_evidence: H1ScopedDeliveryAuthorityEvidenceV1,
    h1_current_scope_exchange: H1J7CurrentScopeExchangeV1,
    sent: PublicPortCall,
    returned: PublicPortSuccess,
    output_records: tuple[OwnerRecordBytes, OwnerRecordBytes],
    sent_at_ns: int,
    returned_at_ns: int,
) -> str:
    """The frozen V3 retention digest; timestamps/evidence are separately canonical fields."""
    return _retained_fingerprint(
        identity=identity,
        invocation=invocation,
        expected=expected,
        original_cut=original_cut,
        h1_authority_evidence=h1_authority_evidence,
        h1_current_scope_exchange=h1_current_scope_exchange,
        sent=sent,
        returned=returned,
        output_records=output_records,
        sent_at_ns=sent_at_ns,
        returned_at_ns=returned_at_ns,
    )


def decode_scoped_authorization_issuance_v3(raw: bytes) -> ScopedAuthorizationIssuanceV3:
    """Decode only this exact canonical schema; the result remains inert evidence."""
    _canonical_json(raw)
    try:
        value = ScopedAuthorizationIssuanceV3.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("retained scoped authorization issuance is invalid") from error
    if value.canonical_bytes() != raw:
        raise ValueError("retained scoped authorization issuance is noncanonical")
    return value


__all__ = [
    "SCHEMA",
    "ScopedAuthorizationIssuanceV3",
    "decode_scoped_authorization_issuance_v3",
    "issuance_batch_fingerprint",
]
