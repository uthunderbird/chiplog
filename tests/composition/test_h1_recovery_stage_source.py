"""RED contracts for the historical H1 COMPLETION-stage source boundary."""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

import chiplog.composition.h1_recovery_stage_source as stage_module
from chiplog.composition.h1_recovery_stage_source import (
    H1RecoveryStageSource,
    H1RecoveryStageSourceError,
)


def test_require_current_holds_one_cut_and_reconstructs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native, one-shot P/E rebuild, and retained P replay share one gate hold."""
    events: list[str] = []

    class Gate:
        @contextlib.contextmanager
        def hold(self) -> Iterator[None]:
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

    native = dataclasses.make_dataclass(
        "Native", ["original_identity", "original_fingerprint", "selected_seal"]
    )(object(), "f" * 64, object())
    context = object.__new__(stage_module._IssuedRecoveryStages)
    source = object.__new__(H1RecoveryStageSource)
    port = SimpleNamespace(
        _replay_historical_recovery_sources=lambda cap: events.append("p-replay") or cap
    )
    source._runtime = SimpleNamespace(
        _authority_gate=lambda: Gate(), _h1_preissuance_registration_source_port=port
    )
    source._recovery_contexts = {
        id(context): stage_module._RecoveryStageState(
            context=context,
            root=object(),
            native=native,
            completion_input=b"expected",
            p_source_cap="cap",
        )
    }
    monkeypatch.setattr(source, "replay_completion_native", lambda _: events.append("native"))
    calls: list[object] = []

    class Reader:
        def __init__(self, _runtime: object) -> None:
            pass

        def _reconstruct_current_completion_bytes(self, **_kwargs: object) -> bytes:
            calls.append("reconstruct")
            events.append("reconstruct")
            return b"expected"

    monkeypatch.setattr(stage_module, "H1RecoveryHistoricalPESource", Reader)
    source._require_current(context)

    assert events == ["enter", "native", "reconstruct", "p-replay", "exit"]
    assert calls == ["reconstruct"]

    class ChangedReader(Reader):
        def _reconstruct_current_completion_bytes(self, **_kwargs: object) -> bytes:
            events.append("reconstruct-changed")
            return b"changed"

    monkeypatch.setattr(stage_module, "H1RecoveryHistoricalPESource", ChangedReader)
    events.clear()
    with pytest.raises(H1RecoveryStageSourceError, match="completion input differs"):
        source._require_current(context)
    assert events == ["enter", "native", "reconstruct-changed", "exit"]

    monkeypatch.setattr(stage_module, "H1RecoveryHistoricalPESource", Reader)
    events.clear()

    def changed_capability(_cap: object) -> None:
        events.append("p-replay-changed")
        raise ValueError("changed retained capability")

    port._replay_historical_recovery_sources = changed_capability
    with pytest.raises(H1RecoveryStageSourceError, match="historical P source differs"):
        source._require_current(context)
    assert events == ["enter", "native", "reconstruct", "p-replay-changed", "exit"]
