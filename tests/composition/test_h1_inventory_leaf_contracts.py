"""Public carrier contracts shared by the H1 inventory leaves."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields
from typing import cast

import pytest

from chiplog.composition.h1_inventory_leaf_contracts import (
    H1_EVIDENCE_INBOX_METADATA_DOMAIN,
    H1_INVENTORY_FAMILIES,
    H1_SCOPE_NAMESPACES,
    H1_SCOPE_RELATIONS,
    SURFACES,
    H1DecodedInventoryItem,
    H1InventoryFamily,
    H1LeafRegistration,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
    ScopeNamespace,
    ScopeRelationName,
    Surface,
)


def test_leaf_contracts_have_a_closed_public_surface() -> None:
    """Leaves cannot invent carriers or scope vocabulary outside the frozen seam."""
    assert SURFACES == (
        "PHYSICAL",
        "OWNER_COMMAND",
        "LOOP_ENTRY",
        "EVIDENCE_INBOX",
        "CUSTODY_ENTRY",
        "WORKSPACE_SOURCE",
    )
    assert H1_SCOPE_NAMESPACES == (
        "run",
        "turn",
        "call",
        "intent",
        "mandate",
        "obligation",
        "reduction",
        "evidence",
        "source",
        "record",
        "effect",
        "dispatch",
        "delivery",
        "outcome",
        "proposal",
        "workspace",
        "planning",
    )
    assert tuple(name.upper() for name in H1_SCOPE_NAMESPACES) == H1_INVENTORY_FAMILIES
    assert set(H1_SCOPE_RELATIONS) == {
        "RUN_TURN",
        "TURN_CALL",
        "CALL_INTENT",
        "INTENT_MANDATE",
        "MANDATE_OBLIGATION",
        "OBLIGATION_REDUCTION",
        "EVIDENCE_SOURCE",
        "RECORD_SOURCE",
        "RECORD_RUN",
        "RECORD_TURN",
        "EFFECT_INTENT",
        "DISPATCH_EFFECT",
        "DELIVERY_DISPATCH",
        "OUTCOME_DELIVERY",
        "PROPOSAL_INTENT",
        "WORKSPACE_SOURCE",
        "PLANNING_SOURCE",
    }


def test_raw_item_retains_each_exact_occurrence_even_when_bytes_repeat() -> None:
    """Identity is its enumerated locator, never a digest or content-only key."""
    raw = b'{"same":"bytes"}'
    first = H1RawInventoryItem(
        "PHYSICAL", "records/7", "tenant", "owner", "schema.v1", None, "record-7", None, raw
    )
    second = H1RawInventoryItem(
        "PHYSICAL", "records/8", "tenant", "owner", "schema.v1", None, "record-8", None, raw
    )

    assert first.raw == raw
    assert second.raw == raw
    assert first.locator != second.locator
    assert first != second
    with pytest.raises(FrozenInstanceError):
        first.locator = "records/rewritten"  # type: ignore[misc]


def test_evidence_inbox_metadata_is_exact_and_has_a_closed_typed_field_set() -> None:
    metadata = {
        "domain": H1_EVIDENCE_INBOX_METADATA_DOMAIN,
        "followup_kind": "delivery",
        "state": "ready",
        "attempt_id": "attempt-1",
        "transport_version": "v1",
        "cursor": "cursor-1",
        "fingerprint": "digest",
        "source_id": "source-1",
        "evidence_id": "evidence-1",
    }
    exact = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    item = H1RawInventoryItem(
        "EVIDENCE_INBOX", "inbox/1", "tenant", "owner", "schema.v1", None, None, None, b"raw", exact
    )

    assert item.metadata_bytes == exact
    nullable = {**metadata, "attempt_id": None, "transport_version": None, "cursor": None}
    assert (
        H1RawInventoryItem(
            "EVIDENCE_INBOX",
            "inbox/nullable",
            "tenant",
            "owner",
            "schema.v1",
            None,
            None,
            None,
            b"raw",
            json.dumps(nullable, sort_keys=True, separators=(",", ":")).encode(),
        ).metadata_bytes
        is not None
    )
    with pytest.raises(ValueError, match="field set"):
        H1RawInventoryItem(
            "EVIDENCE_INBOX",
            "inbox/2",
            "tenant",
            "owner",
            "schema.v1",
            None,
            None,
            None,
            b"raw",
            b"{}",
        )


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: H1RawInventoryItem(
                cast(Surface, "OTHER"),
                "row/1",
                "tenant",
                "owner",
                "schema",
                None,
                None,
                None,
                b"raw",
            ),
            "surface",
        ),
        (
            lambda: H1ScopeKey("tenant", cast(ScopeNamespace, "other"), "identity", None),
            "namespace",
        ),
        (
            lambda: H1DecodedInventoryItem(
                "row/1", (), (), (), (cast(H1InventoryFamily, "OTHER"),)
            ),
            "family",
        ),
    ],
)
def test_leaf_contracts_reject_unknown_closed_values(
    factory: Callable[[], object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_relation_requires_a_closed_name_and_compatible_scope_endpoints() -> None:
    run = H1ScopeKey("tenant", "run", "run-1", "head-1")
    turn = H1ScopeKey("tenant", "turn", "turn-1", "head-2")
    call = H1ScopeKey("tenant", "call", "call-1", None)
    record = H1ScopeKey("tenant", "record", "record-1", "head-3")

    assert H1ScopeRelation("RUN_TURN", run, turn).subject is run
    assert H1ScopeRelation("RECORD_RUN", record, run).target is run
    assert H1ScopeRelation("RECORD_TURN", record, turn).target is turn
    with pytest.raises(ValueError, match="relation"):
        H1ScopeRelation(cast(ScopeRelationName, "OTHER"), run, turn)
    with pytest.raises(ValueError, match="endpoints"):
        H1ScopeRelation("RUN_TURN", run, call)


def test_decoded_result_keeps_the_leaf_locator_and_closed_families() -> None:
    result = H1DecodedInventoryItem(
        "owner/decision/4/member/2",
        (H1ScopeKey("tenant", "call", "call-1", None),),
        (),
        (),
        ("CALL",),
    )

    assert result.locator == "owner/decision/4/member/2"
    assert result.families == ("CALL",)
    assert [field.name for field in fields(H1DecodedInventoryItem)] == [
        "locator",
        "identities",
        "relations",
        "source_refs",
        "families",
    ]


def test_registration_keeps_none_as_its_explicit_wildcard_marker() -> None:
    registration = H1LeafRegistration("OWNER_COMMAND", "owner", "schema.v1", None, "decoder.v1")
    item = H1RawInventoryItem(
        "OWNER_COMMAND", "decision/2", "tenant", "owner", "schema.v1", None, None, None, b"exact"
    )

    assert registration.record_kind is None
    assert item.record_kind is None
    assert [field.name for field in fields(H1LeafRegistration)] == [
        "surface",
        "owner",
        "schema",
        "record_kind",
        "decoder_version",
    ]


def test_shared_inventory_failure_preserves_the_b_public_error_api() -> None:
    failure = H1OwnerInventoryFailure.unsupported(
        family="CALL", owner="agent_loop", schema="schema.v1", locator="row/1"
    )

    assert failure.code == "UNSUPPORTED"
    assert failure.family == "CALL"
    assert failure.owner == "agent_loop"
    assert failure.schema == "schema.v1"
    assert failure.locator == "row/1"
    assert (
        str(failure) == "UNSUPPORTED: family=CALL owner=agent_loop schema=schema.v1 locator=row/1"
    )
    incomplete = H1OwnerInventoryFailure.incomplete(family="EVIDENCE", locator="inbox/1")
    assert (incomplete.code, incomplete.owner, incomplete.schema) == ("INCOMPLETE", "", "")
