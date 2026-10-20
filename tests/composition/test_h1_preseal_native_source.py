"""Issuer-owned native facts available before an H1 V2 seal exists."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest


class _OrderedGate:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    @contextmanager
    def hold(self) -> Iterator[None]:
        self._events.append("gate")
        yield


class _OrderedLock:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> None:
        self._events.append("source")

    def __exit__(self, *args: object) -> None:
        return None


def test_preseal_source_always_enters_authority_gate_before_issuer_lock() -> None:
    """P's gate-held replay cannot invert against source lifecycle operations."""
    from chiplog.composition.h1_preseal_native_source import H1PresealNativeSource

    events: list[str] = []
    issuer = object.__new__(H1PresealNativeSource)
    fake_runtime = type("Runtime", (), {"_authority_gate": lambda _self: _OrderedGate(events)})()
    cast(Any, issuer)._runtime = fake_runtime
    cast(Any, issuer)._lock = _OrderedLock(events)
    cast(Any, issuer)._closed = False
    cast(Any, issuer)._issued = {}

    with pytest.raises(ValueError, match="issuer-owned"):
        issuer.replay(cast(Any, object()))
    assert events == ["gate", "source"]

    events.clear()
    issuer.revoke()
    assert events == ["gate", "source"]
