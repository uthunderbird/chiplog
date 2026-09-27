"""RED witnesses for B-only H1 recovery enrollment and broker admission."""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_live_completion_enrollment import H1LiveCompletionEnrollmentUnavailable
from chiplog.composition.h1_recovery_stage_source import H1RecoveryStageSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform import r7_runtime
from chiplog.platform.broker import PublicPortCall
from tests.composition.test_h1_postseal_recovery_stages import _seal_v3_then_v2_without_recovery
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot

_RED = "guarded installed B recovery enrollment/admission is not mounted yet"


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _recovery_material(
    runtime: Any,
) -> AsyncGenerator[tuple[Any, H1RecoveryStageSource, object, Any, bytes, PublicPortCall]]:
    """Yield installed source/context/lease and a real completion broker frame."""
    request, _initial = await _seal_v3_then_v2_without_recovery(runtime)
    coordinator = runtime._h1_postseal_recovery_coordinator
    enrollment = runtime._h1_live_completion_enrollment
    assert coordinator is not None
    assert enrollment is not None
    async with await coordinator._fence.acquire() as lease:
        state = coordinator._begin_or_resume_held(
            lease, request.identity, request.original_driver_command_fingerprint(), None
        )
        source = coordinator._source
        assert type(source) is H1RecoveryStageSource
        locator = CallSubjectHead(
            subject_id=state.root.selected_seal_subject_id,
            revision=Present(
                head=state.root.selected_seal_head,
                fingerprint=state.root.selected_seal_fingerprint,
            ),
        )
        context = source._capture_recovery(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=locator,
            root=state.root,
        )
        semantic_input = source._reconstruct_input(context, "COMPLETION", {}, None)
        ordinary = enrollment._open_session()
        ordinary.capture_first_path(
            original_identity=request.identity,
            original_fingerprint=request.original_driver_command_fingerprint(),
            selected_seal=locator,
        )
        exchange = await ordinary.prepare_first_path_completion()
        assert exchange.sent.canonical_payload == semantic_input
        yield enrollment, source, context, lease, semantic_input, exchange.sent


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED)
async def test_recovery_opener_rejects_foreign_or_copied_authority(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            async for enrollment, source, context, lease, _semantic, _sent in _recovery_material(
                runtime
            ):
                session = enrollment._open_recovery_session(
                    source=source, context=context, lease=lease
                )
                enrollment._require_recovery_session(
                    session=session, source=source, context=context, lease=lease
                )
                state = source._context_state(context)
                foreign_source = H1RecoveryStageSource(runtime)
                foreign_context = foreign_source._capture_recovery(
                    original_identity=state.native.original_identity,
                    original_fingerprint=state.native.original_fingerprint,
                    selected_seal=state.native.selected_seal,
                    root=state.root,
                )
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable, match=r"source|registered"
                ):
                    enrollment._open_recovery_session(
                        source=foreign_source, context=foreign_context, lease=lease
                    )
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable, match=r"context|registered"
                ):
                    enrollment._require_recovery_session(
                        session=session,
                        source=source,
                        context=object.__new__(type(context)),
                        lease=lease,
                    )
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable, match=r"lease|registered"
                ):
                    enrollment._require_recovery_session(
                        session=session, source=source, context=context, lease=object()
                    )
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable, match=r"session|registered"
                ):
                    enrollment._require_recovery_session(
                        session=object.__new__(type(session)),
                        source=source,
                        context=context,
                        lease=lease,
                    )
                with pytest.raises((TypeError, H1LiveCompletionEnrollmentUnavailable)):
                    copy.copy(context)


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED)
async def test_recovery_clearance_consumes_before_send_and_binds_exact_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            async for enrollment, source, context, lease, semantic, sent in _recovery_material(
                runtime
            ):
                session = enrollment._open_recovery_session(
                    source=source, context=context, lease=lease
                )
                clearance = enrollment._reserve_recovery_clearance(
                    session=session, stage="COMPLETION", semantic_input=semantic, sent=sent
                )
                guard = enrollment._recovery_admission_guard(clearance)
                engine = runtime._supervisor.runtime()
                sends: list[bytes] = []
                original_send = r7_runtime._send_frame

                def record_send(
                    *args: object,
                    _sends: list[bytes] = sends,
                    _send: Any = original_send,
                    **kwargs: object,
                ) -> None:
                    _sends.append(cast(bytes, args[2]))
                    _send(*args, **kwargs)

                monkeypatch.setattr(r7_runtime, "_send_frame", record_send)
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable, match=r"exact|frame|clearance"
                ):
                    await engine._call_with_admission_guard(
                        sent.model_copy(),
                        admission_guard=guard,
                        authority_gate=runtime._authority_gate(),
                    )
                assert sends == []
                result = await engine._call_with_admission_guard(
                    sent,
                    admission_guard=guard,
                    authority_gate=runtime._authority_gate(),
                )
                assert result.request_id == sent.request_id
                assert len(sends) == 1
                enrollment._require_admitted_recovery(clearance, session=session, sent=sent)
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable,
                    match=r"one.use|consumed|clearance",
                ):
                    await engine._call_with_admission_guard(
                        sent,
                        admission_guard=guard,
                        authority_gate=runtime._authority_gate(),
                    )
                assert len(sends) == 1


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED)
async def test_reentrant_recovery_revocation_burns_clearance_before_any_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            async for enrollment, source, context, lease, semantic, sent in _recovery_material(
                runtime
            ):
                session = enrollment._open_recovery_session(
                    source=source, context=context, lease=lease
                )
                clearance = enrollment._reserve_recovery_clearance(
                    session=session, stage="COMPLETION", semantic_input=semantic, sent=sent
                )
                guard = enrollment._recovery_admission_guard(clearance)
                sends: list[bytes] = []
                original_current = source._require_current
                original_send = r7_runtime._send_frame

                def revoke_during_recheck(
                    value: object,
                    _enrollment: Any = enrollment,
                    _session: Any = session,
                    _current: Any = original_current,
                ) -> None:
                    _enrollment._revoke_recovery_session(_session)
                    _current(value)

                def record_send(
                    *args: object,
                    _sends: list[bytes] = sends,
                    _send: Any = original_send,
                    **kwargs: object,
                ) -> None:
                    _sends.append(cast(bytes, args[2]))
                    _send(*args, **kwargs)

                monkeypatch.setattr(source, "_require_current", revoke_during_recheck)
                monkeypatch.setattr(r7_runtime, "_send_frame", record_send)
                with pytest.raises(
                    H1LiveCompletionEnrollmentUnavailable,
                    match=r"revoked|current|clearance",
                ):
                    await runtime._supervisor.runtime()._call_with_admission_guard(
                        sent,
                        admission_guard=guard,
                        authority_gate=runtime._authority_gate(),
                    )
                assert sends == []
                with pytest.raises(H1LiveCompletionEnrollmentUnavailable):
                    enrollment._require_admitted_recovery(clearance, session=session, sent=sent)


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED)
async def test_cancellation_drains_sent_broker_worker_before_recovery_lease_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    sent = threading.Event()
    release = threading.Event()
    original_send = r7_runtime._send_frame
    original_receive = r7_runtime._receive_frame

    def pause_after_send(*args: object, **kwargs: object) -> None:
        original_send(*args, **kwargs)
        sent.set()

    def block_receive(*args: object, **kwargs: object) -> object:
        assert release.wait(timeout=2), "test never released broker worker"
        return original_receive(*args, **kwargs)

    monkeypatch.setattr(r7_runtime, "_send_frame", pause_after_send)
    monkeypatch.setattr(r7_runtime, "_receive_frame", block_receive)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request, _initial = await _seal_v3_then_v2_without_recovery(runtime)
            coordinator = runtime._h1_postseal_recovery_coordinator
            assert coordinator is not None
            recovery = asyncio.create_task(
                coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint()
                )
            )
            await asyncio.wait_for(asyncio.to_thread(sent.wait, 2), timeout=3)
            recovery.cancel()
            await asyncio.sleep(0)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(coordinator._fence.acquire(), timeout=0.03)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await recovery
            async with await coordinator._fence.acquire() as lease:
                lease.require_owned()
