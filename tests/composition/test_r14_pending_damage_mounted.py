"""Reject corrupted predecessor and orphan rows around pending suspension recovery."""

import sqlite3
from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import (
    read_execution_call_history,
    read_execution_recovery_history,
)
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import LostCommitAcknowledgement
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["predecessor-absent", "orphan-complete"])
async def test_pending_suspension_damage_rejects_outer_read_and_startup(
    tmp_path, monkeypatch, damage
):
    fault = "before_commit" if damage == "predecessor-absent" else "after_commit"
    database = tmp_path / "pending-open.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=tmp_path / "custody"
    )
    options = dict(
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    )
    operation = "agent_loop.execution-suspension.v1"
    async with open_common_cli_execution_runtime(
        database, responses=(native_response(),), **options
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "pending-open")
        receipt = await runtime.drive_input(wire)
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        _, inventory, _ = read_execution_call_history(runtime)
        calls = tuple(
            item
            for item in inventory.ordered_calls
            if item.initialized_record.call.classification == "CONSEQUENTIAL"
        )
        assert len(calls) == 1
        call = calls[0]
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=call.original_call_id,
                initialized=call.initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        accepted = await runtime.accept_call(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="accept-pending-open", preview_bytes=preview.canonical_bytes()
            ),
        )
        intent = accepted.external_intent.subject_id
        await runtime.authorize_dispatch("hermetic-ingress", intent, "authorize", sealed.run_id)
        await runtime.commit_first_send("hermetic-ingress", intent, "send", sealed.run_id)
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent)
        await runtime.publish_accepted_open_recovery(original_call_id=call.original_call_id)

        async def publish(*, original_call_id):
            assert original_call_id == call.original_call_id
            return await runtime.suspend_execution(sealed.run_id, sealed.head, ())

        submit = runtime._appender.submit
        injected = []

        async def interrupt(command):
            if command.operation_kind == operation:
                injected.append(command)
                command = replace(command, fault=fault)
            return await submit(command)

        expected_error = LostCommitAcknowledgement if fault == "after_commit" else RuntimeError
        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", interrupt)
            with pytest.raises(expected_error, match="injected"):
                await publish(original_call_id=call.original_call_id)
        assert len(injected) == 1
        command = injected[0]
        pending = runtime._pending()
        assert len(pending) == 1 and pending[0]["operation_kind"] == operation
        state, actual, anchor = runtime._selected_physical_state(command)
        assert state == ("ABSENT" if fault == "before_commit" else "COMPLETE")
        assert (actual == anchor) == (fault != "after_commit")
        # This is the real outer semantic/physical fold, before replay repairs the cut.
        history = read_execution_recovery_history(
            runtime, selected_only=True, exact_historical_sources=True
        )
        from chiplog.composition.r14_execution_suspension import _retained

        expected_suspension = _retained(pending[0]).result
        assert expected_suspension.run in history[0].records
        with read_connection(database) as connection:
            batch_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT record_id FROM records WHERE tenant_id=? AND commit_sequence=? "
                    "ORDER BY rowid",
                    (runtime._tenant_id, command.expected_head + 1),
                )
            )
        assert batch_ids == (
            () if fault == "before_commit" else tuple(row.record_id for row in command.records)
        )

        # Both cases first establish the real uncorrupted pending outer read above.
        with sqlite3.connect(database) as connection:
            predecessor = connection.execute(
                "SELECT owner,schema_id,canonical_bytes FROM records "
                "WHERE tenant_id=? AND record_id=?",
                (runtime._tenant_id, sealed.head),
            ).fetchone()
            assert predecessor is not None
            if damage == "predecessor-absent":
                changed = connection.execute(
                    "UPDATE records SET canonical_bytes=? WHERE tenant_id=? AND record_id=?",
                    (b"not-json", runtime._tenant_id, sealed.head),
                )
                assert changed.rowcount == 1
            else:
                connection.execute(
                    "INSERT INTO records(tenant_id,record_id,owner,schema_id,canonical_bytes,"
                    "commit_sequence) VALUES (?,?,?,?,?,0)",
                    (runtime._tenant_id, "orphan-pending-probe", *predecessor),
                )
            head = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
        actual = capture_authority_storage_state(database)[0]
        runtime._commitment_journal.commit(runtime._tenant_id, actual)
        assert runtime._commitment_journal.load(runtime._tenant_id) == actual
        state, actual_after, anchor_after = runtime._selected_physical_state(command)
        assert state == ("ABSENT" if damage == "predecessor-absent" else "COMPLETE")
        assert actual_after == anchor_after == actual
        before_journal = runtime._loop_decisions().entries()
        before_pending = runtime._pending()
        assert len(before_pending) == 1
        before_transfers = len(resources.require_original_provider().transfers)
        assert before_transfers == 1
        with pytest.raises((LoopIntegrityError, LoopRejected, OwnerJournalIntegrityError)):
            read_execution_recovery_history(
                runtime, selected_only=True, exact_historical_sources=True
            )
        assert runtime._loop_decisions().entries() == before_journal
        assert runtime._pending() == before_pending

    with pytest.raises((LoopIntegrityError, LoopRejected, OwnerJournalIntegrityError)):
        async with open_common_cli_execution_runtime(database, **options):
            raise AssertionError("damaged pending startup entered runtime")
    assert runtime._loop_decisions().entries() == before_journal
    assert runtime._pending() == before_pending
    assert len(resources.require_original_provider().transfers) == before_transfers
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            == head
        )
