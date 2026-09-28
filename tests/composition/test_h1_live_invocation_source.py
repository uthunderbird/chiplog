"""The absent H1 broker proof owner cannot be replaced with caller evidence."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast

import pytest


@pytest.mark.asyncio
async def test_uninstalled_invocation_source_rejects_completion_and_dto_shaped_inputs() -> None:
    """No session, identity, principal, or trust claim can mint a live invocation."""
    from chiplog.composition.h1_live_invocation_source import (
        H1LiveInvocationSource,
        H1LiveInvocationSourceUnavailable,
    )

    # The positive constructor accepts only a canonical installed runtime.  A
    # source object with a non-mounted runtime is useful here solely to prove
    # that the frozen async ingress still rejects before using caller inputs.
    source = object.__new__(H1LiveInvocationSource)
    runtime = SimpleNamespace(_h1_live_invocation_source=source)
    source._runtime = cast(Any, runtime)
    source._revoked = False
    source._issued_count = 0
    forged = {
        "session": object(),
        "identity": {"tenant_id": "forged", "command_id": "forged"},
        "principal_bytes": b'{"principal_id":"forged"}',
        "observed_trust": {"phase": "ACTIVE"},
    }

    with pytest.raises(H1LiveInvocationSourceUnavailable, match="not installed"):
        await source.capture_invocation(session=forged["session"], identity=forged["identity"])

    assert source.issued_count == 0


def test_invocation_source_names_required_broker_trust_owner_proof() -> None:
    """The extension must authenticate a completed B call, not trust a DTO."""
    from chiplog.composition.h1_live_invocation_source import (
        H1InvocationOwnerExtension,
        H1LiveInvocationSource,
    )

    source = object.__new__(H1LiveInvocationSource)
    extension = source.required_owner_extension

    assert isinstance(extension, H1InvocationOwnerExtension)
    assert extension.operation == "agent_loop.complete_acceptance.v2"
    assert extension.reason == "no installed H1 completion broker/trust proof owner"
    assert extension.required_observations == (
        "exact enrolled B session and completion exchange",
        "canonical authenticated principal bytes",
        "immutable observed trust evidence",
        "broker epoch/session/runtime generation",
    )
    assert extension.requires_shared_gate_currentness is True
    assert extension.requires_one_use_unpredictable_issuer is True


def test_materialization_burns_retained_capture_when_currentness_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale materialization cannot become a retryable private capture."""
    from chiplog.composition.h1_live_completion_enrollment import (
        H1LiveCompletionEnrollmentUnavailable,
        _H1LiveCompletionEnrollment,
    )
    from chiplog.composition.h1_live_invocation_source import (
        H1LiveInvocationSource,
        H1LiveInvocationSourceUnavailable,
        _H1InvocationCapture,
    )

    source = object.__new__(H1LiveInvocationSource)
    session = object()
    capture = _H1InvocationCapture(
        session=session,
        recovery=object(),
        identity=object(),
        observed=cast(Any, object()),  # Rejection occurs before observation use.
        principal_bytes=b"principal",
        issued_at_ns=0,
        issuance_id="h1-invocation:test",
    )
    enrollment = object.__new__(_H1LiveCompletionEnrollment)
    attempts = 0

    def reject_stale_recovery(candidate: object) -> object:
        nonlocal attempts
        attempts += 1
        assert candidate is session
        raise H1LiveCompletionEnrollmentUnavailable("stale recovery")

    monkeypatch.setattr(enrollment, "_require_recovery_record", reject_stale_recovery)
    runtime = SimpleNamespace(
        _h1_live_invocation_source=source,
        _h1_live_completion_enrollment=enrollment,
        _authority_gate=lambda: SimpleNamespace(hold=lambda: nullcontext()),
    )
    source._runtime = cast(Any, runtime)
    source._revoked = False
    source._captures = {id(capture): capture}

    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="stale recovery"):
        source._materialize_capture(
            capture=capture,
            session=session,
            identity=cast(Any, object()),
            expected=cast(Any, object()),
            replay_exchanges=cast(Any, ()),
        )
    with pytest.raises(H1LiveInvocationSourceUnavailable, match="capture is unavailable"):
        source._materialize_capture(
            capture=capture,
            session=session,
            identity=cast(Any, object()),
            expected=cast(Any, object()),
            replay_exchanges=cast(Any, ()),
        )

    assert attempts == 1
    assert source._captures == {}
