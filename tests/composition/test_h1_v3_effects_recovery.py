"""Mounted recovery of the selected SCOPED_V3 Effects suffix."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    PrepareH1ScopedDeliveryV1,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
    open_installed_h1_scoped_runtime,
)
from chiplog.composition.h1_completion_exchange_registry import H1CompletionExchangeRegistry
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_coordinator import H1PostSealRecoveryCoordinatorError
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.h1_v3_recovery_historical_source import H1V3RecoveryHistoricalSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform import r7_runtime as broker_runtime
from chiplog.platform.operator_grant_key_pin import OperatorGrantKeyPinFileV2
from chiplog.platform.operator_policy_key_pin import OperatorPolicyKeyPinFileV1
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)
from tests.support.h1_v3_recovery import _issue_reconstructed_grant, _recover_b_prefix

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


def _write_pins(database: Path) -> None:
    public = _KEY.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    policy = OperatorPolicyKeyPinFileV1(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=("ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",),
        status="ACTIVE",
        public_key=public,
    )
    grant = OperatorGrantKeyPinFileV2(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=("ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",),
        status="ACTIVE",
        public_key=public,
    )
    for path, pin in (
        (database.with_suffix(database.suffix + ".operator-policy-key.json"), policy),
        (database.with_suffix(database.suffix + ".operator-grant-key.json"), grant),
    ):
        path.write_bytes(pin.canonical_bytes())
        path.chmod(0o600)


async def _selected_root(runtime: CommonCliExecutionRuntime) -> tuple[Any, Any, Any]:
    request = await _admit(runtime)
    initial = cast(Any, await runtime.drive_input(request))
    runtime._execution_model._responses = (
        DeliveryCompletion(
            tenant=TENANT,
            run_id=initial.stable_run_lineage_id,
            turn_id=initial.stable_run_lineage_id + "/turn/1",
            deliveries=(
                ProposedDelivery(payload=(Commentary(text="scoped V3 effects recovery"),)),
            ),
        ).canonical_bytes(),
    )
    started = await runtime.begin_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
    )
    captured = await runtime.capture_execution(
        "hermetic-ingress", initial.stable_run_lineage_id, started.head
    )
    assert select_h1_v3_prepare_for_candidate(
        runtime, captured, expected_head=captured.head
    ).decision_id
    assert (
        await runtime.seal_execution_complete(
            "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V2"
        )
    ).state == "ACTIVE"
    locator = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
    )
    root = H1PostSealRecoveryRootSource(runtime).derive_on_restart(
        request.identity, request.original_driver_command_fingerprint(), locator
    )
    journal = runtime._h1_postseal_recovery_journal
    receipt = journal.append_transition(
        H1PostSealRecoveryTransition.begin_selected(
            H1PostSealRecoveryState.empty(root), producer_choice="SCOPED_V3"
        ),
        expected_global_tip=journal.scan().tip,
    )
    return request, locator, receipt.scan.state_for_root(root.root_id())


async def _prepared_recovery(runtime: CommonCliExecutionRuntime) -> tuple[Any, Any, Any]:
    request, locator, state = await _selected_root(runtime)
    state = await _recover_b_prefix(runtime, request, locator, state)
    await _issue_reconstructed_grant(runtime, request, locator)
    return request, locator, state


async def _pin_scoped_effects_input(
    runtime: CommonCliExecutionRuntime, request: Any, locator: Any, state: Any
) -> tuple[Any, bytes]:
    """Persist only the V3 semantic input, then retire its initial inventory capture."""
    coordinator = runtime._h1_postseal_recovery_coordinator
    registry = runtime._h1_completion_exchange_registry
    assert coordinator is not None
    assert type(registry) is H1CompletionExchangeRegistry
    source = coordinator._source
    context: object | None = None
    session: object | None = None
    async with await coordinator._fence.acquire() as lease:
        try:
            context = source._capture_recovery(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
                root=state.root,
            )
            session = coordinator._open_recovery_session(
                source=source, context=context, lease=lease
            )
            session._bind_recovery(source=source, context=context, lease=lease)
            historical = registry.capture_recovered_prepared_delivery_historical(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            semantic_input, command_id = await session._pin_scoped_recovery_effects(
                historical_capture=historical
            )
            state = coordinator._append_stage_held(
                lease,
                state,
                H1PostSealRecoveryTransition.pin_input(
                    state,
                    stage="EFFECTS",
                    semantic_input=semantic_input,
                    effects_command_id=command_id,
                ),
            )
            return state, semantic_input
        finally:
            if session is not None:
                await coordinator._drain_session_held(lease, session)
            if context is not None:
                source._retire_recovery_context(context)


def _records(runtime: CommonCliExecutionRuntime, root_id: str) -> tuple[Any, ...]:
    with runtime._authority_gate().hold():
        return runtime._h1_postseal_recovery_journal._read_selected_root_entries_held(
            root_id
        ).records


async def _pin_scoped_terminal_input(
    runtime: CommonCliExecutionRuntime, request: Any, locator: Any, state: Any
) -> tuple[Any, bytes]:
    """Persist the authenticated V3 terminal request without admitting its owner."""
    evidence = H1V3RecoveryHistoricalSource(runtime).read_terminal(
        original_identity=request.identity,
        original_fingerprint=request.original_driver_command_fingerprint(),
        selected_seal=locator,
    )
    assert evidence.terminal_result is None
    semantic_input = evidence.terminal_request.canonical_bytes()
    coordinator = runtime._h1_postseal_recovery_coordinator
    assert coordinator is not None
    async with await coordinator._fence.acquire() as lease:
        state = coordinator._append_stage_held(
            lease,
            state,
            H1PostSealRecoveryTransition.pin_input(
                state, stage="TERMINAL_WORK", semantic_input=semantic_input
            ),
        )
    return state, semantic_input


async def _resume_to_scoped_effects(
    runtime: CommonCliExecutionRuntime,
    request: Any,
    locator: Any,
    state: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Run the real V3 prefix and stop before terminal admission."""
    coordinator = runtime._h1_postseal_recovery_coordinator
    assert coordinator is not None

    async def stop_terminal(**_kwargs: Any) -> Any:
        raise H1PostSealRecoveryCoordinatorError("test stop before terminal work")

    with monkeypatch.context() as scoped:
        scoped.setattr(coordinator, "_resume_scoped_v3_terminal_work_held", stop_terminal)
        with pytest.raises(H1PostSealRecoveryCoordinatorError, match="test stop"):
            await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
    with runtime._authority_gate().hold():
        effects_complete = runtime._h1_postseal_recovery_journal.scan().state_for_root(
            state.root.root_id()
        )
    assert len(_records(runtime, effects_complete.root.root_id())) == 7
    return effects_complete


