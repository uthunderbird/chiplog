"""Installed RED witnesses for the separate H1 post-seal finalizer.

The tests build their recovery evidence through the mounted driver.  They never
construct a recovery ROOT, owner result, issuance, batch, or authority marker.
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
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_runtime,
)
from chiplog.composition.common_execution_driver_contracts import ExecutionDriverRejectedV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryJournal
from chiplog.composition.h1_postseal_recovery_coordinator import (
    H1PostSealRecoveryCoordinatorError,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortCall
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot

_STAGES = ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")


class H1PostSealFinalizationUnavailable(RuntimeError):
    """The frozen private finalization entry has not been installed yet."""


_RED = pytest.mark.xfail(
    strict=True,
    raises=H1PostSealFinalizationUnavailable,
    reason="separate installed H1 finalize_execution is absent",
)


class _RecordingEngine:
    """Observe actual broker frames without supplying any synthetic result."""

    def __init__(self, installed: Any) -> None:
        self._installed = installed
        self.calls: list[PublicPortCall] = []

    def session(self, owner: str) -> Any:
        return self._installed.session(owner)

    async def call(self, sent: PublicPortCall) -> Any:
        self.calls.append(sent)
        return await self._installed.call(sent)

    async def _call_with_admission_guard(
        self, sent: PublicPortCall, *, admission_guard: Any, authority_gate: Any = None
    ) -> Any:
        self.calls.append(sent)
        return await self._installed._call_with_admission_guard(
            sent, admission_guard=admission_guard, authority_gate=authority_gate
        )


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _install_recorder(
    runtime: CommonCliExecutionRuntime, monkeypatch: pytest.MonkeyPatch
) -> _RecordingEngine:
    private = cast(Any, runtime)
    engine = _RecordingEngine(private._supervisor.runtime())
    monkeypatch.setattr(private._supervisor, "runtime", lambda: engine)
    return engine


async def _seal_v3_then_v2_without_recovery(
    runtime: CommonCliExecutionRuntime,
) -> tuple[Any, Any]:
    """Create the real selected seal while deliberately leaving ROOT absent."""
    private = cast(Any, runtime)
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="finalization preflight"),)),),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    sealed = await runtime.seal_execution_complete(
        "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
    )
    assert sealed.state == "ACTIVE"
    assert private._h1_postseal_recovery_journal.scan().states_by_root == ()
    return request, initial


async def _durable_four_stage_chain(
    runtime: CommonCliExecutionRuntime,
) -> tuple[Any, Any]:
    """Reach the genuine four-result recovery cut through normal advance."""
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    private = cast(Any, runtime)
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="finalization durable cut"),)),),
        ).canonical_bytes(),
    )
    await runtime.advance_execution(advance(initial, request))
    states = private._h1_postseal_recovery_journal.scan().states_by_root
    assert len(states) == 1
    state = states[0][1]
    assert tuple(stage for stage, _raw, _command_id in state.inputs) == _STAGES
    assert tuple(stage for stage, _raw in state.results) == _STAGES
    return request, initial


async def _finalize(runtime: CommonCliExecutionRuntime, request: Any) -> Any:
    """Call only the frozen entrypoint; preparation is not a fallback."""
    finalizer = getattr(runtime, "finalize_execution", None)
    if not callable(finalizer):
        raise H1PostSealFinalizationUnavailable("installed finalize_execution is absent")
    return await finalizer(request.identity, request.original_driver_command_fingerprint())


def _assert_held_without_b(result: object, engine: _RecordingEngine) -> None:
    assert isinstance(result, ExecutionDriverRejectedV1)
    assert result.code == "HOLD"
    assert engine.calls == []


def _corrupt_open_recovery_body(runtime: CommonCliExecutionRuntime) -> None:
    """Damage one authenticated body byte without replacing the enrolled inode."""
    private = cast(Any, runtime)
    body_path, _device, _inode = private._h1_postseal_recovery_journal._journal.physical_sources()[
        0
    ]
    with Path(body_path).open("r+b") as body:
        first = body.read(1)
        assert first
        body.seek(0)
        body.write(b"!" if first != b"!" else b"?")
        body.flush()


def _recovery_body_and_tip(runtime: CommonCliExecutionRuntime) -> tuple[bytes, str | None]:
    """Observe the mounted journal without manufacturing recovery evidence."""
    private = cast(Any, runtime)
    journal = private._h1_postseal_recovery_journal
    body_path, _device, _inode = journal._journal.physical_sources()[0]
    return Path(body_path).read_bytes(), journal.scan().tip


@pytest.mark.asyncio
async def test_held_finalization_preflight_revalidates_a_complete_durable_chain_without_b_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finalizer gets only a lease-bound capture of real durable evidence."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _durable_four_stage_chain(runtime)

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as restarted:
            engine = _install_recorder(restarted, monkeypatch)
            before = _recovery_body_and_tip(restarted)

            def append_must_not_run(*_args: object, **_kwargs: object) -> object:
                raise AssertionError(
                    "read-only finalization preflight appended to recovery journal"
                )

            monkeypatch.setattr(H1PostSealRecoveryJournal, "append_transition", append_must_not_run)
            coordinator = cast(Any, restarted)._h1_postseal_recovery_coordinator
            assert coordinator is not None
            async with await coordinator._fence.acquire() as lease:
                capture = coordinator._preflight_complete_chain_held(
                    lease, request.identity, request.original_driver_command_fingerprint()
                )
                assert capture._state.next_stage == "COMPLETE"
                coordinator._require_complete_chain_preflight_held(lease, capture)
                coordinator._retire_complete_chain_preflight_held(lease, capture)

            assert _recovery_body_and_tip(restarted) == before
            assert engine.calls == []


@pytest.mark.asyncio
async def test_held_finalization_preflight_rejects_missing_root_without_writing_or_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finalization preflight cannot turn a selected seal into a new ROOT."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _seal_v3_then_v2_without_recovery(runtime)

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as restarted:
            engine = _install_recorder(restarted, monkeypatch)
            before = _recovery_body_and_tip(restarted)

            def append_must_not_run(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("read-only finalization preflight appended a missing ROOT")

            monkeypatch.setattr(H1PostSealRecoveryJournal, "append_transition", append_must_not_run)
            coordinator = cast(Any, restarted)._h1_postseal_recovery_coordinator
            assert coordinator is not None
            async with await coordinator._fence.acquire() as lease:
                with pytest.raises(H1PostSealRecoveryCoordinatorError, match="ROOT is absent"):
                    coordinator._preflight_complete_chain_held(
                        lease, request.identity, request.original_driver_command_fingerprint()
                    )

            assert _recovery_body_and_tip(restarted) == before
            assert engine.calls == []


@pytest.mark.asyncio
async def test_held_finalization_preflight_rejects_an_incomplete_real_root_without_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real ROOT without all four durable exchanges cannot enter finalization."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _seal_v3_then_v2_without_recovery(runtime)
            coordinator = cast(Any, runtime)._h1_postseal_recovery_coordinator
            assert coordinator is not None
            async with await coordinator._fence.acquire() as lease:
                coordinator._begin_or_resume_held(
                    lease, request.identity, request.original_driver_command_fingerprint(), None
                )

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as restarted:
            engine = _install_recorder(restarted, monkeypatch)
            before = _recovery_body_and_tip(restarted)

            def append_must_not_run(*_args: object, **_kwargs: object) -> object:
                raise AssertionError(
                    "read-only finalization preflight appended an incomplete chain"
                )

            monkeypatch.setattr(H1PostSealRecoveryJournal, "append_transition", append_must_not_run)
            coordinator = cast(Any, restarted)._h1_postseal_recovery_coordinator
            assert coordinator is not None
            async with await coordinator._fence.acquire() as lease:
                with pytest.raises(H1PostSealRecoveryCoordinatorError, match="chain is incomplete"):
                    coordinator._preflight_complete_chain_held(
                        lease, request.identity, request.original_driver_command_fingerprint()
                    )

            assert _recovery_body_and_tip(restarted) == before
            assert engine.calls == []


@pytest.mark.asyncio
async def test_installed_finalizer_holds_on_missing_root_before_any_b_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _seal_v3_then_v2_without_recovery(runtime)

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as restarted:
            engine = _install_recorder(restarted, monkeypatch)
            try:
                result = await _finalize(restarted, request)
            except H1PostSealFinalizationUnavailable:
                pytest.xfail("separate installed H1 finalize_execution is absent")
            _assert_held_without_b(result, engine)


@pytest.mark.asyncio
async def test_installed_finalizer_holds_on_corrupt_root_before_any_b_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _durable_four_stage_chain(runtime)

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as restarted:
            engine = _install_recorder(restarted, monkeypatch)
            _corrupt_open_recovery_body(restarted)
            try:
                result = await _finalize(restarted, request)
            except H1PostSealFinalizationUnavailable:
                pytest.xfail("separate installed H1 finalize_execution is absent")
            _assert_held_without_b(result, engine)


@pytest.mark.asyncio
@_RED
async def test_installed_private_finalizer_is_available_after_durable_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete durable chain is prerequisite evidence, never live authority."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, initial = await _durable_four_stage_chain(runtime)

        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as restarted:
            engine = _install_recorder(restarted, monkeypatch)
            replay = await restarted.advance_execution(advance(initial, request))
            assert replay.phase == "RUNNING"
            assert replay.disposition == "EXACT_REPLAY"
            assert engine.calls == []
            await _finalize(restarted, request)
