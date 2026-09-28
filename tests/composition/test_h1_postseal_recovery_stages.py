"""RED installed contracts for durable four-stage H1 post-seal recovery.

The recovery journal already has a strict four-stage record format.  These
tests deliberately exercise only the installed driver seam: the desired
coordinator must derive the records and open real B sessions itself.  In
particular, the tests never manufacture a ROOT, stage input, or stage result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.effects.h1_local_preparation_contracts import H1LocalCommentaryOwnerCallV1
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordV1,
)
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot

_STAGES = ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
_OPERATIONS = {
    "COMPLETION": "agent_loop.prepare_first_path_completion",
    "CONVERSATION": "projections.prepare_conversation_completion",
    "EFFECTS": "effects.prepare_h1_local_commentary",
    "TERMINAL_WORK": "agent_loop.prepare_terminal_work",
}
_RED_REASON = (
    "the installed post-seal coordinator currently persists only ROOT; it has no "
    "stage reconstruction, durable stage executor, or restart replay path"
)


class _CrashAfterOwnerReply(RuntimeError):
    """Simulated process loss after an inert owner reply and before result CAS."""


class _CrashAfterResultReadback(RuntimeError):
    """Simulated process loss after a stage result's durable append/readback."""


class _RecordingEngine:
    """Delegate to the installed broker while preserving actual outgoing frames."""

    def __init__(
        self,
        installed: Any,
        events: list[tuple[str, object]],
        *,
        crash_after_operation: str | None = None,
    ) -> None:
        self._installed = installed
        self._events = events
        self._crash_after_operation = crash_after_operation
        self.calls: list[PublicPortCall] = []
        self.results: dict[str, object] = {}

    def session(self, owner: str) -> Any:
        return self._installed.session(owner)

    async def call(self, sent: PublicPortCall) -> Any:
        self.calls.append(sent)
        self._events.append(("call", sent))
        returned = await self._installed.call(sent)
        self.results[sent.request_id] = returned
        if sent.operation_id == self._crash_after_operation:
            raise _CrashAfterOwnerReply()
        return returned

    async def _call_with_admission_guard(
        self, sent: PublicPortCall, *, admission_guard: Any, authority_gate: Any = None
    ) -> Any:
        self.calls.append(sent)
        self._events.append(("call", sent))
        returned = await self._installed._call_with_admission_guard(
            sent, admission_guard=admission_guard, authority_gate=authority_gate
        )
        self.results[sent.request_id] = returned
        if sent.operation_id == self._crash_after_operation:
            raise _CrashAfterOwnerReply()
        return returned


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _seal_v3_then_v2_without_recovery(
    runtime: CommonCliExecutionRuntime,
) -> tuple[Any, Any]:
    """Commit the genuine selected V3 Prepare and V2 seal, leaving ROOT absent."""
    private = cast(Any, runtime)
    request = await _admit(runtime)
    initial = cast(Any, await runtime.drive_input(request))
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="four-stage recovery"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    selected_prepare = select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    )
    assert selected_prepare.decision_id
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    assert sealed.state == "ACTIVE"
    assert private._h1_postseal_recovery_journal.scan().states_by_root == ()
    return request, initial


