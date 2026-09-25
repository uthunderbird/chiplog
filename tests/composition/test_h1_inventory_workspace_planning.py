"""Focused contracts for the H1 workspace/planning raw inventory leaf."""

from __future__ import annotations

import hashlib

import pytest

from chiplog.capabilities.planning._planning import _record
from chiplog.composition.h1_inventory_leaf_contracts import H1RawInventoryItem
from chiplog.composition.h1_inventory_workspace_planning import (
    REGISTRATIONS,
    decode_h1_workspace_planning_scope,
)
from chiplog.domain_primitives import RecordId, TenantId


def _planning_item(
    *,
    record_kind: str = "chiplog.planning.intention_line",
    raw: bytes | None = None,
    record_id: str = "line-1",
    fingerprint: str | None = None,
) -> H1RawInventoryItem:
    payload = _record(
        RecordId(TenantId("tenant"), record_id),
        record_kind,
        {
            "authority_act_id": "authority",
            "initial_revision_id": {"tenant_id": "tenant", "value": "revision-1"},
            "principal_id": "principal",
            "tenant_id": "tenant",
        },
    ).canonical_bytes
    exact = payload if raw is None else raw
    return H1RawInventoryItem(
        "PHYSICAL",
        "records/7",
        "tenant",
        "planning",
        "chiplog.planning.record.v1",
        record_kind,
        record_id,
        hashlib.sha256(exact).hexdigest() if fingerprint is None else fingerprint,
        exact,
    )


def test_planning_record_preserves_identity_and_benign_empty_frontier() -> None:
    decoded = decode_h1_workspace_planning_scope(_planning_item())

    assert decoded.locator == "records/7"
    assert decoded.identities[0].namespace == "planning"
    assert decoded.identities[0].identity == "line-1"
    assert decoded.source_refs == ()
    assert decoded.relations == ()
    assert decoded.families == ()


@pytest.mark.parametrize(
    "item",
    [
        _planning_item(raw=b'{"not":"canonical planning"}'),
        _planning_item(raw=_planning_item().raw + b" "),
        _planning_item(fingerprint="0" * 64),
        H1RawInventoryItem(
            "PHYSICAL",
            "records/unknown",
            "tenant",
            "planning",
            "chiplog.planning.record.v1",
            "chiplog.planning.unknown",
            "unknown",
            None,
            b"{}",
        ),
    ],
)
def test_invalid_registered_bytes_or_identity_fail_closed(item: H1RawInventoryItem) -> None:
    with pytest.raises(RuntimeError):
        decode_h1_workspace_planning_scope(item)


def test_unknown_outer_pair_is_unsupported() -> None:
    item = H1RawInventoryItem(
        "PHYSICAL",
        "records/9",
        "tenant",
        "evidence_journal",
        "chiplog.evidence_journal.record.v1",
        None,
        None,
        None,
        b"{}",
    )

    with pytest.raises(RuntimeError, match="UNSUPPORTED"):
        decode_h1_workspace_planning_scope(item)


def test_registrations_cover_each_supported_planning_and_workspace_kind_once() -> None:
    keys = [
        (entry.surface, entry.owner, entry.schema, entry.record_kind) for entry in REGISTRATIONS
    ]

    assert len(keys) == len(set(keys))
    assert (
        "PHYSICAL",
        "planning",
        "chiplog.planning.record.v1",
        "chiplog.planning.intention_line",
    ) in keys
    assert ("PHYSICAL", "evidence_journal", "chiplog.evidence_journal.record.v1", None) not in keys
    assert (
        "WORKSPACE_SOURCE",
        "workspace_issuance",
        "chiplog.execution.h1-original-workspace-issuance.v1",
        None,
    ) in keys
