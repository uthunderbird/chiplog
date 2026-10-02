"""Contract tests for inert H1 scoped-authorization command projection."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
)
from chiplog.capabilities.effects.contracts import ExactHead, ProviderRecipient
from chiplog.capabilities.effects.h1_producer_source_contracts import (
    H1PreparedDeliveryMandateCandidateV1,
)
from chiplog.capabilities.effects.h1_scoped_authorization_command_projection import (
    H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND,
    H1_SCOPED_AUTHORIZATION_COMMAND_SCHEMA_V1,
    H1ScopedAuthorizationCommandProjectionV1,
    decode_h1_scoped_authorization_command_projection_v1,
    project_h1_scoped_authorization_command_v1,
)
from chiplog.capabilities.effects.h1_scoped_authorization_contracts import (
    H1ScopedAuthorizationCurrentV1,
)
from chiplog.composition.h1_scoped_authorization_issuance_v3 import (
    decode_scoped_authorization_issuance_v3,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _loop_head(name: str) -> LoopHead:
    return LoopHead(identity=name, head=name + ":head", fingerprint=_sha(name.encode()))


def _candidate() -> H1PreparedDeliveryMandateCandidateV1:
    return H1PreparedDeliveryMandateCandidateV1(
        prepared_delivery_basis_bytes=b"basis",
        grant_anchor=PreparedExternalDeliveryGrantAnchorV2(
            owner_id="deployment_trust",
            decision=_loop_head("grant-decision"),
            record_ordinal=0,
            record_type_id="chiplog.deployment_trust.prepared_external_delivery_grant",
            schema_id="chiplog.deployment_trust.record.v1",
            record=_loop_head("grant-record"),
            grant=_loop_head("grant"),
            revision=0,
        ),
        policy_anchor=PreparedExternalSelfDeliveryPolicyAnchorV1(
            owner_id="deployment_trust",
            decision=_loop_head("policy-decision"),
            record_ordinal=0,
            record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
            schema_id="chiplog.deployment_trust.record.v1",
            record=_loop_head("policy-record"),
            policy=_loop_head("policy"),
            revision=0,
        ),
        canonical_mandate_bytes=b"mandate",
        mandate_fingerprint=_sha(b"mandate"),
    )


def _recipient() -> ProviderRecipient:
    return ProviderRecipient(
        provider="provider",
        account="account",
        recipient="recipient",
        endpoint=ExactHead(subject_id="endpoint", head="endpoint:head", fingerprint=_sha(b"e")),
        canonical_address=b"address",
        credential_binding=ExactHead(
            subject_id="credential", head="credential:head", fingerprint=_sha(b"c")
        ),
    )


def _command() -> H1ScopedAuthorizationCommandProjectionV1:
    return project_h1_scoped_authorization_command_v1(
        command_id="command",
        tenant_id="tenant",
        intent_id="intent",
        selected_v4_decision_id="v4-decision",
        selected_v4_decision_fingerprint=_sha(b"v4"),
        selected_physical_intent_record_fingerprint=_sha(b"physical"),
        selected_g1_claim_decision_id="g1-decision",
        selected_g1_claim_decision_fingerprint=_sha(b"g1-decision"),
        selected_g1_canonical_record_fingerprint=_sha(b"g1-record"),
        complete_mandate_candidate=_candidate(),
        recipient=_recipient(),
        payload=b"payload",
    )


def test_projection_retains_each_anchor_and_digest() -> None:
    candidate = _candidate()
    value = _command()

    assert value.schema_id == H1_SCOPED_AUTHORIZATION_COMMAND_SCHEMA_V1
    assert value.kind == H1_PREPARE_SCOPED_AUTHORIZATION_COMMAND
    assert (value.command_id, value.tenant_id, value.intent_id) == ("command", "tenant", "intent")
    assert value.selected_v4_decision_id == "v4-decision"
    assert value.selected_v4_decision_fingerprint == _sha(b"v4")
    assert value.selected_physical_intent_record_fingerprint == _sha(b"physical")
    assert value.selected_g1_claim_decision_id == "g1-decision"
    assert value.selected_g1_claim_decision_fingerprint == _sha(b"g1-decision")
    assert value.selected_g1_canonical_record_fingerprint == _sha(b"g1-record")
    assert value.complete_mandate_candidate_fingerprint == _sha(candidate.canonical_bytes())
    assert value.complete_mandate_candidate_fingerprint != candidate.mandate_fingerprint
    assert value.recipient == _recipient()
    assert value.payload == b"payload"
    with pytest.raises(ValidationError):
        value.command_id = "other"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("selected_v4_decision_fingerprint", "bad"),
        ("selected_physical_intent_record_fingerprint", "bad"),
        ("selected_g1_claim_decision_fingerprint", "bad"),
        ("selected_g1_canonical_record_fingerprint", "bad"),
        ("complete_mandate_candidate_fingerprint", "bad"),
        ("recipient", "recipient"),
        ("payload", "payload"),
    ),
)
def test_projection_rejects_digest_or_payload_type_mutation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        H1ScopedAuthorizationCommandProjectionV1.model_validate(
            _command().model_dump() | {field: value}
        )


def test_strict_decoder_accepts_exact_canonical_wire_only() -> None:
    raw = _command().canonical_bytes()

    assert decode_h1_scoped_authorization_command_projection_v1(raw).canonical_bytes() == raw
    with pytest.raises(ValueError):
        decode_h1_scoped_authorization_command_projection_v1(
            raw.replace(b'"command_id":"command"', b'"command_id":"forged","command_id":"command"')
        )
    unknown = json.loads(raw)
    unknown["unknown"] = True
    with pytest.raises(ValueError, match="invalid"):
        decode_h1_scoped_authorization_command_projection_v1(
            json.dumps(unknown, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        )
    with pytest.raises(ValueError, match="strict UTF-8 JSON"):
        decode_h1_scoped_authorization_command_projection_v1(b"\xff")
    with pytest.raises(ValueError, match="noncanonical"):
        decode_h1_scoped_authorization_command_projection_v1(json.dumps(json.loads(raw)).encode())


def test_command_schema_cannot_be_decoded_as_v3_or_current_authority() -> None:
    raw = _command().canonical_bytes()

    with pytest.raises(ValueError):
        decode_scoped_authorization_issuance_v3(raw)
    with pytest.raises(ValidationError):
        H1ScopedAuthorizationCurrentV1.model_validate_json(raw)
