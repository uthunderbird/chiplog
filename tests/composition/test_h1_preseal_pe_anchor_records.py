"""Contract tests for the inert, journal-sibling H1 P/E anchor RECORD."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal, cast

import pytest

from chiplog.capabilities.agent_loop.contracts import DisclosureLabel
from chiplog.composition.h1_preseal_pe_anchor_records import (
    _MAX_BASE64_TEXT_BYTES,
    _MAX_BYTES,
    ANCHOR_SCHEMA_ID,
    H1PresealPEAnchorRecordError,
    H1PresealPEAnchorRecordV1,
    decode_h1_preseal_pe_anchor_record,
    h1_preseal_pe_anchor_projection_identity,
)
from chiplog.composition.h1_preseal_pe_anchor_records import _b64 as _decode_b64
from chiplog.platform.broker import BrokerSession


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _head(identity: str) -> dict[str, str]:
    return {
        "identity": identity,
        "head": "head:" + identity,
        "fingerprint": _digest(identity.encode()),
    }


def _label(*, endpoint: str = "endpoint", value: str = "ENDPOINT_RESTRICTED") -> dict[str, object]:
    return {
        "lattice_version": "chiplog.disclosure.v1",
        "value": value,
        "allowed_endpoints": [endpoint] if value == "ENDPOINT_RESTRICTED" else [],
    }


def _member(index: int) -> dict[str, object]:
    return {
        "member_index": index,
        "member_digest": _digest(f"member-{index}".encode()),
        "provenance": {
            "original_head": f"provenance:{index}",
            "source_kind": "PROMPT",
            "source_locator": {
                "kind": "PREPARE_ARTIFACT",
                "prepare_entry_id": _digest(b"prepare"),
                "prepare_payload_digest": _digest(b"prepare-payload"),
                "artifact_digest": _digest(f"artifact-{index}".encode()),
            },
        },
        "disclosure": {
            "original_head": f"disclosure:{index}",
            "original_label": _label(value="UNRESTRICTED"),
            "rule": "PROMPT_JOIN",
        },
        "narrowing": {"ordinal": 0, "label": _label()},
    }


def anchor_mapping() -> dict[str, Any]:
    members = [_member(0), _member(1)]
    vector = json.dumps(members, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {
        "schema_id": ANCHOR_SCHEMA_ID,
        "version": 1,
        "binding": {
            "deployment_id": "deployment",
            "database_id": "database",
            "database_genesis_digest": _digest(b"genesis"),
            "tenant_id": "tenant",
            "principal_id": "principal",
            "run_id": "run",
            "turn_id": "run/turn/1",
            "attempt_id": "attempt",
            "prepared_run_head": "loop:" + _digest(b"prepared-run"),
            "manifest_digest": _digest(b"manifest"),
            "selected_prepare": {
                "entry_id": _digest(b"prepare"),
                "payload_digest": _digest(b"prepare-payload"),
            },
            "publication": {
                "operation_kind": "execution.complete-seal.h1.v2",
                "operation_id": "operation",
                "expected_head": 4,
                "request_fingerprint": _digest(b"request"),
            },
            "selected_response_seal": _head("response-seal"),
            "owner_asof": {"tenant_id": "tenant", "owner_head": _digest(b"owner-head")},
            "member_count": len(members),
            "member_vector_digest": _digest(vector),
        },
        "p": {
            "scope_ref": _head("scope"),
            "scope_bytes_base64": _b64(b'{"scope":"accepted"}'),
            "policy_ref": _head("policy"),
            "policy_bytes_base64": _b64(b'{"policy":"accepted"}'),
            "recipient": {
                "provider_id": "provider",
                "account_id": "account",
                "recipient_id": "recipient",
                "endpoint": _head("endpoint"),
                "canonical_address_base64": _b64(b"recipient@example.test"),
                "credential_binding": _head("credential"),
            },
            "custody_entry_generation": 7,
            "custody_entry_digest": _digest(b"custody"),
            "source_signature_digest": _digest(b"signature"),
            "accepted_issue_wire_digest": _digest(b"issued"),
            "accepted_current_wire_digest": _digest(b"current"),
        },
        "e_members": members,
        "e_worker": {
            "runtime_instance_id": "runtime",
            "owner_route_generation": "generation",
            "worker_session_id": "session",
            "owner_id": "agent_loop",
            "run_head": "loop:" + _digest(b"prepared-run"),
            "fence_kind": "NON_SCHEDULER",
        },
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def test_anchor_record_roundtrips_exact_canonical_bytes() -> None:
    original = H1PresealPEAnchorRecordV1.from_mapping(anchor_mapping())
    raw = original.canonical_bytes()

    decoded = decode_h1_preseal_pe_anchor_record(raw)

    assert decoded == original
    assert decoded.canonical_bytes() == raw
    assert decoded.as_dict() == anchor_mapping()
    assert "neither authority nor trust" in (decoded.__doc__ or "").lower()


@pytest.mark.parametrize(
    "raw",
    (
        b'{"schema_id":"a","schema_id":"b"}',
        b'{"version":1}',
        b'{"version":1.0}',
        b'{"version":-0}',
        b'{"version":NaN}',
        b"\xef\xbb\xbf{}",
        b' {"version":1}',
        b"[]",
    ),
)
def test_anchor_record_denies_noncanonical_or_duplicate_json(raw: bytes) -> None:
    with pytest.raises(H1PresealPEAnchorRecordError):
        decode_h1_preseal_pe_anchor_record(raw)


@pytest.mark.parametrize(
    "path",
    (
        ("unexpected",),
        ("binding", "unexpected"),
        ("p", "unexpected"),
        ("e_members", 0, "unexpected"),
        ("e_worker", "unexpected"),
    ),
)
def test_anchor_record_denies_extra_keys_at_every_level(path: tuple[str | int, ...]) -> None:
    value = anchor_mapping()
    target: Any = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = "injected"

    with pytest.raises(H1PresealPEAnchorRecordError):
        H1PresealPEAnchorRecordV1.from_mapping(value)


@pytest.mark.parametrize(
    "path",
    (
        ("binding", "member_vector_digest"),
        ("p", "scope_bytes_base64"),
        ("p", "accepted_current_wire_digest"),
        ("e_members", 0, "narrowing"),
        ("e_worker", "worker_session_id"),
    ),
)
def test_anchor_record_denies_missing_residual_fields(path: tuple[str | int, ...]) -> None:
    value = anchor_mapping()
    target: Any = value
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]

    with pytest.raises(H1PresealPEAnchorRecordError):
        H1PresealPEAnchorRecordV1.from_mapping(value)


def test_anchor_record_requires_ordered_complete_member_vector() -> None:
    value = anchor_mapping()
    value["e_members"] = [_member(1), _member(0)]
    value["binding"]["member_vector_digest"] = _digest(_canonical(value["e_members"]))
    with pytest.raises(H1PresealPEAnchorRecordError, match="member"):
        H1PresealPEAnchorRecordV1.from_mapping(value)

    value = anchor_mapping()
    value["binding"]["member_count"] = 3
    with pytest.raises(H1PresealPEAnchorRecordError, match="member"):
        H1PresealPEAnchorRecordV1.from_mapping(value)


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value["e_members"].pop(),
        lambda value: value["e_members"][1].__setitem__("member_index", 3),
        lambda value: value["e_members"][0]["provenance"]["source_locator"].__setitem__(
            "prepare_entry_id", _digest(b"other")
        ),
        lambda value: value["e_worker"].__setitem__("run_head", "loop:" + _digest(b"other")),
        lambda value: value["binding"]["owner_asof"].__setitem__("tenant_id", "other"),
    ),
)
def test_anchor_record_denies_malformed_cross_bindings(
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    value = anchor_mapping()
    mutator(value)
    with pytest.raises(H1PresealPEAnchorRecordError):
        H1PresealPEAnchorRecordV1.from_mapping(value)


def test_anchor_projection_identity_is_versioned_and_bound_to_anchor_coordinates() -> None:
    anchor = H1PresealPEAnchorRecordV1.from_mapping(anchor_mapping())
    decision_id = _digest(b"decision")
    member_zero = h1_preseal_pe_anchor_projection_identity(
        anchor, selected_decision_id=decision_id, role="E_MEMBER", member_index=0
    )

    assert member_zero == h1_preseal_pe_anchor_projection_identity(
        anchor, selected_decision_id=decision_id, role="E_MEMBER", member_index=0
    )
    assert member_zero != h1_preseal_pe_anchor_projection_identity(
        anchor, selected_decision_id=decision_id, role="E_MEMBER", member_index=1
    )
    assert member_zero != h1_preseal_pe_anchor_projection_identity(
        anchor, selected_decision_id=decision_id, role="P"
    )
    assert not member_zero.startswith("h1-evidence:")
    assert not member_zero.startswith("journal:")

    assert member_zero != h1_preseal_pe_anchor_projection_identity(
        anchor,
        selected_decision_id=_digest(b"other-decision"),
        role="E_MEMBER",
        member_index=0,
    )


@pytest.mark.parametrize(
    "role,index",
    (("E_MEMBER", None), ("P", 0), ("unknown", None)),
)
def test_anchor_projection_identity_denies_invalid_role_ordinal_pair(
    role: str, index: int | None
) -> None:
    anchor = H1PresealPEAnchorRecordV1.from_mapping(anchor_mapping())
    with pytest.raises(H1PresealPEAnchorRecordError):
        h1_preseal_pe_anchor_projection_identity(
            anchor,
            selected_decision_id=_digest(b"decision"),
            role=cast(Literal["P", "E_MEMBER", "E_WORKER"], role),
            member_index=index,
        )


@pytest.mark.parametrize(
    "key",
    ("journal_locator", "evidence_locator", "exact_head_id", "record_id", "identity"),
)
def test_anchor_record_denies_old_e_journal_locator_or_identity(key: str) -> None:
    value = anchor_mapping()
    value["e_members"][0][key] = "h1-evidence:member:legacy"
    with pytest.raises(H1PresealPEAnchorRecordError):
        H1PresealPEAnchorRecordV1.from_mapping(value)


def test_anchor_record_denies_native_payload_bytes_smuggled_as_residual() -> None:
    value = anchor_mapping()
    native_payload = b'{"native":"prepared payload"}'
    value["binding"]["selected_prepare"]["payload_digest"] = _digest(native_payload)
    value["e_members"][0]["provenance"]["source_locator"]["prepare_payload_digest"] = _digest(
        native_payload
    )
    value["p"]["scope_bytes_base64"] = _b64(native_payload)
    with pytest.raises(H1PresealPEAnchorRecordError, match="native"):
        H1PresealPEAnchorRecordV1.from_mapping(value)


def test_anchor_record_has_no_native_payload_bearing_fields() -> None:
    anchor = H1PresealPEAnchorRecordV1.from_mapping(anchor_mapping())
    rendered = json.dumps(anchor.as_dict())
    for forbidden in (
        "payload_base64",
        "record_bytes",
        "native_bytes",
        "member_bytes",
        "source_bytes",
    ):
        assert forbidden not in rendered


def test_anchor_record_rejects_noncanonical_embedded_scope_policy_bytes() -> None:
    value = anchor_mapping()
    value["p"]["scope_bytes_base64"] = _b64(b'{ "scope":"accepted" }')
    with pytest.raises(H1PresealPEAnchorRecordError):
        H1PresealPEAnchorRecordV1.from_mapping(value)


@pytest.mark.parametrize("field", ("scope_bytes_base64", "policy_bytes_base64"))
def test_anchor_record_accepts_large_canonical_embedded_scope_policy_bytes(field: str) -> None:
    value = anchor_mapping()
    embedded = b'{"retained":"' + b"x" * 5_000 + b'"}'
    encoded = _b64(embedded)
    assert len(encoded) > 4_096
    value["p"][field] = encoded

    record = H1PresealPEAnchorRecordV1.from_mapping(value)

    assert len(record.canonical_bytes()) <= _MAX_BYTES


def test_anchor_record_base64_field_rejects_canonical_text_above_its_bound() -> None:
    encoded = _b64(b"x" * ((_MAX_BASE64_TEXT_BYTES // 4) * 3 + 3))
    assert len(encoded) > _MAX_BASE64_TEXT_BYTES

    with pytest.raises(H1PresealPEAnchorRecordError, match="bounded base64"):
        _decode_b64(encoded, "p.scope_bytes_base64")


def test_anchor_record_accepts_current_p_e_label_and_worker_wire_shapes() -> None:
    """Use the concrete DTO payloads emitted by the current P/E owner paths."""
    value = anchor_mapping()
    original = DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()).model_dump(mode="json")
    narrowed = DisclosureLabel(
        value="ENDPOINT_RESTRICTED", allowed_endpoints=("endpoint",)
    ).model_dump(mode="json")
    session = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="route-generation",
        owner_id="agent_loop",
        session_id="session",
    )
    value["e_members"][0]["disclosure"]["original_label"] = original
    value["e_members"][0]["narrowing"]["label"] = narrowed
    value["e_worker"]["owner_route_generation"] = session.generation_id
    value["binding"]["member_vector_digest"] = _digest(_canonical(value["e_members"]))

    record = H1PresealPEAnchorRecordV1.from_mapping(value)

    worker = cast(dict[str, object], record.as_dict()["e_worker"])
    assert worker["owner_route_generation"] == "route-generation"
