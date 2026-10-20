"""Mounted RED contract for historical P/E COMPLETION reconstruction.

The test deliberately seals the selected V2 prefix without retaining any
caller-shaped delivery DTO.  A future installed reader must select its own
authenticated P/E closure, and must fail closed when that closure is absent.
"""

from __future__ import annotations

import importlib

import pytest


def test_current_historical_reader_reconstructs_once_and_rejects_noncanonical_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Currentness uses one source-owned rebuild, never a synthetic receipt."""
    source_module = importlib.import_module("chiplog.composition.h1_recovery_historical_pe_source")
    source_type = source_module.H1RecoveryHistoricalPESource
    reader = object.__new__(source_type)
    calls: list[object] = []

    class Request:
        def canonical_bytes(self) -> bytes:
            return b"canonical"

    monkeypatch.setattr(source_type, "_validate_inputs", staticmethod(lambda *_: None))
    monkeypatch.setattr(
        source_type,
        "_reconstruct",
        lambda _self, *_: calls.append("reconstruct") or Request(),
    )
    monkeypatch.setattr(source_module, "decode_first_path_completion_request", lambda _: Request())

    assert (
        reader._reconstruct_current_completion_bytes(
            original_identity=object(), original_fingerprint="f", selected_seal=object()
        )
        == b"canonical"
    )
    assert calls == ["reconstruct"]

    class Noncanonical(Request):
        def canonical_bytes(self) -> bytes:
            return b"different"

    monkeypatch.setattr(
        source_module, "decode_first_path_completion_request", lambda _: Noncanonical()
    )
    with pytest.raises(source_module.H1RecoveryHistoricalPESourceIntegrityError):
        reader._reconstruct_current_completion_bytes(
            original_identity=object(), original_fingerprint="f", selected_seal=object()
        )


__all__ = []