@pytest.mark.asyncio
async def test_selected_scoped_v3_resume_reaches_terminal_owner_and_replays_nine_record_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            request, locator, _state = await _prepared_recovery(runtime)
            effects_calls = 0
            terminal_calls = 0
            all_owner_calls = 0
            terminal_request_payloads: list[bytes] = []
            terminal_result_payloads: list[bytes] = []
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal all_owner_calls, effects_calls, terminal_calls
                all_owner_calls += 1
                if sent.operation_id == "effects.prepare_h1_scoped_delivery":
                    effects_calls += 1
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_calls += 1
                    terminal_request_payloads.append(sent.canonical_payload)
                returned = await original(sent, **kwargs)
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_result_payloads.append(returned.canonical_payload)
                return returned

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            completed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert effects_calls == 1
            assert terminal_calls == 1
            records = _records(runtime, completed.root.root_id())
            assert tuple((record.kind, record.stage) for record in records) == (
                ("ROOT", None),
                ("STAGE_INPUT", "COMPLETION"),
                ("STAGE_RESULT", "COMPLETION"),
                ("STAGE_INPUT", "CONVERSATION"),
                ("STAGE_RESULT", "CONVERSATION"),
                ("STAGE_INPUT", "EFFECTS"),
                ("STAGE_RESULT", "EFFECTS"),
                ("STAGE_INPUT", "TERMINAL_WORK"),
                ("STAGE_RESULT", "TERMINAL_WORK"),
            )
            evidence = H1V3RecoveryHistoricalSource(runtime).read_effects(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert evidence.effects_result is not None
            assert evidence.effects_result.disposition == "PREPARED"
            assert evidence.effects_result.canonical_bytes() == dict(completed.results)["EFFECTS"]
            terminal = H1V3RecoveryHistoricalSource(runtime).read_terminal(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert terminal.terminal_result is not None
            assert terminal_request_payloads == [terminal.terminal_request.canonical_bytes()]
            assert terminal_result_payloads == [terminal.terminal_result.canonical_bytes()]
            assert completed.stage_input("TERMINAL_WORK")[0] == (
                terminal.terminal_request.canonical_bytes()
            )
            assert (
                terminal.terminal_result.canonical_bytes()
                == dict(completed.results)["TERMINAL_WORK"]
            )
            owner_calls_before_replay = all_owner_calls
            replayed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert replayed == completed
            assert effects_calls == 1
            assert terminal_calls == 1
            assert all_owner_calls == owner_calls_before_replay


@pytest.mark.asyncio
async def test_selected_scoped_v3_eight_record_restart_dispatches_only_the_pinned_terminal_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            request, locator, _state = await _prepared_recovery(runtime)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            effects_complete = await _resume_to_scoped_effects(
                runtime, request, locator, _state, monkeypatch
            )
            pinned, terminal_input = await _pin_scoped_terminal_input(
                runtime, request, locator, effects_complete
            )
            assert len(_records(runtime, pinned.root.root_id())) == 8
            effects_calls = 0
            terminal_calls = 0
            all_owner_calls = 0
            terminal_request_payloads: list[bytes] = []
            terminal_result_payloads: list[bytes] = []
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal all_owner_calls, effects_calls, terminal_calls
                all_owner_calls += 1
                if sent.operation_id == "effects.prepare_h1_scoped_delivery":
                    effects_calls += 1
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_calls += 1
                    terminal_request_payloads.append(sent.canonical_payload)
                returned = await original(sent, **kwargs)
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_result_payloads.append(returned.canonical_payload)
                return returned

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            completed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert effects_calls == 0
            assert terminal_calls == 1
            assert all_owner_calls == 1
            assert terminal_request_payloads == [terminal_input]
            assert terminal_result_payloads == [dict(completed.results)["TERMINAL_WORK"]]
            assert len(_records(runtime, completed.root.root_id())) == 9
            assert completed.stage_input("TERMINAL_WORK")[0] == terminal_input


@pytest.mark.asyncio
async def test_selected_scoped_v3_cancellation_after_terminal_result_drains_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            request, locator, state = await _prepared_recovery(runtime)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            terminal_calls = 0
            terminal_result_committed = asyncio.Event()
            drain_entered = asyncio.Event()
            release_drain = asyncio.Event()
            contender_acquired = asyncio.Event()
            broker = runtime._supervisor.runtime()
            original_call = broker._call_with_admission_guard
            original_append = coordinator._append_stage_held
            original_drain = coordinator._drain_session_held
            recovery_task: asyncio.Task[Any] | None = None

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal terminal_calls
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_calls += 1
                return await original_call(sent, **kwargs)

            def cancel_after_terminal_result(lease: Any, state: Any, record: Any) -> Any:
                appended = original_append(lease, state, record)
                if record.kind == "STAGE_RESULT" and record.stage == "TERMINAL_WORK":
                    assert recovery_task is asyncio.current_task()
                    terminal_result_committed.set()
                    recovery_task.cancel()
                return appended

            async def hold_drain(lease: Any, session: Any) -> None:
                drain_entered.set()
                cancelled: asyncio.CancelledError | None = None
                while not release_drain.is_set():
                    try:
                        await asyncio.shield(release_drain.wait())
                    except asyncio.CancelledError as error:
                        cancelled = error
                await original_drain(lease, session)
                if cancelled is not None:
                    raise cancelled

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            monkeypatch.setattr(coordinator, "_append_stage_held", cancel_after_terminal_result)
            monkeypatch.setattr(coordinator, "_drain_session_held", hold_drain)
            recovery_task = asyncio.create_task(
                coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint(), locator
                )
            )
            await terminal_result_committed.wait()
            await drain_entered.wait()

            async def contend() -> None:
                async with await coordinator._fence.acquire() as lease:
                    lease.require_owned()
                    contender_acquired.set()

            contender = asyncio.create_task(contend())
            await asyncio.sleep(0)
            assert not contender_acquired.is_set()
            release_drain.set()
            with pytest.raises(asyncio.CancelledError):
                await recovery_task
            await asyncio.wait_for(contender, timeout=1)
            assert contender_acquired.is_set()
            assert terminal_calls == 1
            records = _records(runtime, state.root.root_id())
            terminal = H1V3RecoveryHistoricalSource(runtime).read_terminal(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert terminal.terminal_result is not None
            assert len(records) == 9
            replayed = await coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert replayed.stage_input("TERMINAL_WORK")[0] == (
                terminal.terminal_request.canonical_bytes()
            )
            assert terminal_calls == 1


@pytest.mark.asyncio
async def test_selected_scoped_v3_tampered_terminal_pin_rejects_before_terminal_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            request, locator, _state = await _prepared_recovery(runtime)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            effects_complete = await _resume_to_scoped_effects(
                runtime, request, locator, _state, monkeypatch
            )
            terminal = H1V3RecoveryHistoricalSource(runtime).read_terminal(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            async with await coordinator._fence.acquire() as lease:
                tampered = coordinator._append_stage_held(
                    lease,
                    effects_complete,
                    H1PostSealRecoveryTransition.pin_input(
                        effects_complete,
                        stage="TERMINAL_WORK",
                        semantic_input=terminal.terminal_request.canonical_bytes() + b" ",
                    ),
                )
            assert len(_records(runtime, tampered.root.root_id())) == 8
            terminal_calls = 0
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal terminal_calls
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_calls += 1
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            with pytest.raises(H1PostSealRecoveryCoordinatorError):
                await coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint(), locator
                )
            assert terminal_calls == 0
            assert len(_records(runtime, tampered.root.root_id())) == 8


@pytest.mark.asyncio
async def test_selected_scoped_v3_six_record_restart_reuses_the_pinned_effects_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=_resources(tmp_path)
        ) as runtime:
            request, locator, state = await _prepared_recovery(runtime)
            state, pinned_input = await _pin_scoped_effects_input(runtime, request, locator, state)
            assert len(_records(runtime, state.root.root_id())) == 6
            pinned_request = PrepareH1ScopedDeliveryV1.model_validate_json(pinned_input)
            clock_epoch, current_time_ns = runtime._require_dispatch_resources().clock()
            assert clock_epoch == pinned_request.current.clock_epoch
            assert pinned_request.current.observed_time_ns < current_time_ns
            assert current_time_ns < pinned_request.current.lease_expires_at_ns
            calls = 0
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal calls
                if sent.operation_id == "effects.prepare_h1_scoped_delivery":
                    calls += 1
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            completed = await _resume_to_scoped_effects(
                runtime, request, locator, state, monkeypatch
            )
            assert calls == 1
            records = _records(runtime, completed.root.root_id())
            assert len(records) == 7
            assert completed.stage_input("EFFECTS")[0] == pinned_input
            evidence = H1V3RecoveryHistoricalSource(runtime).read_effects(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            assert evidence.effects_request.canonical_bytes() == pinned_input


@pytest.mark.asyncio
async def test_selected_scoped_v3_revoked_resource_rejects_before_terminal_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    resources = _resources(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(launch, resources=resources) as runtime:
            request, locator, _state = await _prepared_recovery(runtime)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            effects_complete = await _resume_to_scoped_effects(
                runtime, request, locator, _state, monkeypatch
            )
            terminal_calls = 0
            broker = runtime._supervisor.runtime()
            original = broker._call_with_admission_guard

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal terminal_calls
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_calls += 1
                return await original(sent, **kwargs)

            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            original_records = tuple(
                record.canonical_bytes
                for record in _records(runtime, effects_complete.root.root_id())
            )
            assert len(original_records) == 7
            resources.revoke()
            with pytest.raises(H1PostSealRecoveryCoordinatorError):
                await coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint(), locator
                )
            assert terminal_calls == 0
            records = _records(runtime, effects_complete.root.root_id())
            assert len(records) == 7
            assert tuple(record.canonical_bytes for record in records) == original_records


@pytest.mark.asyncio
async def test_selected_scoped_v3_revocation_at_terminal_admission_keeps_only_pinned_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal guard rechecks the real recovered authority before owner IPC."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    _write_pins(slot.database_path)
    resources = _resources(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(launch, resources=resources) as runtime:
            request, locator, state = await _prepared_recovery(runtime)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            effects_complete = await _resume_to_scoped_effects(
                runtime, request, locator, state, monkeypatch
            )
            original_seven = tuple(
                record.canonical_bytes
                for record in _records(runtime, effects_complete.root.root_id())
            )
            assert len(original_seven) == 7
            terminal = H1V3RecoveryHistoricalSource(runtime).read_terminal(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=locator,
            )
            authority_reader = runtime._h1_scoped_delivery_authority_reader
            original_recheck = authority_reader.recheck
            rechecks = 0
            terminal_attempts = 0
            terminal_frames = 0
            broker = runtime._supervisor.runtime()
            original_call = broker._call_with_admission_guard
            original_send_frame = broker_runtime._send_frame

            def revoke_before_terminal_admission(reader: Any, capture: Any) -> Any:
                nonlocal rechecks
                rechecks += 1
                if rechecks == 2:
                    resources.revoke()
                assert reader is authority_reader
                return original_recheck(capture)

            async def counted(sent: Any, **kwargs: Any) -> Any:
                nonlocal terminal_attempts
                if sent.operation_id == "agent_loop.prepare_terminal_work":
                    terminal_attempts += 1
                return await original_call(sent, **kwargs)

            def count_terminal_frames(connection: Any, secret: bytes, payload: bytes) -> None:
                nonlocal terminal_frames
                if json.loads(payload).get("operation") == "agent_loop.prepare_terminal_work":
                    terminal_frames += 1
                original_send_frame(connection, secret, payload)

            monkeypatch.setattr(type(authority_reader), "recheck", revoke_before_terminal_admission)
            monkeypatch.setattr(broker, "_call_with_admission_guard", counted)
            monkeypatch.setattr(broker_runtime, "_send_frame", count_terminal_frames)
            with pytest.raises(H1PostSealRecoveryCoordinatorError):
                await coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint(), locator
                )
            assert rechecks == 2
            assert terminal_attempts == 1
            assert terminal_frames == 0
            records = _records(runtime, effects_complete.root.root_id())
            assert len(records) == 8
            assert tuple(record.canonical_bytes for record in records[:7]) == original_seven
            pinned = records[7]
            assert pinned.kind == "STAGE_INPUT"
            assert pinned.stage == "TERMINAL_WORK"
            with runtime._authority_gate().hold():
                pinned_state = runtime._h1_postseal_recovery_journal.scan().state_for_root(
                    effects_complete.root.root_id()
                )
            assert pinned_state.stage_input("TERMINAL_WORK")[0] == (
                terminal.terminal_request.canonical_bytes()
            )
