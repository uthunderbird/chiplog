"""Focused lifecycle checks for the inert V22 runtime-and-fence reader."""

from __future__ import annotations

import copy
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


def _installed(monkeypatch: pytest.MonkeyPatch):
    import chiplog.composition.h1_scoped_authorization_runtime_and_fence as subject

    runtime = _Runtime()
    joined_capture = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)
    binding = SimpleNamespace(
        holder_id="holder",
        session_id="session",
        broker_epoch="broker-epoch",
        runtime_generation="generation",
        clock_epoch="clock-epoch",
        lease_expires_at_ns=50,
    )
    g1 = SimpleNamespace(canonical_bytes=lambda: b"exact physical g1")
    claim = SimpleNamespace(
        claim_decision_id="selected-decision",
        claim_decision_fingerprint="f" * 64,
        g1=g1,
        binding=SimpleNamespace(claimant_clock=binding),
    )
    evidence = SimpleNamespace(source=SimpleNamespace(intent_id="intent"), current_claim=claim)

    class _Joined:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.changed = False

        def recheck_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            assert capture is joined_capture
            if self.changed:
                raise subject.H1ScopedAuthorizationDeliveryWorkHold("changed")
            return capture

        def _issued_record_held(self, capture: object) -> object:
            assert capture is joined_capture
            return SimpleNamespace(evidence=evidence)

    class _Clock:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.now = 10
            self.valid_until = 50
            self.released: list[object] = []

        def read(self, capture: object) -> object:
            assert capture is joined_capture
            return object()

        def role_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return SimpleNamespace(
                clock_epoch="clock-epoch",
                observed_at_ns=self.now,
                valid_until_ns=self.valid_until,
            )

        def release(self, capture: object) -> None:
            self.released.append(capture)

    joined, clock = _Joined(runtime), _Clock(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    runtime._h1_scoped_authorization_clock_reader_v1 = clock
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _Joined)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationClockReaderV1", _Clock)
    return subject, runtime, joined_capture, joined, clock, claim, binding


def test_runtime_reader_retains_exact_g1_and_monotonic_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock, _, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        role = reader.role_held(capture)
    assert role.physical_g1_bytes == b"exact physical g1"
    assert (role.selected_decision_id, role.claimant_holder_id, role.broker_epoch) == (
        "selected-decision",
        "holder",
        "broker-epoch",
    )
    clock.now = 11
    with runtime._authority_gate().hold():
        assert reader.role_held(capture).observed_at_ns == 11


def test_runtime_reader_accepts_authenticated_horizon_shorter_than_claim_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock, _, _ = _installed(monkeypatch)
    clock.valid_until = 30
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        assert reader.role_held(capture).lease_expires_at_ns == 50


def test_runtime_reader_rejects_clock_deadline_beyond_claim_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock, _, _ = _installed(monkeypatch)
    clock.valid_until = 51
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    with pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="clock or claim"):
        reader.read(joined_capture)
    assert len(clock.released) == 1


@pytest.mark.parametrize(
    "field",
    [
        "broker_epoch",
        "runtime_generation",
        "session_id",
        "clock_epoch",
        "lease_expires_at_ns",
    ],
)
def test_runtime_reader_rejects_changed_claim_fence(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    subject, runtime, joined_capture, _, _, _, binding = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    capture = reader.read(joined_capture)
    setattr(binding, field, "other" if field != "lease_expires_at_ns" else 51)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationRuntimeAndFenceHold,
            match=r"sources changed|clock or claim",
        ),
    ):
        reader.recheck_held(capture)


def test_runtime_reader_rejects_joined_change_backward_clock_and_forgery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, joined, clock, _, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    capture = reader.read(joined_capture)
    joined.changed = True
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="unavailable"),
    ):
        reader.recheck_held(capture)
    joined.changed = False
    clock.now = 9
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="clock or claim"),
    ):
        reader.recheck_held(capture)
    forged = object.__new__(subject.H1ScopedAuthorizationRuntimeCaptureV1)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="not reader-issued"),
    ):
        reader.role_held(forged)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(capture)


@pytest.mark.parametrize("field", ["claim_decision_id", "claim_decision_fingerprint", "g1"])
def test_runtime_reader_rejects_selected_decision_or_physical_g1_substitution(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    subject, runtime, joined_capture, _, _, claim, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    capture = reader.read(joined_capture)
    setattr(
        claim,
        field,
        SimpleNamespace(canonical_bytes=lambda: b"other physical g1") if field == "g1" else "other",
    )
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="sources changed"),
    ):
        reader.recheck_held(capture)


def test_runtime_reader_releases_only_its_clock_capture_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock, _, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationRuntimeReaderV1(runtime)
    capture = reader.read(joined_capture)
    reader.release(capture)
    assert len(clock.released) == 1
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="not reader-issued"),
    ):
        reader.role_held(capture)
    second = reader.read(joined_capture)
    reader.close()
    assert len(clock.released) == 2
    with (
        runtime._authority_gate().hold(),
        pytest.raises(subject.H1ScopedAuthorizationRuntimeAndFenceHold, match="reader is closed"),
    ):
        reader.role_held(second)
