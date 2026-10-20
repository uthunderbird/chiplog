"""RED witnesses for B-only H1 recovery enrollment and broker admission."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, cast

import pytest

from chiplog.composition.h1_live_completion_enrollment import H1LiveCompletionEnrollmentUnavailable

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
async def test_failed_shared_drain_keeps_recovery_enrollment_active_for_both_cleanup_paths() -> (
    None
):
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
