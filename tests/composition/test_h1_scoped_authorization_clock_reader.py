"""Temporal and lifetime guards for the inert V22 clock role."""

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
    def __init__(self, readings: list[tuple[str, int]]) -> None:
        self._gate = _Gate()
        self.readings = readings

    def _authority_gate(self) -> _Gate:
        return self._gate

    def _require_dispatch_resources(self):
        return self

    def clock(self) -> tuple[str, int]:
        return self.readings.pop(0)


def _installed(monkeypatch: pytest.MonkeyPatch, readings: list[tuple[str, int]]):
    import chiplog.composition.h1_scoped_authorization_clock_reader as subject

    runtime = _Runtime(readings)
    horizon = SimpleNamespace(
        clock_contract="chiplog.dispatch.monotonic.v2",
        clock_epoch="epoch",
        not_before_ns=10,
        expires_at_ns=30,
    )
    claim = SimpleNamespace(
        binding=SimpleNamespace(
            claimant_clock=SimpleNamespace(clock_epoch="epoch", lease_expires_at_ns=25)
        )
    )
    evidence = SimpleNamespace(source=SimpleNamespace(intent_id="intent"), current_claim=claim)
    effects = SimpleNamespace(
        observed=SimpleNamespace(original_intent=SimpleNamespace(mandate=SimpleNamespace(horizon=horizon)))
    )
    effects_reader = SimpleNamespace(_issued_record_held=lambda capture: effects)

    class _Joined:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self._effects = effects_reader

        def evidence_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return evidence

        def _issued_record_held(self, capture: object) -> object:
            return SimpleNamespace(effects_capture="effects", evidence=evidence)

    class _Resources:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.released: list[object] = []
            self.issued: dict[object, object] = {}

        def read(self, capture: object) -> object:
            issued = object.__new__(subject.H1ScopedAuthorizationResourceCaptureV4)
            self.issued[issued] = capture
            return issued

        def recheck_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return capture

        def roles_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return SimpleNamespace(clock_epoch="epoch")

        def _issued_record_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return SimpleNamespace(
                delivery_work_capture=self.issued[capture],
                roles=SimpleNamespace(clock_epoch="epoch"),
            )

        def release(self, capture: object) -> None:
            self.released.append(capture)

    joined, resources = _Joined(runtime), _Resources(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    runtime._h1_scoped_authorization_resource_reader_v4 = resources
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _Joined)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationResourceReaderV4", _Resources)
    capture = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)
    return subject, runtime, resources, horizon, claim, capture


def test_clock_role_returns_fresh_final_reading_and_releases_dependent_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, resources, _, _, joined_capture = _installed(
        monkeypatch, [("epoch", 10), ("epoch", 10), ("epoch", 11), ("epoch", 12)]
    )
    reader = subject.H1ScopedAuthorizationClockReaderV1(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        role = reader.role_held(capture)
    assert (role.observed_at_ns, role.valid_until_ns) == (12, 25)
    reader.release(capture)
    assert len(resources.released) == 1
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationClockHold, match="not reader-issued"
    ):
        reader.role_held(capture)


def test_clock_role_uses_mandate_horizon_when_it_precedes_claim_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, _, horizon, _, joined_capture = _installed(
        monkeypatch, [("epoch", 10), ("epoch", 10), ("epoch", 11), ("epoch", 11)]
    )
    horizon.expires_at_ns = 20
    reader = subject.H1ScopedAuthorizationClockReaderV1(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        assert reader.role_held(capture).valid_until_ns == 20


@pytest.mark.parametrize(
    ("readings", "change"),
    [
        ([("epoch", 20), ("epoch", 19)], None),
        ([("epoch", 20), ("other", 21)], None),
        ([("epoch", 20), ("epoch", 30)], None),
        ([("epoch", 20), ("epoch", 25)], None),
        ([("epoch", 20), ("epoch", 21)], "contract"),
    ],
)
def test_clock_role_rejects_rollback_epoch_expiry_and_foreign_contract(
    monkeypatch: pytest.MonkeyPatch,
    readings: list[tuple[str, int]],
    change: str | None,
) -> None:
    subject, runtime, resources, horizon, _, joined_capture = _installed(monkeypatch, readings)
    if change == "contract":
        horizon.clock_contract = "foreign.clock.v1"
    reader = subject.H1ScopedAuthorizationClockReaderV1(runtime)
    with pytest.raises(subject.H1ScopedAuthorizationClockHold, match="horizon, epoch, or lease"):
        reader.read(joined_capture)
    assert len(resources.released) == 1


def test_clock_role_recheck_rejects_global_clock_rollback_and_close_revokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, resources, _, _, joined_capture = _installed(
        monkeypatch,
        [
            ("epoch", 10),
            ("epoch", 10),
            ("epoch", 11),
            ("epoch", 20),
            ("epoch", 19),
            ("epoch", 19),
        ],
    )
    reader = subject.H1ScopedAuthorizationClockReaderV1(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        assert reader.role_held(capture).observed_at_ns == 20
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationClockHold, match="horizon, epoch, or lease"
    ):
        reader.recheck_held(capture)
    reader.close()
    assert len(resources.released) == 1
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationClockHold, match="reader is closed"
    ):
        reader.role_held(capture)


def test_clock_cleanup_revokes_capture_after_dependency_was_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, resources, _, _, joined_capture = _installed(
        monkeypatch,
        [("epoch", 10), ("epoch", 11), ("epoch", 12), ("epoch", 13)],
    )
    reader = subject.H1ScopedAuthorizationClockReaderV1(runtime)
    released = reader.read(joined_capture)
    closed = reader.read(joined_capture)

    def dependent_already_closed(capture: object) -> None:
        raise subject.H1ScopedAuthorizationResourceHold("resource reader is closed")

    monkeypatch.setattr(resources, "release", dependent_already_closed)
    reader.release(released)
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationClockHold, match="not reader-issued"
    ):
        reader.role_held(released)
    reader.close()
    assert reader._issued == {}
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationClockHold, match="reader is closed"
    ):
        reader.role_held(closed)
