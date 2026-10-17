"""Lifecycle guards for the inert V4 R16 resource capture."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest


class _Gate:
    held = False

    @contextmanager
    def hold(self):
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def require_held(self) -> None:
        if not self.held:
            raise RuntimeError("gate is not held")


class _Runtime:
    def __init__(self) -> None:
        self._gate = _Gate()

    def _authority_gate(self) -> _Gate:
        return self._gate


def test_v4_resource_capture_rejects_forged_foreign_released_and_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_resource_sources_v4 as subject

    class _Joined:
        def __init__(self, runtime: object) -> None:
            self.runtime = runtime
            self._runtime = runtime

    runtime = _Runtime()
    joined = _Joined(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _Joined)
    foreign_runtime = _Runtime()
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = _Joined(foreign_runtime)
    with pytest.raises(TypeError, match="requires joined work reader"):
        subject.H1ScopedAuthorizationResourceReaderV4(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    joined_capture = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)
    record = subject._ResourceRecordV4(
        delivery_work_capture=joined_capture,
        observation=SimpleNamespace(),
        roles=SimpleNamespace(marker="roles"),
        selected=SimpleNamespace(),
    )
    monkeypatch.setattr(
        subject.H1ScopedAuthorizationResourceReaderV4,
        "_read_record_held",
        lambda self, capture: record,
    )

    reader = subject.H1ScopedAuthorizationResourceReaderV4(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        assert reader.roles_held(capture).marker == "roles"

    forged = object.__new__(subject.H1ScopedAuthorizationResourceCaptureV4)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationResourceHold, match="not reader-issued"),
    ):
        reader.roles_held(forged)

    other = subject.H1ScopedAuthorizationResourceReaderV4(runtime)
    foreign = other.read(joined_capture)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationResourceHold, match="not reader-issued"),
    ):
        reader.roles_held(foreign)

    reader.release(capture)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationResourceHold, match="not reader-issued"),
    ):
        reader.roles_held(capture)

    other.close()
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationResourceHold, match="reader is closed"),
    ):
        other.roles_held(foreign)

    reopened = subject.H1ScopedAuthorizationResourceReaderV4(runtime)
    reopened_capture = reopened.read(joined_capture)
    with runtime._authority_gate().hold():
        assert reopened.roles_held(reopened_capture).marker == "roles"
