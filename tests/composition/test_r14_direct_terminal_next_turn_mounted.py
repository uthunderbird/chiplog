"""Mounted direct R14 terminal becomes one replayable next Turn after reopen."""

import faulthandler
import json
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_ACCEPTED_DIRECT_TERMINAL_V30_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected, ToolCall
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionContinue
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import SuccessResult
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.composition import r14_loop_history
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history, read_execution_history
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r14_next_turn_records import NEXT_TURN_PUBLICATION
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


def _mark(name: str) -> None:
    print(f"R14_PHASE {time.perf_counter():.3f} {name}", file=sys.stderr, flush=True)


@pytest.mark.asyncio
async def test_selected_direct_terminal_reopens_to_one_next_turn(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "direct-terminal-next-turn.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    source = NativeRecoveryCutSource()
    manifest = R14_EXECUTION_ACCEPTED_DIRECT_TERMINAL_V30_PRODUCTION_MANIFEST
    turn_two_response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ToolCall(call_id="turn-two-proposal", tool="propose_intent", text="follow-up"),
        ),
    ).canonical_bytes()

    _mark("start open-initial-runtime")
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(), turn_two_response),
        manifest=manifest,
        recovery_cut_source=source,
    ) as runtime:
        _mark("done open-initial-runtime")
        _mark("start publish-recovery-profile")
        await runtime.publish_native_recovery_profile(version="v3")
        _mark("done publish-recovery-profile")
        _mark("start admit-slot")
        wire = await admit_runtime_slot(runtime, "r14-direct-next-turn")
        _mark("done admit-slot")
        _mark("start drive-input")
        receipt = await runtime.drive_input(wire)
        _mark("done drive-input")
        _mark("start begin-execution")
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        _mark("done begin-execution")
        _mark("start capture-execution")
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        _mark("done capture-execution")
        _mark("start seal-execution")
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        _mark("done seal-execution")
        _mark("start read-initial-call-history")
        _, inventory, _ = read_execution_call_history(runtime)
        _mark("done read-initial-call-history")
        calls = tuple(
            item
            for item in inventory.ordered_calls
            if item.initialized_record.call.classification == "CONSEQUENTIAL"
        )
        assert len(calls) == 1
        _mark("start preview-call-acceptance")
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=calls[0].original_call_id,
                initialized=calls[0].initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        _mark("done preview-call-acceptance")
        _mark("start accept-call")
        accepted = await runtime.accept_call(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="accept-r14-direct-next-turn", preview_bytes=preview.canonical_bytes()
            ),
        )
        _mark("done accept-call")
        intent_id = accepted.external_intent.subject_id
        _mark("start authorize-dispatch")
        assert isinstance(
            await runtime.authorize_dispatch(
                "hermetic-ingress", intent_id, "authorize-r14-direct-next-turn", sealed.run_id
            ),
            JournalSelectedPublication,
        )
        _mark("done authorize-dispatch")
        _mark("start commit-first-send")
        assert isinstance(
            await runtime.commit_first_send(
                "hermetic-ingress", intent_id, "send-r14-direct-next-turn", sealed.run_id
            ),
            JournalSelectedPublication,
        )
        _mark("done commit-first-send")
        _mark("start emit-committed")
        await runtime.emit_committed("hermetic-ingress", intent_id)
        _mark("done emit-committed")
        _mark("start resolve-dispatch-outcome")
        closed_publication = await runtime.resolve_dispatch_outcome(
            "hermetic-ingress", intent_id, "close-r14-direct-next-turn"
        )
        _mark("done resolve-dispatch-outcome")
        closed = DispatchOutcomeRecordV2.model_validate_json(
            closed_publication.complete_records[0].canonical_bytes
        )
        assert closed.snapshot.state == "CONFIRMED"
        assert closed.snapshot.obligation.state == "CLOSED"
        transfers_before_next_turn = len(resources.require_original_provider().transfers)
        assert transfers_before_next_turn == 1

        _mark("start publish-direct-terminal")
        direct_publication = await runtime.publish_accepted_direct_terminal()
        _mark("done publish-direct-terminal")
        _mark("start read-selected-direct-terminal")
        selected_terminal = runtime.read_selected_accepted_direct_terminal_frontier()
        _mark("done read-selected-direct-terminal")
        assert selected_terminal is not None
        assert isinstance(selected_terminal.calls[0].disposition, SuccessResult)
        _mark("start publish-accounting-readiness")
        from chiplog.composition import r14_recovery_publication

        original_collect = source.collect_held
        collect_count = 0

        def timed_collect(held_runtime):
            nonlocal collect_count
            collect_count += 1
            name = f"accounting-cut-{collect_count}"
            _mark(f"start {name}")
            try:
                return original_collect(held_runtime)
            finally:
                _mark(f"done {name}")

        original_actor = runtime._execution_actor

        async def timed_actor(peer):
            _mark("start accounting-actor-read")
            try:
                return await original_actor(peer)
            finally:
                _mark("done accounting-actor-read")

        original_exchange = r14_recovery_publication._exchange

        async def timed_exchange(held_runtime, request, operation, result_type):
            name = "accounting-owner-" + operation.rsplit(".", 1)[-1]
            _mark(f"start {name}")
            try:
                return await original_exchange(held_runtime, request, operation, result_type)
            finally:
                _mark(f"done {name}")

        original_issue_actor = r14_recovery_publication.issue_execution_recovery_actor_v1

        async def timed_issue_actor(held_runtime, peer):
            _mark("start accounting-actor-issue")
            try:
                return await original_issue_actor(held_runtime, peer)
            finally:
                _mark("done accounting-actor-issue")

        original_recover_exact = runtime._recover_exact

        async def timed_recover_exact(*args, **kwargs):
            _mark("start accounting-finish-recover-exact")
            try:
                return await original_recover_exact(*args, **kwargs)
            finally:
                _mark("done accounting-finish-recover-exact")

        original_finish_decision = runtime._finish_decision

        def timed_finish_decision(*args, **kwargs):
            _mark("start accounting-finish-decision")
            try:
                return original_finish_decision(*args, **kwargs)
            finally:
                _mark("done accounting-finish-decision")

        original_read_call_history = r14_loop_history._read_call_history
        writer_submitted = [False]
        stack_probe_used = [False]

        def timed_read_call_history(*args, **kwargs):
            timer = None
            if writer_submitted[0] and not stack_probe_used[0]:
                stack_probe_used[0] = True
                timer = threading.Timer(
                    3.0,
                    faulthandler.dump_traceback,
                    kwargs={"file": sys.stderr, "all_threads": True},
                )
                timer.daemon = True
                timer.start()
            _mark("start accounting-finish-read-call-history")
            try:
                return original_read_call_history(*args, **kwargs)
            finally:
                if timer is not None:
                    timer.cancel()
                    timer.join(timeout=2.0)
                _mark("done accounting-finish-read-call-history")

        from chiplog.platform import authority_reads

        original_snapshot_bytes = authority_reads.capture_authority_snapshot_bytes

        def timed_snapshot_bytes(*args, **kwargs):
            _mark("start accounting-writer-authority-snapshot")
            try:
                return original_snapshot_bytes(*args, **kwargs)
            finally:
                _mark("done accounting-writer-authority-snapshot")

        original_submit = runtime._appender.submit

        def timed_guard(name, guard):
            if guard is None:
                return None

            def wrapped_guard(*args, **kwargs):
                _mark(f"start {name}")
                try:
                    return guard(*args, **kwargs)
                finally:
                    _mark(f"done {name}")

            return wrapped_guard

        async def timed_submit(batch):
            timed_batch = replace(
                batch,
                admission_guard=timed_guard(
                    "accounting-writer-admission-guard", batch.admission_guard
                ),
                decision_guard=timed_guard(
                    "accounting-writer-decision-guard", batch.decision_guard
                ),
            )
            _mark("start accounting-writer-submit")
            try:
                result = await original_submit(timed_batch)
                writer_submitted[0] = True
                return result
            finally:
                _mark("done accounting-writer-submit")

        monkeypatch.setattr(source, "collect_held", timed_collect)
        monkeypatch.setattr(runtime, "_execution_actor", timed_actor)
        monkeypatch.setattr(runtime._appender, "submit", timed_submit)
        monkeypatch.setattr(r14_recovery_publication, "_exchange", timed_exchange)
        monkeypatch.setattr(
            authority_reads, "capture_authority_snapshot_bytes", timed_snapshot_bytes
        )
        monkeypatch.setattr(
            r14_recovery_publication,
            "issue_execution_recovery_actor_v1",
            timed_issue_actor,
        )
        monkeypatch.setattr(runtime, "_recover_exact", timed_recover_exact)
        monkeypatch.setattr(runtime, "_finish_decision", timed_finish_decision)
        monkeypatch.setattr(r14_loop_history, "_read_call_history", timed_read_call_history)
        try:
            await runtime.publish_execution_accounting_readiness()
        finally:
            monkeypatch.undo()
        _mark("done publish-accounting-readiness")
        _mark("start collect-recovery-cut")
        cut = source.collect(runtime)
        _mark("done collect-recovery-cut")
        assert cut is not None
        assert cut.original_closures == ()
        assert cut.current_reductions == ()
        assert any(
            row.physical_record.subject_id == closed.record.head for row in cut.complete_sources
        )
        terminal_source = next(
            row
            for row in cut.complete_sources
            if row.schema_id == "chiplog.execution.accepted-direct-terminal-disposition.v1"
            and row.subject.subject_id == direct_publication.terminal.terminal_id
        )
        result_source = next(
            row
            for row in cut.complete_sources
            if row.schema_id == "chiplog.execution.accepted-direct-terminal-result.v1"
            and row.subject.subject_id == direct_publication.result.result_id
        )
        assert selected_terminal.calls[0].disposition.terminal == terminal_source.subject.revision
        assert selected_terminal.calls[0].disposition.result == result_source.subject.revision

    _mark("start open-reopened-runtime-1")
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(turn_two_response,),
        manifest=manifest,
        recovery_cut_source=source,
    ) as reopened:
        _mark("done open-reopened-runtime-1")
        _mark("start start-next-turn")
        next_run = await reopened.start_next_execution_turn(sealed.run_id, sealed.head)
        _mark("done start-next-turn")
        assert next_run.event == "TurnStarted"
        assert tuple(turn.ordinal for turn in next_run.turns) == (1, 2)
        assert next_run.turns[-1].state == "PREPARING"
        _mark("start replay-next-turn")
        assert await reopened.start_next_execution_turn(sealed.run_id, sealed.head) == next_run
        _mark("done replay-next-turn")
        assert (
            sum(
                json.loads(raw).get("operation_kind") == NEXT_TURN_PUBLICATION
                for _, _, raw in reopened._loop_decisions().entries()
            )
            == 1
        )
        with pytest.raises(LoopRejected, match="changed replay request"):
            await reopened.start_next_execution_turn(sealed.run_id, next_run.head)
        assert len(resources.require_original_provider().transfers) == transfers_before_next_turn
        from chiplog.composition import r14_execution_runtime

        rendered_contexts: list[str] = []
        original_render = r14_execution_runtime.render_execution_prompt

        def observe_render(content: str):
            rendered_contexts.append(content)
            return original_render(content)

        monkeypatch.setattr(r14_execution_runtime, "render_execution_prompt", observe_render)
        _mark("start capture-same-run-turn-two")
        captured_turn_two = await reopened.capture_execution(
            "hermetic-ingress", next_run.run_id, next_run.head
        )
        _mark("done capture-same-run-turn-two")
        assert len(rendered_contexts) == 1
        model_context = json.loads(rendered_contexts[0])
        prior_turn = sealed.turns[0]
        assert model_context["prior_turns"] == [
            {
                "turn_id": prior_turn.turn_id,
                "response_base64": prior_turn.attempts[prior_turn.selector].response_base64,
                "rejection": prior_turn.attempts[prior_turn.selector].rejection,
                "visibility": prior_turn.attempts[prior_turn.selector].manifest.digest(),
                "results": [
                    {
                        "original_call_id": direct_publication.result.original_call_id,
                        "terminal": direct_publication.terminal.model_dump(mode="json"),
                        "result": direct_publication.result.model_dump(mode="json"),
                    }
                ],
            }
        ]
        _mark("start seal-same-run-turn-two")
        sealed_turn_two = await reopened.seal_execution(
            "hermetic-ingress", captured_turn_two.run_id, captured_turn_two.head
        )
        _mark("done seal-same-run-turn-two")
        assert sealed_turn_two.turns[-1].ordinal == 2
        assert sealed_turn_two.turns[-1].state == "ACCEPTED"
        assert sealed_turn_two.turns[-1].response_seal is not None
        assert read_execution_history(reopened).records[-1] == sealed_turn_two
        _mark("start read-final-history")
        assert sealed_turn_two.turns[-1].response_seal is not None
        _mark("done read-final-history")
