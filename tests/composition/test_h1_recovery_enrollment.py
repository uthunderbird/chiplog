"""RED witnesses for B-only H1 recovery enrollment and broker admission."""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import AsyncGenerator
from contextlib import nullcontext
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


def test_terminal_recovery_clearance_uses_p_only_for_finalization() -> None:
    """Ordinary terminal recovery admits without P; finalization burns absent/foreign P."""
    from chiplog.composition.h1_live_completion_enrollment import (
        _H1LiveCompletionEnrollment,
        _RecoveryClearanceRecord,
        _RecoveryEnrollmentRecord,
    )

    class Gate:
        def hold(self) -> Any:
            return nullcontext()

    class Lease:
        def _require_admission_current(self) -> None:
            return None

    class Source:
        def _require_current(self, context: object) -> None:
            assert context is expected_context

    class ForeignP:
        def _check_terminal_clearance_current(self, clearance: object, sent: object) -> None:
            assert clearance is expected_clearance
            assert sent is expected_sent
            raise ValueError("foreign P clearance")

    expected_context = object()
    expected_clearance = object()
    expected_sent = object()
    source = Source()
    lease = Lease()
    records: list[_RecoveryClearanceRecord] = []

    def consume(*, preflight: object | None, scope_port: object) -> _RecoveryClearanceRecord:
        owner = cast(Any, object.__new__(_H1LiveCompletionEnrollment))
        owner._gate = Gate()
        owner._scope_port = scope_port
        recovery = _RecoveryEnrollmentRecord(
            session=object(),
            source=source,
            context=expected_context,
            lease=lease,
            preflight=preflight,
        )
        record = _RecoveryClearanceRecord(
            enrollment=recovery,
            stage="TERMINAL_WORK",
            semantic_input=b"terminal",
            semantic_digest="digest",
            sent=expected_sent,
            call_fingerprint="fingerprint",
            owner_frame_bytes=b"frame",
        )
        records.append(record)
        owner._require_recovery_clearance = lambda clearance: (
            record if clearance is expected_clearance else pytest.fail("foreign clearance")
        )
        owner._recovery_call_identity = lambda sent: (
            ("fingerprint", b"frame")
            if sent is expected_sent
            else pytest.fail("foreign sent frame")
        )
        owner._consume_recovery_clearance(
            expected_clearance, sent=expected_sent, owner_frame_bytes=b"frame"
        )
        return record

    ordinary = consume(preflight=None, scope_port=object())
    assert ordinary.state == "ADMITTED"

    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="source or lease"):
        consume(preflight=object(), scope_port=object())
    assert records[-1].state == "BURNED"

    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="source or lease"):
        consume(preflight=object(), scope_port=ForeignP())
    assert records[-1].state == "BURNED"


def test_recovery_revocation_retains_active_enrollment_when_p_cleanup_fails() -> None:
    """A failed P retirement cannot reopen B's single-session guard."""
    from chiplog.composition.h1_live_completion_enrollment import (
        _H1LiveCompletionEnrollment,
        _RecoveryClearanceRecord,
        _RecoveryEnrollmentRecord,
    )

    class Gate:
        def hold(self) -> Any:
            return nullcontext()

    class FailingP:
        def _revoke_recovery_finalization(self, session: object) -> None:
            assert session is expected_session
            raise RuntimeError("P retirement failed")

    expected_session = object()
    record = _RecoveryEnrollmentRecord(
        session=cast(Any, expected_session), source=object(), context=object(), lease=object()
    )
    clearance = _RecoveryClearanceRecord(
        enrollment=record,
        stage="COMPLETION",
        semantic_input=b"completion",
        semantic_digest="digest",
        sent=cast(Any, object()),
        call_fingerprint="fingerprint",
        owner_frame_bytes=b"frame",
    )
    owner = cast(Any, object.__new__(_H1LiveCompletionEnrollment))
    owner._gate = Gate()
    owner._scope_port = FailingP()
    owner._recovery_clearances = {id(clearance): clearance}
    owner._recovery_records = {id(expected_session): record}
    owner._require_recovery_record = lambda session: (
        record if session is expected_session else pytest.fail("foreign session")
    )

    with pytest.raises(RuntimeError, match="P retirement failed"):
        owner._revoke_recovery_session(expected_session)
    assert record.state == "ACTIVE"
    assert clearance.state == "RESERVED"
    owner._require_recovery_source_context_lease = lambda **_kwargs: None
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="already registered"):
        owner._open_recovery_session(source=object(), context=object(), lease=object())


@pytest.mark.asyncio
async def test_failed_shared_drain_keeps_recovery_enrollment_active_for_both_cleanup_paths(
) -> None:
    """Ordinary and finalization cleanup share a drain seam that never revokes on failure."""
    from chiplog.composition.h1_live_completion_enrollment import (
        _H1LiveCompletionEnrollment,
        _RecoveryEnrollmentRecord,
    )
    from chiplog.composition.h1_postseal_recovery_coordinator import (
        _H1PostSealRecoveryCoordinator,
    )

    class Lease:
        checked = False

        def require_owned(self) -> None:
            self.checked = True

    class FailedDrain:
        async def _drain_recovery(self) -> None:
            raise RuntimeError("broker drain failed")

    session = FailedDrain()
    record = _RecoveryEnrollmentRecord(
        session=cast(Any, session), source=object(), context=object(), lease=object()
    )
    enrollment = cast(Any, object.__new__(_H1LiveCompletionEnrollment))
    enrollment._gate = type("Gate", (), {"hold": lambda self: nullcontext()})()
    enrollment._recovery_records = {id(session): record}
    enrollment._require_recovery_source_context_lease = lambda **_kwargs: None
    runtime = type("Runtime", (), {"_h1_live_completion_enrollment": enrollment})()
    coordinator = cast(Any, object.__new__(_H1PostSealRecoveryCoordinator))
    coordinator._runtime = runtime
    lease = Lease()

    with pytest.raises(RuntimeError, match="broker drain failed"):
        await coordinator._drain_session_held(lease, session)
    assert lease.checked is False
    assert record.state == "ACTIVE"
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="already registered"):
        enrollment._open_recovery_session(source=object(), context=object(), lease=object())


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
            enrollment = runtime._h1_live_completion_enrollment
            assert coordinator is not None
            assert enrollment is not None
            recovery = asyncio.create_task(
                coordinator.resume_selected(
                    request.identity, request.original_driver_command_fingerprint()
                )
            )
            await asyncio.wait_for(asyncio.to_thread(sent.wait, 2), timeout=3)
            recovery.cancel()
            await asyncio.sleep(0)
            assert tuple(record.state for record in enrollment._recovery_records.values()) == (
                "ACTIVE",
            )
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(coordinator._fence.acquire(), timeout=0.03)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await recovery
            assert tuple(record.state for record in enrollment._recovery_records.values()) == (
                "REVOKED",
            )
            async with await coordinator._fence.acquire() as lease:
                lease.require_owned()
