"""Mounted RED witness for the H1 V2 seal-to-recovery handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Never, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryAppendReceipt,
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordV1,
    H1PostSealRecoveryRecordV2,
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_coordinator import (
    H1PostSealRecoveryCoordinatorError,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortCall
from tests.support.h1_cli_execution import _admit, advance
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _selected_seal_locator(runtime, run_head: str) -> CallSubjectHead:
    raw = next(
        raw
        for _, _, raw in runtime._loop_decisions().entries()
        if json.loads(raw).get("operation_id") == run_head
    )
    retained = RetainedExecutionCompleteSealV2.model_validate_json(
        json.loads(raw)["execution_complete_seal"]
    )
    response_seal = retained.exchange.proposal.fan_out.response_seal
    return CallSubjectHead(
        subject_id=response_seal.response_seal_id,
        revision=Present(
            head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
        ),
    )


class _RecordingEngine:
    """Observe real owner calls while retaining the installed engine's behavior."""

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


def _install_recorder(runtime: Any, monkeypatch: pytest.MonkeyPatch) -> _RecordingEngine:
    private = cast(Any, runtime)
    engine = _RecordingEngine(private._supervisor.runtime())
    monkeypatch.setattr(private._supervisor, "runtime", lambda: engine)
    return engine


def _recovery_body_and_tip(runtime: Any) -> tuple[bytes, str | None]:
    private = cast(Any, runtime)
    journal = private._h1_postseal_recovery_journal
    body_path, _device, _inode = journal._journal.physical_sources()[0]
    return Path(body_path).read_bytes(), journal.scan().tip


async def _install_scoped_root(runtime: Any) -> Any:
    """Install one genuine selected seal and its authenticated SCOPED_V3 ROOT."""
    private = cast(Any, runtime)
    request = await _admit(runtime)
    initial = await runtime.drive_input(request)
    private._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(ProposedDelivery(payload=(Commentary(text="scoped root"),)),),
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

    locator = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
    )
    root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
        request.identity, request.original_driver_command_fingerprint(), locator
    )
    journal = private._h1_postseal_recovery_journal
    receipt = journal.append_transition(
        H1PostSealRecoveryTransition.begin_selected(
            H1PostSealRecoveryState.empty(root), producer_choice="SCOPED_V3"
        ),
        expected_global_tip=journal.scan().tip,
    )
    assert receipt.scan.state_for_root(root.root_id()).selected_producer == "SCOPED_V3"
    return request


@pytest.mark.asyncio
async def test_installed_advance_persists_its_independently_derived_root(
    tmp_path: Path,
) -> None:
    """A durable V2 seal independently creates its recovery ROOT."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="recover root"),)),),
                ).canonical_bytes(),
            )

            result = await runtime.advance_execution(advance(initial, request))

            locator = _selected_seal_locator(runtime, result.selected_run_head.head)
            expected_root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            selected_roots = [
                state.root
                for _, state in runtime._h1_postseal_recovery_journal.scan().states_by_root
                if (
                    state.root.selected_seal_subject_id,
                    state.root.selected_seal_head,
                    state.root.selected_seal_fingerprint,
                )
                == (
                    locator.subject_id,
                    locator.revision.head,
                    locator.revision.fingerprint,
                )
            ]

            assert selected_roots == [expected_root]
            assert result.phase == "RUNNING"
            assert result.disposition == "COMMITTED"


@pytest.mark.asyncio
async def test_root_append_uncertainty_reopens_and_reconciles_without_a_second_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A possibly durable ROOT is reconciled from a fresh enrolled wrapper."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    original_append = H1PostSealRecoveryJournal.append_transition
    append_calls = 0

    def append_then_lose_readback(
        self: H1PostSealRecoveryJournal,
        record: H1PostSealRecoveryRecordV1 | H1PostSealRecoveryRecordV2,
        *,
        expected_global_tip: str | None,
    ) -> H1PostSealRecoveryAppendReceipt:
        nonlocal append_calls
        if record.kind == "ROOT":
            append_calls += 1
        receipt = original_append(self, record, expected_global_tip=expected_global_tip)
        if record.kind == "ROOT":
            raise OSError("simulated post-append readback loss")
        return receipt

    monkeypatch.setattr(H1PostSealRecoveryJournal, "append_transition", append_then_lose_readback)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="uncertain root"),)),),
                ).canonical_bytes(),
            )

            result = await runtime.advance_execution(advance(initial, request))

            assert result.phase == "RUNNING"
            assert append_calls == 1
            journal = runtime._h1_postseal_recovery_journal
            assert journal is not None
            states = journal.scan().states_by_root
            assert len(states) == 1
            assert states[0][1].selected_producer == "LOCAL_V2"
            assert len(states[0][1].inputs) == len(states[0][1].results) == 4


@pytest.mark.asyncio
async def test_scoped_root_fails_closed_before_local_recovery_or_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCOPED failures preserve its selection and never fall back to LOCAL_V2.

    The historical nodeid predates the supported scoped route. Its shared
    source capture and finalization classification are now legitimate seams.
    """
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _install_scoped_root(runtime)
            private = cast(Any, runtime)
            coordinator = private._h1_postseal_recovery_coordinator
            assert coordinator is not None
            journal = runtime._h1_postseal_recovery_journal
            assert journal is not None
            states = journal.scan().states_by_root
            assert len(states) == 1
            selected = states[0][1]
            assert selected.selected_producer == "SCOPED_V3"
            engine = _install_recorder(runtime, monkeypatch)
            before = _recovery_body_and_tip(runtime)
            source_error = RuntimeError("scoped source unavailable")
            authority_error = RuntimeError("scoped authority unavailable")
            reached: list[str] = []

            def local_must_not_run(_state: H1PostSealRecoveryState) -> Never:
                raise AssertionError("SCOPED_V3 reached LOCAL_V2 fallback")

            def source_unavailable(actual_source: object, **kwargs: object) -> Never:
                assert actual_source is coordinator._source
                assert kwargs["original_identity"] == request.identity
                assert (
                    kwargs["original_fingerprint"] == request.original_driver_command_fingerprint()
                )
                assert kwargs["root"] == selected.root
                reached.append("source")
                raise source_error

            authority = private._h1_live_publication_authority

            async def authority_unavailable(actual_authority: object, **kwargs: object) -> Never:
                assert actual_authority is authority
                assert kwargs["identity"] == request.identity
                assert (
                    kwargs["original_fingerprint"] == request.original_driver_command_fingerprint()
                )
                reached.append("authority")
                raise authority_error

            monkeypatch.setattr(
                type(coordinator), "_require_local_producer", staticmethod(local_must_not_run)
            )
            monkeypatch.setattr(type(coordinator._source), "_capture_recovery", source_unavailable)
            monkeypatch.setattr(
                type(authority), "_recover_finalization_held", authority_unavailable
            )

            with pytest.raises(H1PostSealRecoveryCoordinatorError) as resumed:
                await coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint()
                )
            assert resumed.value.__cause__ is source_error
            assert reached == ["source"]
            assert _recovery_body_and_tip(runtime) == before
            assert engine.calls == []

            with pytest.raises(RuntimeError) as finalized:
                await coordinator.finalize_selected(
                    request.identity, request.original_driver_command_fingerprint()
                )
            assert finalized.value is authority_error
            assert reached == ["source", "authority"]
            assert _recovery_body_and_tip(runtime) == before
            assert journal.scan().states_by_root[0][1].selected_producer == "SCOPED_V3"
            assert engine.calls == []
