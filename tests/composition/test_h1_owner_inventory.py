"""Closed native decoder inventory for the private H1 owner reader."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    DELIVERY_ACCEPTANCE_SCHEMA,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionTerminalManifest,
)
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import WORK_RECORD_CONTRACTS
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    SCHEMA_ID as H1_LOCAL_INTENT_SCHEMA,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    OWNER as CONVERSATION_OWNER,
)
from chiplog.capabilities.projections.workspace_boundary import DisclosureLabel, SourceReference
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_common_cli_execution_runtime,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_inventory_completion import (
    REGISTRATIONS as COMPLETION_REGISTRATIONS,
)
from chiplog.composition.h1_inventory_completion import (
    H1CompletionInventoryCohort,
    decode_h1_completion_scope,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1DecodedInventoryItem,
    H1RawInventoryItem,
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
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
    complete_acceptance_command,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PublicationVerificationMode,
    SQLiteMaterializer,
    VerifiedOwnerPublication,
)
from tests.support.completion_assembly import accepted_completion_fixture
from tests.support.h1_cli_execution import admit_complete_script, advance


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


def test_h1_completion_writer_pairs_have_closed_inventory_decoders() -> None:
    """Every admitted H1 completion member has a closed inventory decoder."""
    terminal_schema = cast(str, ExecutionTerminalManifest.model_fields["schema_id"].default)
    expected = {
        ("agent_loop", DELIVERY_ACCEPTANCE_SCHEMA),
        ("agent_loop", terminal_schema),
        ("agent_loop", "chiplog.agent-loop.execution-record.v3"),
        *((contract.owner, contract.schema_id) for contract in WORK_RECORD_CONTRACTS),
        (CONVERSATION_OWNER, ACCEPTED_ENTRY_SCHEMA),
        ("effects", H1_LOCAL_INTENT_SCHEMA),
    }

    assert expected <= set(CommonCliExecutionRuntime._record_schema_variants)
    assert expected <= set(h1_owner_decoder_registry())
    assert unsupported_runtime_record_pairs(CommonCliExecutionRuntime) == frozenset()


@pytest.mark.asyncio
async def test_synthetic_completion_leaf_cohort_decodes_members_as_run_scoped() -> None:
    """A synthetic leaf cohort checks decoding only; it carries no H1 authority claim."""
    fixture = await accepted_completion_fixture("v3", "nonempty")
    cohort = H1CompletionInventoryCohort(
        fixture.assembly.original_completion_request.run.run_id,
        {
            record.record_id: (
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                record.fingerprint,
            )
            for record in fixture.batch.complete_records
        },
    )
    completion_pairs = {
        (registration.owner, registration.schema) for registration in COMPLETION_REGISTRATIONS
    }
    decoded = [
        decode_h1_completion_scope(
            H1RawInventoryItem(
                "PHYSICAL",
                "records/" + record.record_id,
                fixture.batch.identity.tenant_id,
                record.owner,
                record.schema_id,
                record.record_kind,
                record.record_id,
                record.fingerprint,
                record.canonical_bytes,
            ),
            cohort,
        )
        for record in fixture.batch.complete_records
        if (record.owner, record.schema_id)
        in completion_pairs
    ]

    assert decoded
    assert all(item.identities[1].namespace == "run" for item in decoded)
    assert all(item.identities[1].identity == cohort.run_id for item in decoded)


@pytest.mark.asyncio
async def test_completion_v3_run_must_match_the_reconciled_cohort_run() -> None:
    """The V3 Run member cannot acquire ancestry from a different cohort."""
    fixture = await accepted_completion_fixture("v3", "nonempty")
    run = next(
        record
        for record in fixture.batch.complete_records
        if (record.owner, record.schema_id)
        == ("agent_loop", "chiplog.agent-loop.execution-record.v3")
    )
    cohort = H1CompletionInventoryCohort(
        "different-run",
        {
            run.record_id: (
                run.owner,
                run.schema_id,
                run.canonical_bytes,
                run.fingerprint,
            )
        },
    )

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_completion_scope(
            H1RawInventoryItem(
                "PHYSICAL",
                "records/" + run.record_id,
                fixture.batch.identity.tenant_id,
                run.owner,
                run.schema_id,
                run.record_kind,
                run.record_id,
                run.fingerprint,
                run.canonical_bytes,
            ),
            cohort,
        )


@pytest.mark.asyncio
async def test_generic_completion_producer_batch_writes_through_test_local_registry(
    tmp_path: Path,
) -> None:
    """A generic acceptance batch exercises writer mechanics, outside H1 issuance."""
    fixture = await accepted_completion_fixture("v3", "nonempty")
    batch = fixture.batch
    command = complete_acceptance_command(
        RetainedCompleteAcceptanceExchangeV1(
            assembly=fixture.assembly,
            batch=batch,
            expected_head=batch.expected.tenant_frontier,
            predecessor_commitment=batch.expected.expected_materialization_commitment,
        )
    )

    class Resolver:
        def __init__(self) -> None:
            self.modes: list[PublicationVerificationMode] = []
            self.resulting: str | None = None

        def verify(
            self, candidate: object, mode: PublicationVerificationMode
        ) -> VerifiedOwnerPublication:
            assert candidate == guarded
            self.modes.append(mode)
            if mode == "PRECOMMIT":
                assert self.resulting is not None
                return VerifiedOwnerPublication.from_command(
                    guarded,
                    binding_fingerprint="fixture-binding",
                    selected_identity="fixture-selected",
                    selected_fingerprint="f" * 64,
                    expected_resulting=self.resulting,
                    expected_commit_sequence=1,
                )
            return VerifiedOwnerPublication.from_command(
                guarded,
                binding_fingerprint="fixture-binding",
                selected_identity=None,
                selected_fingerprint=None,
                expected_resulting=None,
                expected_commit_sequence=None,
            )

    resolver = Resolver()

    def decide(resulting: str) -> None:
        resolver.resulting = resulting

    guarded = replace(command, admission_guard=lambda: None, decision_guard=decide)
    generic_pairs = tuple(
        dict.fromkeys(
            (
                *CommonCliExecutionRuntime._record_schema_variants,
                *((record.owner, record.schema_id) for record in batch.complete_records),
            )
        )
    )
    store = SQLiteMaterializer(
        tmp_path / "completion.sqlite",
        record_contracts=CommonCliExecutionRuntime._record_contracts,
        record_schema_variants=generic_pairs,
    )
    store._mount_owner_publication_resolver(resolver)
    appender = EventAppender(store, capacity=2)
    await appender.advance_fence(
        FenceAdvanceCommand(batch.identity.tenant_id, guarded.fence_generation, 0)
    )
    unknown = replace(
        guarded,
        records=(replace(guarded.records[0], schema_id="chiplog.unknown.v1"), *guarded.records[1:]),
    )
    with pytest.raises(ValueError, match="owner/schema"):
        await appender.submit(unknown)
    assert store.durable_records() == ()

    assert (await appender.submit(guarded)).disposition == "COMMITTED"
    assert resolver.modes == ["ABSENT", "PRESELECT", "PRECOMMIT"]
    rows = store.durable_records()
    assert rows == tuple(
        sorted(
            (
                batch.identity.tenant_id,
                record.record_id,
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                1,
            )
            for record in guarded.records
        )
    )
    await appender.close()


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

    database = tmp_path / "h1-owner-inventory-post.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database, resources=resources, responses=(complete,)
    ) as runtime:
        initial = await runtime.drive_input(request)
        assert initial.kind == "SELECTED_EXECUTION_RECEIPT_V1"
        committed = await runtime.advance_execution(advance(cast(Any, initial), request))
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
    database = tmp_path / "h1-owner-inventory.sqlite"
    custody = tmp_path / "dispatch-custody"
    request, complete = await admit_complete_script(database, custody)
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
