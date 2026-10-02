"""Mounted authenticated R16 source capture and explicit authority gaps."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime, open_dispatch_loop
from chiplog.composition.r16_native_inventory import (
    R16InventoryIntegrityError,
    collect_r16_native_inventory,
)
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PublicationResult
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.owner_publications import OwnerPublicationUncertain
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.dispatch import _response
from tests.support.h1_cli_execution import admit_complete_script


@pytest.mark.asyncio
async def test_mounted_capture_retains_whole_tenant_source_without_zero_call_claim(
    tmp_path: Path,
) -> None:
    database = tmp_path / "inventory.sqlite"
    custody = tmp_path / "custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(complete,),
    ) as runtime:
        await runtime.drive_input(request)
        result = collect_r16_native_inventory(runtime)
        assert result.status == "HOLD"
        assert any(gap.code == "R17_CALL_RELEVANCE_UNAVAILABLE" for gap in result.gaps)
        with closing(sqlite3.connect(database)) as connection:
            expected_records = tuple(
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                    "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                    (runtime._tenant_id,),
                )
            )
            expected_evidence = tuple(
                connection.execute(
                    "SELECT source_id,evidence_id,fingerprint,canonical_bytes,followup_kind,"
                    "state,attempt_id,transport_version,cursor FROM evidence_inbox "
                    "WHERE tenant_id=? ORDER BY source_id,evidence_id",
                    (runtime._tenant_id,),
                )
            )
        assert (
            tuple(
                (r.record_id, r.owner, r.schema, r.raw, r.commit_sequence) for r in result.records
            )
            == expected_records
        )
        assert result.evidence_rows == expected_evidence
        assert result.records
        assert {item.locator for item in result.occurrences if item.locator.startswith("records/")}
        assert result.owner_entries == tuple(runtime._owner_decisions()._raw.entries())
        # Unsupported native run/turn rows are preserved as gaps rather than
        # passed through H1 zero-call classification or discarded as irrelevant.
        assert any(gap.code == "UNSUPPORTED" for gap in result.gaps)


@pytest.mark.asyncio
async def test_mounted_unanchored_irrelevant_corruption_cannot_return_partial_capture(
    tmp_path: Path,
) -> None:
    database = tmp_path / "corrupt.sqlite"
    custody = tmp_path / "custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(complete,),
    ) as runtime:
        await runtime.drive_input(request)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE tenant_id=? AND record_id="
                "(SELECT record_id FROM records WHERE tenant_id=? LIMIT 1)",
                (b"{}", runtime._tenant_id, runtime._tenant_id),
            )
        with pytest.raises(R16InventoryIntegrityError) as raised:
            collect_r16_native_inventory(runtime)
        assert raised.value.status == "HOLD"
        assert raised.value.locator == "physical-anchor"
        assert raised.value.__cause__ is not None


@pytest.mark.asyncio
async def test_mounted_pending_loop_is_retained_as_hold(tmp_path: Path) -> None:
    database = tmp_path / "pending.sqlite"
    custody = tmp_path / "custody"
    request, complete = await admit_complete_script(database, custody)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=custody)
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(complete,),
    ) as runtime:
        await runtime.drive_input(request)
        runtime._append_decision(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": "pending",
                "operation_kind": "agent_loop",
                "expected_head": 0,
                "fingerprint": "request",
                "predecessor": runtime._commitment_journal.load(runtime._tenant_id),
                "resulting": "resulting",
                "records": [],
            }
        )
        result = collect_r16_native_inventory(runtime)
        assert any(gap.code == "PENDING" for gap in result.gaps)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema",
    (
        "chiplog.effects.dispatch-record.v2",
        "future.effects.record.v99",
    ),
)
async def test_authenticated_irrelevant_occurrence_is_decoded_before_relevance(
    tmp_path: Path,
    schema: str,
) -> None:
    database = tmp_path / "bad-producer.sqlite"
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_dispatch_loop(database, resources=resources, responses=()) as loop:
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        # Simulate an authenticated but unsupported/broken non-owner producer.
        # Re-anchor intentionally so this negative input reaches the collector's
        # registered decoder, rather than the earlier tampering check.
        with runtime._authority_gate().hold():
            with closing(sqlite3.connect(database)) as connection, connection:
                row = connection.execute(
                    "SELECT head FROM tenant_heads WHERE tenant_id=?",
                    (runtime._tenant_id,),
                ).fetchone()
                sequence = (0 if row is None else row[0]) + 1
                connection.execute(
                    "INSERT INTO publications VALUES(?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        "test.bad-producer",
                        "unrelated",
                        "a" * 64,
                        sequence,
                        "unrelated-record",
                    ),
                )
                connection.execute(
                    "INSERT INTO records VALUES(?,?,?,?,?,?)",
                    (runtime._tenant_id, "unrelated-record", "effects", schema, b"{}", sequence),
                )
                connection.execute(
                    "INSERT INTO tenant_heads VALUES(?,?) "
                    "ON CONFLICT(tenant_id) DO UPDATE SET head=excluded.head",
                    (runtime._tenant_id, sequence),
                )
            with read_connection(database) as connection:
                commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            runtime._commitment_journal.commit(runtime._tenant_id, commitment)
        if schema == "chiplog.effects.dispatch-record.v2":
            with pytest.raises(R16InventoryIntegrityError) as raised:
                collect_r16_native_inventory(runtime)
            assert raised.value.locator == "records/unrelated-record"
            assert raised.value.__cause__ is not None
        else:
            result = collect_r16_native_inventory(runtime)
            assert any(item.schema == schema for item in result.occurrences)
            assert any(
                gap.code == "UNSUPPORTED" and gap.locator == "records/unrelated-record"
                for gap in result.gaps
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_owner", (False, True))
async def test_mounted_r16_flow_retains_selected_and_physical_dispatch_outcome_consumption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_owner: bool,
) -> None:
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_dispatch_loop(
        tmp_path / "dispatch.sqlite",
        resources=resources,
        responses=(_response(),),
    ) as loop:
        created = await loop.create("run", "Prepare action", BudgetPolicy())
        active = await loop.activate("run", created.head)
        await loop.step("run", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        display = await runtime.preview_dispatch("run/turn/1/proposal/effect")
        adopted = await runtime.adopt_dispatch(
            "hermetic-ingress",
            display.display_id,
            display.display_digest,
            display.adoption_act_id,
            "run",
        )
        assert isinstance(adopted, JournalSelectedPublication)
        record = DispatchRecordV2.model_validate_json(adopted.complete_records[-1].canonical_bytes)
        intent_id = record.snapshot.intent.intent_id
        if pending_owner:
            original_submit = EventAppender.submit

            async def fail_after_selection(
                appender: EventAppender, command: PhysicalPublicationCommand
            ) -> PublicationResult:
                original_guard = command.decision_guard
                assert original_guard is not None

                def guard(commitment: str) -> None:
                    original_guard(commitment)
                    raise RuntimeError("test crash after owner selection")

                return await original_submit(appender, replace(command, decision_guard=guard))

            with monkeypatch.context() as patch:
                patch.setattr(EventAppender, "submit", fail_after_selection)
                with pytest.raises(OwnerPublicationUncertain):
                    await runtime.authorize_dispatch("hermetic-ingress", intent_id, "auth", "run")
            result = collect_r16_native_inventory(runtime)
            pending = [
                decision for decision in result.owner_cut.decisions if not decision.materialized
            ]
            assert len(pending) == 1
            assert any(
                gap.locator == f"selected/{pending[0].command_id}" and gap.code == "PENDING"
                for gap in result.gaps
            )
            prefix = f"selected/{pending[0].decision_id}/"
            pending_members = [
                item for item in result.occurrences if item.locator.startswith(prefix + "member/")
            ]
            assert pending_members
            assert not {item.record_id for item in pending_members} & {
                row.record_id for row in result.records
            }
            assert {item.locator for item in pending_members} <= {
                item.locator for item in result.decoded
            }
            return
        authorized = await runtime.authorize_dispatch("hermetic-ingress", intent_id, "auth", "run")
        assert isinstance(authorized, JournalSelectedPublication)
        sent = await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "run")
        assert isinstance(sent, JournalSelectedPublication)
        receipt = await runtime.emit_committed("hermetic-ingress", intent_id)
        assert receipt is not None
        observed = await runtime.observe_dispatch_outcome(intent_id, receipt)
        assert isinstance(observed, JournalSelectedPublication)
        outcome = await runtime.resolve_dispatch_outcome("hermetic-ingress", intent_id, "resolve")
        assert isinstance(outcome, JournalSelectedPublication)
        result = collect_r16_native_inventory(runtime)
        assert result.status == "HOLD"
        expected = {
            "chiplog.effects.dispatch-record.v2",
            "chiplog.effects.dispatch-outcome-record.v2",
            "chiplog.broker-dispatch.send-consumption.v1",
        }
        for schema in expected:
            members = [item for item in result.occurrences if item.schema == schema]
            selected = [item for item in members if item.locator.startswith("selected/")]
            physical = [item for item in members if item.locator.startswith("records/")]
            assert selected and physical
            assert {item.raw for item in selected} == {item.raw for item in physical}
            assert {item.locator for item in members} <= {item.locator for item in result.decoded}
        assert {decision.command_id for decision in result.owner_cut.decisions} == {
            decision.prepared.request.identity.command_id
            for decision in runtime._owner_decisions().snapshot().decisions
        }
