"""Focused retained-wire checks for the inert scoped-authorization V3 codec."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.scoped_authorization_record_contracts import (
    make_scoped_authorization_owner_records,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import ScopedPreSendRevision
from chiplog.capabilities.effects.scoped_intent_contracts import ScopedDispatchAuthorizationRecord
from chiplog.composition.h1_scoped_authorization_issuance_v3 import (
    SCHEMA,
    ScopedAuthorizationIssuanceV3,
    decode_scoped_authorization_issuance_v3,
    issuance_batch_fingerprint,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fixture_issuance() -> ScopedAuthorizationIssuanceV3:
    raw = (
        Path(__file__).with_name("fixtures") / "h1_scoped_authorization_issuance_v3.json"
    ).read_bytes()
    value = decode_scoped_authorization_issuance_v3(raw)
    assert value.canonical_bytes() == raw
    return value


def test_retained_issuance_roundtrips_only_the_exact_canonical_owner_exchange() -> None:
    value = _fixture_issuance()
    assert value.schema_id == SCHEMA
    assert decode_scoped_authorization_issuance_v3(value.canonical_bytes()) == value


def test_recomputed_result_digest_cannot_substitute_authorization_command_or_id() -> None:
    value = _fixture_issuance()
    raw = json.loads(value.returned.canonical_payload)
    raw["authorization"]["command"]["command_id"] = "substituted-command"
    raw["authorization"]["authorization_id"] = "scoped-authorization/substituted-command"
    altered_authorization = ScopedDispatchAuthorizationRecord.model_validate_json(
        json.dumps(raw["authorization"])
    )
    raw["parent"]["authorization"] = reference(
        altered_authorization.authorization_id, altered_authorization.canonical_bytes()
    ).model_dump(mode="json")
    altered_parent = ScopedPreSendRevision.model_validate_json(json.dumps(raw["parent"]))
    raw["complete_batch_fingerprint"] = _sha(
        b"chiplog.effects.scoped-authorization-batch.v3\x00"
        + raw["source_request_fingerprint"].encode()
        + altered_authorization.canonical_bytes()
        + altered_parent.canonical_bytes()
    )
    altered_result = json.dumps(
        raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    altered_returned = value.returned.model_copy(update={"canonical_payload": altered_result})
    altered_records = make_scoped_authorization_owner_records(altered_authorization, altered_parent)
    altered_fingerprint = issuance_batch_fingerprint(
        identity=value.identity,
        invocation=value.invocation,
        expected=value.expected,
        original_cut=value.original_cut,
        h1_authority_evidence=value.h1_authority_evidence,
        h1_current_scope_exchange=value.h1_current_scope_exchange,
        sent=value.sent,
        returned=altered_returned,
        output_records=altered_records,
        sent_at_ns=value.sent_at_ns,
        returned_at_ns=value.returned_at_ns,
    )
    with pytest.raises(ValueError, match=r"output records differ|fenced to its original request"):
        value.model_copy(
            update={
                "returned": altered_returned,
                "output_records": altered_records,
                "owner_batch_fingerprint": altered_fingerprint,
            }
        ).exact_retained_exchange()


def test_recomputed_result_digest_cannot_substitute_the_parent_authorization_head() -> None:
    value = _fixture_issuance()
    raw = json.loads(value.returned.canonical_payload)
    raw["parent"]["authorization"] = reference("other", b"other").model_dump(mode="json")
    altered_parent = ScopedPreSendRevision.model_validate_json(json.dumps(raw["parent"]))
    stable_authorization = ScopedDispatchAuthorizationRecord.model_validate_json(
        json.dumps(json.loads(value.returned.canonical_payload)["authorization"])
    )
    raw["complete_batch_fingerprint"] = _sha(
        b"chiplog.effects.scoped-authorization-batch.v3\x00"
        + raw["source_request_fingerprint"].encode()
        + stable_authorization.canonical_bytes()
        + altered_parent.canonical_bytes()
    )
    altered_result = json.dumps(
        raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    altered_returned = value.returned.model_copy(update={"canonical_payload": altered_result})
    altered_records = make_scoped_authorization_owner_records(stable_authorization, altered_parent)
    altered_fingerprint = issuance_batch_fingerprint(
        identity=value.identity,
        invocation=value.invocation,
        expected=value.expected,
        original_cut=value.original_cut,
        h1_authority_evidence=value.h1_authority_evidence,
        h1_current_scope_exchange=value.h1_current_scope_exchange,
        sent=value.sent,
        returned=altered_returned,
        output_records=altered_records,
        sent_at_ns=value.sent_at_ns,
        returned_at_ns=value.returned_at_ns,
    )
    with pytest.raises(ValueError, match="parent authorization differs"):
        value.model_copy(
            update={
                "returned": altered_returned,
                "output_records": altered_records,
                "owner_batch_fingerprint": altered_fingerprint,
            }
        ).exact_retained_exchange()


def test_recomputed_digest_rejects_foreign_current_scope_session() -> None:
    value = _fixture_issuance()
    foreign_caller = value.h1_current_scope_exchange.sent.caller.model_copy(
        update={"session_id": "foreign-broker-session"}
    )
    foreign_exchange = value.h1_current_scope_exchange.model_copy(
        update={
            "sent": value.h1_current_scope_exchange.sent.model_copy(
                update={"caller": foreign_caller}
            )
        }
    )
    foreign_fingerprint = issuance_batch_fingerprint(
        identity=value.identity,
        invocation=value.invocation,
        expected=value.expected,
        original_cut=value.original_cut,
        h1_authority_evidence=value.h1_authority_evidence,
        h1_current_scope_exchange=foreign_exchange,
        sent=value.sent,
        returned=value.returned,
        output_records=value.output_records,
        sent_at_ns=value.sent_at_ns,
        returned_at_ns=value.returned_at_ns,
    )
    with pytest.raises(ValueError, match="current-scope exchange is not from this broker session"):
        value.model_copy(
            update={
                "h1_current_scope_exchange": foreign_exchange,
                "owner_batch_fingerprint": foreign_fingerprint,
            }
        ).exact_retained_exchange()


def test_recomputed_digest_rejects_current_scope_route_substitution() -> None:
    value = _fixture_issuance()
    wrong_exchange = value.h1_current_scope_exchange.model_copy(
        update={
            "sent": value.h1_current_scope_exchange.sent.model_copy(
                update={"operation_id": "deployment_trust.forged"}
            )
        }
    )
    fingerprint = issuance_batch_fingerprint(
        identity=value.identity,
        invocation=value.invocation,
        expected=value.expected,
        original_cut=value.original_cut,
        h1_authority_evidence=value.h1_authority_evidence,
        h1_current_scope_exchange=wrong_exchange,
        sent=value.sent,
        returned=value.returned,
        output_records=value.output_records,
        sent_at_ns=value.sent_at_ns,
        returned_at_ns=value.returned_at_ns,
    )
    with pytest.raises(ValueError, match="current-scope wire differs"):
        value.model_copy(
            update={
                "h1_current_scope_exchange": wrong_exchange,
                "owner_batch_fingerprint": fingerprint,
            }
        ).exact_retained_exchange()
