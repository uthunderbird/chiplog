"""Reuse state controls only; fabricated pairs confer no native-cut authority."""

from types import SimpleNamespace

import pytest

from chiplog.composition import r14_recovery_publication as publication


@pytest.mark.parametrize("change", ("different", "absent", "io-error", "type-error"))
def test_disabled_readiness_witness_cannot_revive_after_input_restoration(monkeypatch, change):
    runtime = object()
    original = (object(), object())
    calls = []
    state = SimpleNamespace(value=original)

    def capture(actual_runtime):
        assert actual_runtime is runtime
        calls.append(actual_runtime)
        if isinstance(state.value, Exception):
            raise state.value
        return state.value

    monkeypatch.setattr(publication, "_capture_readiness_inputs", capture, raising=False)
    witness = publication._ReadinessInputWitness(original)
    assert witness.unchanged(runtime)
    state.value = {
        "different": (original[0], object()),
        "absent": None,
        "io-error": OSError("auxiliary source unavailable"),
        "type-error": TypeError("auxiliary source invalid"),
    }[change]
    assert not witness.unchanged(runtime)
    state.value = original
    assert not witness.unchanged(runtime)
    assert calls == [runtime, runtime]


@pytest.mark.parametrize("failure", (None, OSError, ValueError, TypeError, RuntimeError))
def test_auxiliary_capture_does_not_read_trust_when_journal_inputs_unavailable(
    monkeypatch, failure
):
    from chiplog.composition import r14_call_history_witness as history

    class NoTrust:
        @property
        def _trust(self):
            raise AssertionError("custom/ineligible source must not need trust capture")

    runtime = NoTrust()

    def unavailable(actual):
        assert actual is runtime
        if failure is not None:
            raise failure("journal unavailable")
        return None

    monkeypatch.setattr(history, "capture_call_history_journal_inputs", unavailable)
    assert publication._capture_readiness_inputs(runtime) is None


def test_auxiliary_capture_discards_journals_when_trust_capture_fails(monkeypatch):
    from chiplog.composition import r14_call_history_witness as history

    def unavailable():
        raise ValueError("trust materialization differs")

    runtime = SimpleNamespace(_trust=SimpleNamespace(capture_verified_observation=unavailable))
    monkeypatch.setattr(history, "capture_call_history_journal_inputs", lambda actual: object())
    assert publication._capture_readiness_inputs(runtime) is None
