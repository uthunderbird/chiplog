"""Closed H1 inventory admission for physically published workspace-policy V2."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

import chiplog.composition.h1_owner_inventory as owner_inventory
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.h1_owner_inventory import (
    _H1_KNOWN_NON_OWNER_OPERATIONS,
    _PhysicalRow,
    _validate_v2_workspace_policy_publications,
)
from chiplog.composition.h1_preseal_contracts import H1Scope
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1


def _policy_row(
    *,
    owner: str = "workspace_policy",
    schema: str = "chiplog.workspace.policy.v2",
    record_id: str = "workspace-policy:identity:payload",
    raw: bytes = b"canonical V2 policy",
    sequence: int = 7,
) -> _PhysicalRow:
    return _PhysicalRow(record_id, owner, schema, raw, sequence)


def _publication(
    row: _PhysicalRow,
    *,
    operation: str = "workspace.policy.h1.v2",
    key: str | None = None,
    fingerprint: str | None = None,
    record_ids: str | None = None,
) -> tuple[object, ...]:
    return (
        operation,
        row.record_id if key is None else key,
        hashlib.sha256(row.raw).hexdigest() if fingerprint is None else fingerprint,
        row.sequence,
        row.record_id if record_ids is None else record_ids,
    )


def test_v2_workspace_policy_is_a_closed_non_owner_operation_with_one_exact_member() -> None:
    row = _policy_row()

    _validate_v2_workspace_policy_publications((_publication(row),), (row,))

    assert "workspace.policy.h1.v2" in _H1_KNOWN_NON_OWNER_OPERATIONS


@pytest.mark.parametrize(
    "mutation",
    ("operation", "owner", "schema", "extra_row", "key", "fingerprint"),
)
def test_v2_workspace_policy_publication_mutations_refuse(mutation: str) -> None:
    row = _policy_row()
    publication = _publication(row)
    rows = (row,)
    if mutation == "operation":
        publication = _publication(row, operation="workspace.policy")
    elif mutation == "owner":
        rows = (_policy_row(owner="other"),)
    elif mutation == "schema":
        rows = (_policy_row(schema="chiplog.workspace.policy.v1"),)
    elif mutation == "extra_row":
        publication = _publication(row, record_ids=row.record_id + "\nextra")
        rows = (row, _PhysicalRow("extra", "other", "other.v1", b"extra", row.sequence))
    elif mutation == "key":
        publication = _publication(row, key="forged")
    else:
        publication = _publication(row, fingerprint="0" * 64)

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        _validate_v2_workspace_policy_publications((publication,), rows)


def test_v2_policy_row_under_legacy_operation_refuses() -> None:
    row = _policy_row()

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        _validate_v2_workspace_policy_publications(
            (_publication(row, operation="workspace.policy"),), (row,)
        )


def test_v1_workspace_policy_is_not_given_the_v2_publication_rule() -> None:
    row = _PhysicalRow("v1", "workspace_policy", "chiplog.workspace.policy.v1", b"v1", 3)

    _validate_v2_workspace_policy_publications(
        (("workspace.policy", "v1", hashlib.sha256(b"v1").hexdigest(), 3, "v1"),), (row,)
    )


def test_workspace_inventory_item_uses_the_loaded_v2_original_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"retained V2 original"
    reference = H1WorkspaceIssuanceRefV1(
        tenant="tenant",
        batch_id="batch",
        entry_id="a" * 64,
        payload_digest=hashlib.sha256(raw).hexdigest(),
    )

    class Journal:
        def load(self, received: H1WorkspaceIssuanceRefV1) -> object:
            assert received == reference
            return SimpleNamespace(
                schema_id="chiplog.execution.h1-original-workspace-issuance.v2",
                canonical_bytes=lambda: raw,
            )

    class Workspace:
        def __init__(self, runtime: object) -> None:
            assert runtime == "runtime"

        def open_h1_workspace_issuance(self) -> Journal:
            return Journal()

    import chiplog.composition.r13_workspace as r13_workspace

    monkeypatch.setattr(r13_workspace, "R13Workspace", Workspace)
    closure = cast(Any, SimpleNamespace(issuance=reference))

    item = owner_inventory._workspace_inventory_item("runtime", closure)

    assert item.schema == "chiplog.execution.h1-original-workspace-issuance.v2"


def test_workspace_merge_passes_the_authenticated_issuance_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = H1WorkspaceIssuanceRefV1(
        tenant="tenant", batch_id="batch", entry_id="a" * 64, payload_digest="b" * 64
    )
    seen: list[H1WorkspaceIssuanceRefV1 | None] = []

    def decode(
        item: H1RawInventoryItem, *, issuance_ref: H1WorkspaceIssuanceRefV1 | None
    ) -> H1DecodedInventoryItem:
        seen.append(issuance_ref)
        return H1DecodedInventoryItem(item.locator, (), (), (), ())

    monkeypatch.setattr(owner_inventory, "decode_h1_workspace_planning_scope", decode)
    scope = H1Scope("tenant", "principal", "run", "turn", (), ())
    item = H1RawInventoryItem(
        "WORKSPACE_SOURCE",
        "workspace-issuance/" + reference.entry_id,
        "tenant",
        "workspace_issuance",
        "chiplog.execution.h1-original-workspace-issuance.v2",
        None,
        reference.entry_id,
        reference.payload_digest,
        b"original",
    )

    assert owner_inventory._merge_workspace_sources(scope, item, issuance_ref=reference) == scope
    assert seen == [reference]
