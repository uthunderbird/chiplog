"""Focused selected V3 binder closure checks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chiplog.composition import h1_historical_selected_sources as historical
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV3


def test_v3_selected_recovery_rejects_a_changed_durable_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retained conversation pair cannot be substituted after V3 selection."""
    issuance = object.__new__(H1CompletionIssuanceV3)
    conversation_request = object()
    conversation_result = object()
    assembly = SimpleNamespace(
        original_completion_request=object(),
        prepared_completion=object(),
        conversation_request=conversation_request,
        conversation_result=conversation_result,
        terminal_work_request=object(),
        terminal_work_result=object(),
    )
    retained = SimpleNamespace(initialization_envelope_bytes=b'{"inbox_initialization":"x"}')
    object.__setattr__(
        issuance,
        "effects_call",
        SimpleNamespace(request=SimpleNamespace(retained_origin=retained)),
    )
    object.__setattr__(issuance, "assembly", assembly)
    object.__setattr__(
        issuance,
        "recovery",
        SimpleNamespace(
            root_id="root",
            journal_instance_id="journal",
            completed_chain_head="head",
        ),
    )
    object.__setattr__(issuance, "effects_result", object())

    original = SimpleNamespace(
        identity=object(),
        canonical_bytes=lambda: b"driver",
        original_driver_command_fingerprint=lambda: "fingerprint",
    )
    initialization = SimpleNamespace(
        driver_request_bytes=b"driver",
        driver_request_fingerprint="fingerprint",
        canonical_bytes=lambda: b"x",
    )
    monkeypatch.setattr(
        historical.RetainedInboxExecutionInitialization,
        "model_validate_json",
        lambda _: initialization,
    )
    monkeypatch.setattr(historical.DriveInputRequestV1, "model_validate_json", lambda _: original)
    class Locator:
        def __init__(self, _runtime: object) -> None:
            pass

        def locate_selected_seal(self, **_kwargs: object) -> object:
            return object()

    monkeypatch.setattr(historical, "H1V2RecoveryNativeSource", Locator)
    class RootSource:
        def __init__(self, _runtime: object) -> None:
            pass

        def derive_on_restart(self, *_args: object) -> object:
            return SimpleNamespace(root_id=lambda: "root", journal_instance_id="journal")

    monkeypatch.setattr(historical, "H1PostSealRecoveryRootSource", RootSource)
    recovered = SimpleNamespace(
        effects=SimpleNamespace(
            pinned_records=(SimpleNamespace(entry_id="head"),),
            original_completion_request=assembly.original_completion_request,
            prepared_completion=assembly.prepared_completion,
            effects_request=issuance.effects_call.request,
            effects_result=issuance.effects_result,
        ),
        terminal_request=assembly.terminal_work_request,
        terminal_result=assembly.terminal_work_result,
    )
    from chiplog.composition import h1_v3_recovery_historical_source as recovery

    class Reader:
        def __init__(self, _runtime: object) -> None:
            pass

        def read_terminal(self, **_kwargs: object) -> object:
            return recovered

    monkeypatch.setattr(recovery, "H1V3RecoveryHistoricalSource", Reader)
    monkeypatch.setattr(
        historical,
        "_v3_selected_conversation",
        lambda _: (object(), conversation_result),
    )

    with pytest.raises(ValueError) as error:
        historical._verify_v3_selected_recovery(issuance, object())
    assert "selected recovery differs" in str(error.value.__cause__)
