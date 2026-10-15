"""Recover selected OPEN-source publications around commit and anchor advancement."""

from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
)
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
from chiplog.platform.workspace_snapshot import read_connection
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["accounting", "recovery", "suspension"])
@pytest.mark.parametrize("fault", ["before_commit", "after_commit", "after_anchor"])
async def test_selected_open_pending_batch_recovers_after_reopen(
    tmp_path, monkeypatch, family, fault
):
    database = tmp_path / "pending-open.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=tmp_path / "custody"
    )
    options = dict(
        resources=resources,
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    )
    operation = (
        "agent_loop.execution-suspension.v1" if family == "suspension"
        else "agent_loop.accepted-open-" + family + ".v1"
    )
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
            item for item in inventory.ordered_calls
            if item.initialized_record.call.classification == "CONSEQUENTIAL"
        )
        assert len(calls) == 1
        call = calls[0]
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=call.original_call_id, initialized=call.initialized,
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
        if family == "suspension":
            saved_recovery = await runtime.publish_accepted_open_recovery(
                original_call_id=call.original_call_id
            )

            async def publish(*, original_call_id):
                assert original_call_id == call.original_call_id
                return await runtime.suspend_execution(sealed.run_id, sealed.head, ())
        else:
            publish = getattr(runtime, "publish_accepted_open_" + family)
        submit = runtime._appender.submit
        append = runtime._append_decision
        injected = []

        async def interrupt(command):
            if command.operation_kind == operation:
                injected.append(command)
                if fault != "after_anchor":
                    command = replace(command, fault=fault)
            return await submit(command)

        def interrupt_marker(entry):
            if (
                fault == "after_anchor" and injected
                and entry.get("kind") == "MATERIALIZED"
                and entry.get("operation_id") == injected[0].idempotency_key
            ):
                raise RuntimeError("injected fault before materialized marker")
            return append(entry)

        expected_error = LostCommitAcknowledgement if fault == "after_commit" else RuntimeError
        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", interrupt)
            patch.setattr(runtime, "_append_decision", interrupt_marker)
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
        if family == "suspension":
            from chiplog.composition.r14_execution_suspension import _retained

            expected_suspension = _retained(pending[0]).result
            assert expected_suspension.run in history[0].records
            with read_connection(database) as connection:
                batch_ids = tuple(row[0] for row in connection.execute(
                    "SELECT record_id FROM records WHERE tenant_id=? AND commit_sequence=? "
                    "ORDER BY rowid", (runtime._tenant_id, command.expected_head + 1),
                ))
            assert batch_ids == (
                () if fault == "before_commit" else tuple(row.record_id for row in command.records)
            )

    async with open_common_cli_execution_runtime(database, **options) as reopened:
        assert not reopened._pending()
        if family == "suspension":
            selected = reopened.read_selected_execution_suspension()
            assert selected is not None
            assert selected.baseline.original_obligations == (saved_recovery.opening.original,)
            assert selected.baseline == expected_suspension.baseline
            assert selected.pair == expected_suspension.pair
        else:
            read = getattr(reopened, "read_selected_accepted_open_" + family)
            selected = read(original_call_id=call.original_call_id)
            assert selected is not None
            accounting = selected if family == "accounting" else selected.accounting
            assert accounting.kind == "RECOVERY_REQUIRED"
        state, actual, anchor = reopened._selected_physical_state(command)
        assert state == "COMPLETE" and actual == anchor
        assert len(resources.require_original_provider().transfers) == 1
        before = read_execution_recovery_history(reopened)
        if family == "suspension":
            replay = await reopened.suspend_execution(sealed.run_id, sealed.head, ())
        else:
            replay = await getattr(reopened, "publish_accepted_open_" + family)(
                original_call_id=call.original_call_id
            )
        assert replay == selected
        assert read_execution_recovery_history(reopened) == before
