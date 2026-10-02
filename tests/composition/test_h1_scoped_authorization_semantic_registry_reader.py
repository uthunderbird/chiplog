"""Focused lifecycle tests for the inert V22 semantic-registry reader."""

from __future__ import annotations

import copy
import hashlib
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_scoped_dispatch_semantics import (
    H1_SCOPED_DISPATCH_V2_SEMANTIC_MANIFEST,
    PREPARE_H1_SCOPED_DELIVERY_V2,
    h1_scoped_dispatch_semantic_manifest_bytes,
    h1_scoped_dispatch_v2_semantic_registry_capture,
)


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


def _producer_only_v2_capture():
    """Make a self-consistent V2 DTO which omits the authorization operation."""
    raw = H1_SCOPED_DISPATCH_V2_SEMANTIC_MANIFEST.model_copy(
        update={"operations": (PREPARE_H1_SCOPED_DELIVERY_V2,)}
    ).canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    head = ExactHead(
        subject_id="chiplog.effects.h1-scoped-dispatch-preparation-manifest",
        head="chiplog.effects.h1-scoped-dispatch-preparation-manifest/" + fingerprint,
        fingerprint=fingerprint,
    )
    return h1_scoped_dispatch_v2_semantic_registry_capture(
        clock_contract="chiplog.dispatch.monotonic.v2", clock_epoch="epoch", valid_until_ns=50
    ).model_copy(
        update={
            "source_version": "v2",
            "canonical_value": raw,
            "head": head,
            "invalidation_manifest": head,
        }
    )


def _installed(monkeypatch: pytest.MonkeyPatch):
    import chiplog.composition.h1_scoped_authorization_semantic_registry_reader as subject

    runtime = _Runtime()
    joined_capture = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)
    original = h1_scoped_dispatch_v2_semantic_registry_capture(
        clock_contract="chiplog.dispatch.monotonic.v2", clock_epoch="epoch", valid_until_ns=75
    )
    request = SimpleNamespace(
        intent=SimpleNamespace(
            acquisition=SimpleNamespace(original_sources=SimpleNamespace(semantic_registry=original))
        )
    )
    issuance = SimpleNamespace(effects_call=SimpleNamespace(request=request))
    claim = SimpleNamespace(
        binding=SimpleNamespace(claimant_clock=SimpleNamespace(lease_expires_at_ns=50))
    )
    evidence = SimpleNamespace(current_claim=claim)
    effects = SimpleNamespace(
        selected=SimpleNamespace(selected_h1=SimpleNamespace(issuance=issuance))
    )
    effects_reader = SimpleNamespace(_issued_record_held=lambda capture: effects)

    class _Joined:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self._effects = effects_reader
            self.changed = False

        def recheck_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            assert capture is joined_capture
            if self.changed:
                raise subject.H1ScopedAuthorizationDeliveryWorkHold("changed")
            return capture

        def _issued_record_held(self, capture: object) -> object:
            assert capture is joined_capture
            return SimpleNamespace(effects_capture="effects", evidence=evidence)

    class _Clock:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.epoch = "epoch"
            self.released: list[object] = []

        def read(self, capture: object) -> object:
            assert capture is joined_capture
            return object()

        def role_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return SimpleNamespace(
                clock_contract="chiplog.dispatch.monotonic.v2",
                clock_epoch=self.epoch,
                valid_until_ns=40,
            )

        def release(self, capture: object) -> None:
            self.released.append(capture)

    joined, clock = _Joined(runtime), _Clock(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    runtime._h1_scoped_authorization_clock_reader_v1 = clock
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _Joined)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationClockReaderV1", _Clock)
    monkeypatch.setattr(subject, "H1CompletionIssuanceV4", type(issuance))
    return subject, runtime, joined_capture, joined, clock, request, claim


def test_registry_reader_returns_exact_original_and_g1_lease_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, _, request, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationSemanticRegistryReaderV1(runtime)
    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        role = reader.role_held(capture)
    assert role.original is request.intent.acquisition.original_sources.semantic_registry
    assert role.current == h1_scoped_dispatch_v2_semantic_registry_capture(
        clock_contract="chiplog.dispatch.monotonic.v2", clock_epoch="epoch", valid_until_ns=50
    )
    assert role.current.valid_until_ns != 40


@pytest.mark.parametrize(
    "replacement", [_producer_only_v2_capture(), h1_scoped_dispatch_semantic_manifest_bytes()]
)
def test_registry_reader_rejects_self_consistent_altered_original_or_v1(
    monkeypatch: pytest.MonkeyPatch, replacement: object
) -> None:
    subject, runtime, joined_capture, _, _, request, _ = _installed(monkeypatch)
    if isinstance(replacement, bytes):
        request.intent.acquisition.original_sources.semantic_registry = (
            h1_scoped_dispatch_v2_semantic_registry_capture(
                clock_contract="chiplog.dispatch.monotonic.v2",
                clock_epoch="epoch",
                valid_until_ns=75,
            ).model_copy(update={"source_version": "v1", "canonical_value": replacement})
        )
    else:
        request.intent.acquisition.original_sources.semantic_registry = replacement
    reader = subject.H1ScopedAuthorizationSemanticRegistryReaderV1(runtime)
    with pytest.raises(subject.H1ScopedAuthorizationSemanticRegistryHold, match="unavailable"):
        reader.read(joined_capture)


def test_registry_reader_rejects_changed_epoch_lease_joined_or_tampered_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, joined, clock, _, claim = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationSemanticRegistryReaderV1(runtime)
    capture = reader.read(joined_capture)
    clock.epoch = "other"
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSemanticRegistryHold, match="unavailable"
    ):
        reader.recheck_held(capture)
    clock.epoch = "epoch"
    claim.binding.claimant_clock.lease_expires_at_ns = 51
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSemanticRegistryHold, match="sources changed"
    ):
        reader.recheck_held(capture)
    claim.binding.claimant_clock.lease_expires_at_ns = 50
    joined.changed = True
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSemanticRegistryHold, match="unavailable"
    ):
        reader.recheck_held(capture)
    joined.changed = False
    record = capture._record
    object.__setattr__(
        capture,
        "_record",
        replace(record, role=replace(record.role, current=_producer_only_v2_capture())),
    )
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSemanticRegistryHold, match="not reader-issued"
    ):
        reader.role_held(capture)


def test_registry_reader_rejects_forgery_and_releases_clock_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock, _, _ = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationSemanticRegistryReaderV1(runtime)
    capture = reader.read(joined_capture)
    forged = object.__new__(subject.H1ScopedAuthorizationSemanticRegistryCaptureV1)
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSemanticRegistryHold, match="not reader-issued"
    ):
        reader.role_held(forged)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(capture)
    reader.release(capture)
    assert len(clock.released) == 1
    second = reader.read(joined_capture)
    reader.close()
    assert len(clock.released) == 2
    with runtime._authority_gate().hold(), pytest.raises(
        subject.H1ScopedAuthorizationSemanticRegistryHold, match="reader is closed"
    ):
        reader.role_held(second)
