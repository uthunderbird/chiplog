"""Focused raw-item contract tests for the H1 CALL inventory leaf."""

from __future__ import annotations

import hashlib

import pytest

from chiplog.composition.h1_inventory_call import REGISTRATIONS, decode_h1_call_scope
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
)
from chiplog.composition.r14_fanout_contracts import INITIALIZED_SCHEMA
from tests.support.execution_cancellation import retained_shape
from tests.support.recovery_records import records


def _lifecycle(
    schema: str, raw: bytes, *, locator: str = "physical/one", record_id: str | None = None
) -> H1RawInventoryItem:
    digest = hashlib.sha256(raw).hexdigest()
    return H1RawInventoryItem(
        "PHYSICAL",
        locator,
        "tenant",
        "agent_loop",
        schema,
        None,
        record_id or "record:" + digest,
        digest,
        raw,
    )


async def test_initialized_call_preserves_locator_and_run_turn_call_edges() -> None:
    retained = await retained_shape()
    initialized = retained.request.initialized_record
    item = _lifecycle(INITIALIZED_SCHEMA, initialized.canonical_bytes())

    decoded = decode_h1_call_scope(item)

    assert decoded.locator == item.locator
    assert {key.namespace for key in decoded.identities} == {"record", "run", "turn", "call"}
    assert {relation.relation for relation in decoded.relations} == {"RUN_TURN", "TURN_CALL"}
    assert decoded.source_refs == ()


@pytest.mark.parametrize(
    ("schema", "record_name"),
    (
        (CANCELLATION_SCHEMA, "terminal"),
        (NOT_EXECUTED_SCHEMA, "result"),
    ),
)
async def test_cancellation_members_decode_before_scope_filtering(
    schema: str, record_name: str
) -> None:
    retained = await retained_shape()
    record = getattr(retained.proposal, record_name)

    decoded = decode_h1_call_scope(_lifecycle(schema, record.canonical_bytes()))

    assert decoded.families == ("CALL", "RECORD")
    assert any(key.namespace == "call" for key in decoded.identities)


def test_recovery_obligation_record_preserves_typed_call_obligation_and_reduction_identities() -> (
    None
):
    member = records().members["ORIGINAL_RESOLUTION_BASIS"]
    item = H1RawInventoryItem(
        "PHYSICAL",
        "recovery/basis",
        "tenant",
        member.owner,
        member.schema_id,
        member.record_kind,
        member.record_id,
        member.fingerprint,
        member.canonical_record_bytes,
    )

    decoded = decode_h1_call_scope(item)

    assert decoded.locator == "recovery/basis"
    assert {key.namespace for key in decoded.identities} >= {
        "record",
        "call",
        "obligation",
        "reduction",
    }
    assert decoded.families == ("CALL", "OBLIGATION", "REDUCTION", "RECORD")


def test_registered_lifecycle_noncanonical_bytes_are_corrupt() -> None:
    raw = b'{"kind":"CALL_INITIALIZED_V1"}'
    with pytest.raises(H1OwnerInventoryFailure) as raised:
        decode_h1_call_scope(_lifecycle(INITIALIZED_SCHEMA, raw))
    assert raised.value.code == "CORRUPT"


def test_unknown_kind_or_schema_is_unsupported() -> None:
    item = H1RawInventoryItem(
        "PHYSICAL",
        "unknown",
        "tenant",
        "agent_loop",
        "unknown.v1",
        "UNKNOWN",
        "id",
        "a" * 64,
        b"{}",
    )
    with pytest.raises(H1OwnerInventoryFailure) as raised:
        decode_h1_call_scope(item)
    assert raised.value.code == "UNSUPPORTED"


def test_registered_recovery_identity_mismatch_is_corrupt() -> None:
    member = records().members["ORIGINAL_OBLIGATION_CLOSURE"]
    item = H1RawInventoryItem(
        "PHYSICAL",
        "recovery/closure",
        "tenant",
        member.owner,
        member.schema_id,
        member.record_kind,
        "other-record",
        member.fingerprint,
        member.canonical_record_bytes,
    )
    with pytest.raises(H1OwnerInventoryFailure) as raised:
        decode_h1_call_scope(item)
    assert raised.value.code == "CORRUPT"


def test_non_call_recovery_row_remains_unsupported_for_its_own_leaf() -> None:
    member = records().members["SEALED_ACCOUNTING"]
    item = H1RawInventoryItem(
        "PHYSICAL",
        "recovery/accounting",
        "tenant",
        member.owner,
        member.schema_id,
        member.record_kind,
        member.record_id,
        member.fingerprint,
        member.canonical_record_bytes,
    )
    with pytest.raises(H1OwnerInventoryFailure) as raised:
        decode_h1_call_scope(item)
    assert raised.value.code == "UNSUPPORTED"


def test_registration_is_explicit_for_five_lifecycle_schemas_and_call_recovery_rows() -> None:
    pairs = {(entry.schema, entry.record_kind) for entry in REGISTRATIONS}
    assert {
        (INITIALIZED_SCHEMA, None),
        (CANCELLATION_SCHEMA, None),
        (NOT_EXECUTED_SCHEMA, None),
        ("chiplog.loop.original-obligation-closure.v1", "ORIGINAL_OBLIGATION_CLOSURE"),
        ("chiplog.loop.semantic-reduction.v1", "LOOP_SEMANTIC_REDUCTION"),
    } <= pairs