def _install_recorders(
    runtime: CommonCliExecutionRuntime,
    events: list[tuple[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    *,
    crash_after_operation: str | None = None,
) -> _RecordingEngine:
    private = cast(Any, runtime)
    engine = _RecordingEngine(
        private._supervisor.runtime(), events, crash_after_operation=crash_after_operation
    )
    monkeypatch.setattr(private._supervisor, "runtime", lambda: engine)
    original_append = H1PostSealRecoveryJournal.append_transition
    original_scan = H1PostSealRecoveryJournal.scan

    def scan_and_record(journal: H1PostSealRecoveryJournal) -> Any:
        scan = original_scan(journal)
        events.append(("scan", scan))
        return scan

    def append_and_record(
        journal: H1PostSealRecoveryJournal,
        record: H1PostSealRecoveryRecordV1,
        *,
        expected_global_tip: str | None,
    ) -> Any:
        receipt = original_append(journal, record, expected_global_tip=expected_global_tip)
        # The receipt is returned only after the enrolled journal has re-scanned
        # the append.  Recording here makes this a readback, not a write intent.
        events.append(("readback", record))
        return receipt

    monkeypatch.setattr(H1PostSealRecoveryJournal, "append_transition", append_and_record)
    monkeypatch.setattr(H1PostSealRecoveryJournal, "scan", scan_and_record)
    return engine


def _stage_calls(
    engine: _RecordingEngine, expected_stages: tuple[str, ...] = _STAGES
) -> dict[str, PublicPortCall]:
    calls = [call for call in engine.calls if call.operation_id in _OPERATIONS.values()]
    assert [call.operation_id for call in calls] == [
        _OPERATIONS[stage] for stage in expected_stages
    ]
    return dict(zip(expected_stages, calls, strict=True))


def _semantic_input(stage: str, call: PublicPortCall) -> bytes:
    if stage != "EFFECTS":
        return cast(bytes, call.canonical_payload)
    owner_call = H1LocalCommentaryOwnerCallV1.model_validate_json(call.canonical_payload)
    assert owner_call.canonical_bytes() == call.canonical_payload
    return cast(bytes, owner_call.request.canonical_bytes())


def _assert_durable_stage_evidence(
    runtime: CommonCliExecutionRuntime,
    engine: _RecordingEngine,
    calls: dict[str, PublicPortCall],
) -> None:
    states = cast(Any, runtime)._h1_postseal_recovery_journal.scan().states_by_root
    assert len(states) == 1
    state = states[0][1]
    assert tuple(stage for stage, _bytes, _command_id in state.inputs) == _STAGES
    assert tuple(stage for stage, _bytes in state.results) == _STAGES
    for stage, call in calls.items():
        assert state.stage_input(stage)[0] == _semantic_input(stage, call)
        returned = engine.results[call.request_id]
        assert isinstance(returned, PublicPortSuccess)
        assert dict(state.results)[stage] == returned.canonical_payload


def _scan_has_stage_input(scan: object, stage: str) -> bool:
    return any(
        stage in {found for found, _bytes, _command_id in state.inputs}
        for _root_id, state in cast(Any, scan).states_by_root
    )


def _assert_readback_precedes_each_ipc(
    events: list[tuple[str, object]],
    calls: dict[str, PublicPortCall],
    *,
    existing_inputs: tuple[str, ...] = (),
    existing_results: tuple[str, ...] = (),
) -> None:
    for stage, call in calls.items():
        if stage in existing_inputs:
            input_index = next(
                index
                for index, (kind, value) in enumerate(events)
                if kind == "scan" and _scan_has_stage_input(value, stage)
            )
        else:
            input_index = next(
                index
                for index, (kind, value) in enumerate(events)
                if kind == "readback"
                and isinstance(value, H1PostSealRecoveryRecordV1)
                and value.kind == "STAGE_INPUT"
                and value.stage == stage
            )
        call_index = events.index(("call", call))
        assert input_index < call_index
        if stage not in existing_results:
            result_index = next(
                index
                for index, (kind, value) in enumerate(events)
                if kind == "readback"
                and isinstance(value, H1PostSealRecoveryRecordV1)
                and value.kind == "STAGE_RESULT"
                and value.stage == stage
            )
            assert call_index < result_index


def _assert_no_duplicate_stage_inputs(
    events: list[tuple[str, object]], existing_inputs: tuple[str, ...]
) -> None:
    appended = tuple(
        record.stage
        for kind, record in events
        if kind == "readback"
        and isinstance(record, H1PostSealRecoveryRecordV1)
        and record.kind == "STAGE_INPUT"
    )
    assert appended == tuple(stage for stage in _STAGES if stage not in existing_inputs)


@pytest.mark.asyncio
async def test_installed_restart_recovers_all_four_stages_then_replays_durable_state_without_b_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real V3→V2 restart pins and readbacks every stage around genuine B IPC."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, initial = await _seal_v3_then_v2_without_recovery(runtime)

        events: list[tuple[str, object]] = []
        async with open_installed_h1_runtime(launch, resources=resources) as restarted:
            scope_port = restarted._h1_preissuance_registration_source_port
            assert scope_port is not None

            def ordinary_terminal_must_not_recheck_p(*_args: object) -> None:
                pytest.fail("ordinary TERMINAL_WORK unexpectedly required finalization P clearance")

            monkeypatch.setattr(
                type(scope_port),
                "_check_terminal_clearance_current",
                ordinary_terminal_must_not_recheck_p,
            )
            first_engine = _install_recorders(restarted, events, monkeypatch)
            receipt = await restarted.advance_execution(advance(initial, request))
            assert hasattr(receipt, "phase"), repr(receipt)
            assert receipt.phase == "RUNNING"
            enrollment = restarted._h1_live_completion_enrollment
            assert enrollment is not None
            assert tuple(record.state for record in enrollment._recovery_records.values()) == (
                "REVOKED",
            )
            first_calls = _stage_calls(first_engine)
            _assert_readback_precedes_each_ipc(events, first_calls)
            _assert_durable_stage_evidence(restarted, first_engine, first_calls)

        async with open_installed_h1_runtime(launch, resources=resources) as durable:
            durable_events: list[tuple[str, object]] = []
            durable_engine = _install_recorders(durable, durable_events, monkeypatch)
            replay = await durable.advance_execution(advance(initial, request))
            assert replay.phase == "RUNNING"
            assert replay.disposition == "EXACT_REPLAY"
            assert [call.operation_id for call in durable_engine.calls] == [
                "deployment_trust.authenticate"
            ]
            assert all(kind != "readback" for kind, _value in durable_events)


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_stage", _STAGES)
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_installed_restart_replays_input_only_crash_cut_with_fresh_b_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_stage: str
) -> None:
    """A reply that dies before result CAS is replayed from its exact durable input."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, initial = await _seal_v3_then_v2_without_recovery(runtime)

        first_events: list[tuple[str, object]] = []
        with monkeypatch.context() as patch:
            async with open_installed_h1_runtime(launch, resources=resources) as crashed:
                first_engine = _install_recorders(
                    crashed,
                    first_events,
                    patch,
                    crash_after_operation=_OPERATIONS[crash_stage],
                )
                with pytest.raises(_CrashAfterOwnerReply):
                    await crashed.advance_execution(advance(initial, request))
                state = crashed._h1_postseal_recovery_journal.scan().states_by_root[0][1]
                assert state.stage_input(crash_stage)[0]
                assert crash_stage not in dict(state.results)
                first_calls = _stage_calls(first_engine, _STAGES[: _STAGES.index(crash_stage) + 1])
                existing_inputs = tuple(stage for stage, _bytes, _command_id in state.inputs)
                existing_results = tuple(stage for stage, _bytes in state.results)

        second_events: list[tuple[str, object]] = []
        async with open_installed_h1_runtime(launch, resources=resources) as restarted:
            second_engine = _install_recorders(restarted, second_events, monkeypatch)
            await restarted.advance_execution(advance(initial, request))
            second_calls = _stage_calls(second_engine)
            _assert_readback_precedes_each_ipc(
                second_events,
                second_calls,
                existing_inputs=existing_inputs,
                existing_results=existing_results,
            )
            _assert_no_duplicate_stage_inputs(second_events, existing_inputs)
            _assert_durable_stage_evidence(restarted, second_engine, second_calls)
            for stage, first in first_calls.items():
                recovered = second_calls[stage]
                assert recovered is not first
                assert _semantic_input(stage, recovered) == _semantic_input(stage, first)
                assert (
                    recovered.caller.broker_epoch,
                    recovered.caller.generation_id,
                    recovered.caller.session_id,
                    recovered.callee.broker_epoch,
                    recovered.callee.generation_id,
                    recovered.callee.session_id,
                ) != (
                    first.caller.broker_epoch,
                    first.caller.generation_id,
                    first.caller.session_id,
                    first.callee.broker_epoch,
                    first.callee.generation_id,
                    first.callee.session_id,
                )
                first_returned = first_engine.results[first.request_id]
                recovered_returned = second_engine.results[recovered.request_id]
                assert isinstance(first_returned, PublicPortSuccess)
                assert isinstance(recovered_returned, PublicPortSuccess)
                assert recovered_returned.responder != first_returned.responder


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_stage", _STAGES)
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_installed_restart_replays_result_only_crash_cut_with_fresh_b_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_stage: str
) -> None:
    """A durable result survives restart as an equality witness for fresh B replay."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    original_append = H1PostSealRecoveryJournal.append_transition

    def append_then_crash(
        journal: H1PostSealRecoveryJournal,
        record: H1PostSealRecoveryRecordV1,
        *,
        expected_global_tip: str | None,
    ) -> Any:
        receipt = original_append(journal, record, expected_global_tip=expected_global_tip)
        if record.kind == "STAGE_RESULT" and record.stage == crash_stage:
            raise _CrashAfterResultReadback()
        return receipt

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            request, initial = await _seal_v3_then_v2_without_recovery(runtime)

        with monkeypatch.context() as patch:
            patch.setattr(H1PostSealRecoveryJournal, "append_transition", append_then_crash)
            async with open_installed_h1_runtime(launch, resources=resources) as crashed:
                crash_events: list[tuple[str, object]] = []
                _install_recorders(crashed, crash_events, patch)
                with pytest.raises(_CrashAfterResultReadback):
                    await crashed.advance_execution(advance(initial, request))
                state = crashed._h1_postseal_recovery_journal.scan().states_by_root[0][1]
                assert dict(state.results)[crash_stage]
                existing_inputs = tuple(stage for stage, _bytes, _command_id in state.inputs)
                existing_results = tuple(stage for stage, _bytes in state.results)
                durable_results = dict(state.results)

        async with open_installed_h1_runtime(launch, resources=resources) as restarted:
            events: list[tuple[str, object]] = []
            engine = _install_recorders(restarted, events, monkeypatch)
            replay = await restarted.advance_execution(advance(initial, request))
            if crash_stage == "TERMINAL_WORK":
                assert replay.disposition == "EXACT_REPLAY"
                assert engine.calls == []
                assert not any(kind == "readback" for kind, _value in events)
            else:
                calls = _stage_calls(engine)
                _assert_readback_precedes_each_ipc(
                    events,
                    calls,
                    existing_inputs=existing_inputs,
                    existing_results=existing_results,
                )
                _assert_no_duplicate_stage_inputs(events, existing_inputs)
                _assert_durable_stage_evidence(restarted, engine, calls)
                state = restarted._h1_postseal_recovery_journal.scan().states_by_root[0][1]
                for stage in existing_results:
                    returned = engine.results[calls[stage].request_id]
                    assert isinstance(returned, PublicPortSuccess)
                    assert returned.canonical_payload == durable_results[stage]
                assert (
                    _semantic_input(crash_stage, calls[crash_stage])
                    == state.stage_input(crash_stage)[0]
                )
