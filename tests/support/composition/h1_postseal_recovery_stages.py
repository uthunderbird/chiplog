"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from typing import Any, cast

import pytest
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordV1,
)
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.platform.broker import PublicPortCall

_STAGES = ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")


_OPERATIONS = {
    "COMPLETION": "agent_loop.prepare_first_path_completion",
    "CONVERSATION": "projections.prepare_conversation_completion",
    "EFFECTS": "effects.prepare_h1_local_commentary",
    "TERMINAL_WORK": "agent_loop.prepare_terminal_work",
}


class _CrashAfterOwnerReply(RuntimeError):
    """Simulated process loss after an inert owner reply and before result CAS."""


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
