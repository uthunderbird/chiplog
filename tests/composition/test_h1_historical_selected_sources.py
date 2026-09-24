"""Closure tests for the H1 historical selected-source boundary."""

from __future__ import annotations

import pytest

from chiplog.composition.h1_historical_selected_sources import (
    _require_historical_ports,
    bind_selected_h1_completion,
    verify_h1_historical_sources,
)


class _NoHistoricalPorts:
    """Looks deliberately unlike an executable/current H1 runtime."""

    class _Gate:
        def hold(self) -> object:
            raise AssertionError("historical verifier must not enter a partial gate")

    def _authority_gate(self) -> _Gate:
        return self._Gate()

    def _require_dispatch_resources(self) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used current dispatch resources")

    def read_admitted_inbox(self, _: str) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used the ingress history facade")

    def _find(self, *_: object) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used the current H1 reader")


def test_verifier_rejects_untyped_batch_before_any_runtime_access() -> None:
    runtime = _NoHistoricalPorts()

    with pytest.raises(TypeError, match="CompleteDeliveryBatchV2"):
        verify_h1_historical_sources(object(), object(), runtime)  # type: ignore[arg-type]


def test_binder_rejects_untyped_batch_before_any_runtime_access() -> None:
    runtime = _NoHistoricalPorts()

    with pytest.raises(TypeError, match="CompleteDeliveryBatchV2"):
        bind_selected_h1_completion(object(), runtime)  # type: ignore[arg-type]


def test_historical_ports_are_not_silently_replaced_by_current_h1_services() -> None:
    """A runtime missing the registered custody/raw seam fails closed."""
    runtime = _NoHistoricalPorts()

    # Deliberately bypass the typed wire boundary: this exercises only the
    # fail-closed runtime capability check and proves no current fallback runs.
    with pytest.raises(ValueError, match="historical custody"):
        _require_historical_ports(runtime)
