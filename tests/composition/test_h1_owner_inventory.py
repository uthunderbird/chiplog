"""Closed native decoder inventory for the private H1 owner reader."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.projections.workspace_boundary import DisclosureLabel, SourceReference
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1ScopeKey,
    H1ScopeRelation,
)
from chiplog.composition.h1_owner_inventory import (
    H1OwnerInventoryFailure,
    _classify_leaf_scope,
    _decode_evidence_items,
    _decode_native_row,
    _evidence_items,
    _PhysicalRow,
    _validate_publication_membership,
    h1_owner_decoder_registry,
    read_h1_scoped_owner_inventory,
    unsupported_runtime_record_pairs,
)
from chiplog.composition.h1_preseal_contracts import H1Scope
from chiplog.composition.h1_selected_prepare import (
    reopen_selected_h1_workspace,
    select_h1_v3_prepare_for_candidate,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


def test_native_zero_call_pairs_have_explicit_decoders() -> None:
    """The three H1 V2 seal members cannot depend on DTO registration alone."""
    registry = h1_owner_decoder_registry()

    assert {
        ("agent_loop", "chiplog.agent-loop.execution-record.v2"),
        ("agent_loop", "chiplog.call.response-seal.v1"),
        ("agent_loop", "chiplog.recovery.frontier-registry.v1"),
        ("planning", "chiplog.planning.record.v1"),
        ("effects", "chiplog.effects.record.v1"),
    } <= set(registry)


def test_unknown_pair_is_explicitly_unsupported() -> None:
    """A pair outside the closed registry never becomes empty evidence."""
    unsupported = unsupported_runtime_record_pairs(CommonCliExecutionRuntime)

    assert unsupported == frozenset()
    scope = H1Scope("tenant", "principal", "run", "turn", (), ())
    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED") as raised:
        _decode_native_row(
            _PhysicalRow(
                "ingress",
                "unknown",
                "chiplog.unknown.v1",
                b"{}",
                1,
            ),
            scope,
        )
    assert raised.value.code == "UNSUPPORTED"


def test_orphan_physical_record_is_corrupt_even_without_a_known_schema() -> None:
    """A row cannot disappear just because no publication or decoder selected it."""
    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT") as raised:
        _validate_publication_membership(
            (),
            (_PhysicalRow("orphan", "unknown", "unknown.v1", b"exact", 4),),
            "tenant",
        )
    assert raised.value.locator == "orphan-sequences/[4]"


def test_evidence_item_metadata_retains_every_sql_column() -> None:
    item = _evidence_items(
        "tenant",
        (("source", "evidence", "fingerprint", b"payload", "PUSH", "READY", "attempt", "v1", "0"),),
    )[0]

    assert item.raw == b"payload"
    assert item.schema == "chiplog.evidence-inbox.row.v1"
    assert item.record_kind == "evidence.inbox"
    assert item.metadata_bytes == (
        b'{"attempt_id":"attempt","cursor":"0","domain":"chiplog.h1.evidence-inbox-metadata.v1",'
        b'"evidence_id":"evidence","fingerprint":"fingerprint","followup_kind":"PUSH",'
        b'"source_id":"source","state":"READY","transport_version":"v1"}'
    )


def test_evidence_item_metadata_preserves_nullable_columns_as_json_null() -> None:
    item = _evidence_items(
        "tenant",
        (("source", "evidence", "fingerprint", b"payload", "PUSH", "READY", None, None, None),),
    )[0]

    assert item.metadata_bytes is not None
    assert b'"attempt_id":null' in item.metadata_bytes
    assert b'"transport_version":null' in item.metadata_bytes
    assert b'"cursor":null' in item.metadata_bytes


def test_scoped_evidence_inbox_row_is_nonempty_after_leaf_decode() -> None:
    """Inbox rows use their registered leaf mapping before scope classification."""
    raw = b"retained evidence"
    item = _evidence_items(
        "tenant",
        (
            (
                "source",
                "evidence",
                hashlib.sha256(raw).hexdigest(),
                raw,
                "PUSH",
                "LOCAL_ACK_AUTHORIZED",
                None,
                None,
                None,
            ),
        ),
    )[0]
    source = SourceReference(
        tenant_id="tenant",
        owner="planning",
        record_id="source",
        record_version="1",
        content_digest="a" * 64,
        label_head="label",
        label=DisclosureLabel(
            lattice_version="chiplog.disclosure.v1",
            value="UNRESTRICTED",
            allowed_endpoints=(),
        ),
    )
    scope = H1Scope("tenant", "principal", "run", "turn", (), (source,))

    with pytest.raises(H1OwnerInventoryFailure, match="NONEMPTY") as raised:
        _decode_evidence_items((item,), scope)

    assert raised.value.locator == "evidence_inbox/source/evidence"


def test_typed_leaf_graph_marks_transitive_turn_call_chain_nonempty() -> None:
    run = H1ScopeKey("tenant", "run", "run", None)
    turn = H1ScopeKey("tenant", "turn", "turn", None)
    call = H1ScopeKey("tenant", "call", "call", None)
    decoded = H1DecodedInventoryItem(
        "records/call",
        (run, turn, call),
        (
            H1ScopeRelation("RUN_TURN", run, turn),
            H1ScopeRelation("TURN_CALL", turn, call),
        ),
        (),
        ("CALL",),
    )
    scope = H1Scope("tenant", "principal", "run", "turn", (), ())
    row = _PhysicalRow("call", "agent_loop", "chiplog.call.initialized-record.v1", b"raw", 1)

    with pytest.raises(H1OwnerInventoryFailure, match="NONEMPTY") as raised:
        _classify_leaf_scope(((row, decoded),), scope)

    assert raised.value.locator == "call"


def test_typed_leaf_graph_accepts_complete_disconnected_component() -> None:
    evidence = H1ScopeKey("tenant", "evidence", "other-evidence", None)
    source = H1ScopeKey("tenant", "source", "other-source", None)
    decoded = H1DecodedInventoryItem(
        "records/evidence",
        (evidence, source),
        (H1ScopeRelation("EVIDENCE_SOURCE", evidence, source),),
        (),
        ("EVIDENCE", "SOURCE"),
    )
    scope = H1Scope("tenant", "principal", "run", "turn", (), ())
    row = _PhysicalRow(
        "evidence", "evidence_journal", "chiplog.evidence_journal.record.v1", b"raw", 1
    )

    _classify_leaf_scope(((row, decoded),), scope)


@pytest.mark.asyncio
async def test_mounted_native_h1_postseal_inventory_and_selected_delta_mutant(
    tmp_path: Path,
) -> None:
    """POST_SEAL uses the actual V2 selected-cut reader, not a fabricated boundary."""
    from chiplog.composition.r14_execution_complete_seal_records import (
        RetainedExecutionCompleteSealV2,
    )
    from tests.composition.test_h1_cli_v2_selection import _admit_complete_script, _advance

    database = tmp_path / "h1-owner-inventory-post.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        committed = await runtime.advance_execution(_advance(cast(Any, initial), request))
        assert committed.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        retained = RetainedExecutionCompleteSealV2.model_validate_json(
            next(
                json.loads(raw)["execution_complete_seal"]
                for _, _, raw in runtime._loop_decisions().entries()
                if "execution_complete_seal" in json.loads(raw)
            )
        )
        reader = H1FirstPathSources(runtime)
        raw = reader._read_selected_cut(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=reader._seal_reference(retained.exchange.proposal.fan_out.response_seal),
        )
        selected_seal = reader._selected_seal(raw)
        selected_prepare, workspace = reader._resolve_workspace_closure(raw, historical=False)
        captured = raw.lineage[-2][1]

        receipt = read_h1_scoped_owner_inventory(
            runtime,
            captured=captured,
            selected_prepare=selected_prepare,
            workspace=workspace,
            phase="POST_SEAL",
            selected_seal=selected_seal,
        )
        assert receipt.scope.run_id == captured.run_id

        corrupted = replace(
            selected_seal,
            command=replace(selected_seal.command, idempotency_key="substituted-seal"),
        )
        with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
            read_h1_scoped_owner_inventory(
                runtime,
                captured=captured,
                selected_prepare=selected_prepare,
                workspace=workspace,
                phase="POST_SEAL",
                selected_seal=corrupted,
            )


@pytest.mark.asyncio
async def test_mounted_native_h1_preseal_inventory_is_positive(tmp_path: Path) -> None:
    """Exercise the real H0 → V3 capture and selected-workspace readers."""
    from tests.composition.test_common_cli_execution_runtime import _admit_complete_script

    database = tmp_path / "h1-owner-inventory.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await _admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        selected_initial = cast(Any, initial)
        started = await runtime.begin_execution(
            "hermetic-ingress",
            selected_initial.stable_run_lineage_id,
            selected_initial.selected_run_head.head,
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", selected_initial.stable_run_lineage_id, started.head
        )
        selected = select_h1_v3_prepare_for_candidate(
            runtime, captured, expected_head=captured.head
        )
        workspace = reopen_selected_h1_workspace(runtime, selected)

        receipt = read_h1_scoped_owner_inventory(
            runtime,
            captured=captured,
            selected_prepare=selected,
            workspace=workspace,
            phase="PRE_SEAL",
        )

    assert receipt.scope.run_id == captured.run_id
