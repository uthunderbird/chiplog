from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition import r14_accepted_direct_terminal_publication as publication
from chiplog.composition import r14_loop_history as loop_history


def _two_seal_history(*accepted_ids):
    executions = tuple(
        SimpleNamespace(
            proposal=SimpleNamespace(
                fan_out=SimpleNamespace(
                    initialized_records=(SimpleNamespace(original_call_id=call_id),),
                    response_seal=SimpleNamespace(
                        response_seal_id=seal_id, digest=lambda digest=digest: digest
                    ),
                )
            )
        )
        for call_id, seal_id, digest in (
            ("call-a", "seal-a", "a" * 64),
            ("call-b", "seal-b", "b" * 64),
        )
    )
    acceptances = tuple(
        SimpleNamespace(
            loop_request=SimpleNamespace(binding=SimpleNamespace(original_call_id=call_id))
        )
        for call_id in accepted_ids
    )
    return (SimpleNamespace(records=(object(),)), None, None, executions, (), acceptances)


def test_direct_terminal_selects_the_requested_seal_among_two():
    history = _two_seal_history("call-b")
    assert publication._native_response_for_call(history, "call-a").subject_id == "seal-a"
    assert publication._native_response_for_call(history, "call-b").subject_id == "seal-b"


def test_direct_terminal_rejects_missing_or_ambiguous_target_seal():
    history = _two_seal_history("call-b")
    with pytest.raises(LoopRejected, match="for call"):
        publication._native_response_for_call(history, "absent")
    duplicated = (*history[:3], history[3] + (history[3][1],), *history[4:])
    with pytest.raises(LoopRejected, match="for call"):
        publication._native_response_for_call(duplicated, "call-b")


def test_direct_terminal_rejects_projection_for_another_seal():
    history = _two_seal_history("call-b")
    wrong = SimpleNamespace(
        selected_response_seal=publication._native_response_for_call(history, "call-a")
    )
    with pytest.raises(LoopRejected, match="Prepare source unavailable"):
        publication._current_cut(
            object(),
            original_call_id="call-b",
            authenticated_history=history,
            authenticated_projection=wrong,
        )


def test_direct_terminal_keeps_unqualified_acceptance_ambiguous():
    assert (
        publication._current_cut(
            object(), authenticated_history=_two_seal_history("call-a", "call-b")
        )
        is None
    )


def test_direct_terminal_rejects_a_different_explicit_historical_target():
    with pytest.raises(LoopRejected, match="historical target differs"):
        publication._current_cut(
            object(),
            original_call_id="call-a",
            authenticated_history=_two_seal_history("call-b"),
            expected_source_cut=SimpleNamespace(original_call_id="call-b"),
        )


def test_selected_direct_terminal_reuses_strict_history_inside_authority_gate(
    monkeypatch,
) -> None:
    gate_state = {"held": False}
    passed = {}
    history = (None, None, None, object(), None, None, None)

    @contextmanager
    def hold():
        assert not gate_state["held"]
        gate_state["held"] = True
        try:
            yield
        finally:
            gate_state["held"] = False

    runtime = SimpleNamespace(
        _authority_gate=lambda: SimpleNamespace(hold=hold),
        _loop_decisions=lambda: SimpleNamespace(entries=lambda: ()),
    )

    def read_history(actual_runtime, **kwargs):
        assert actual_runtime is runtime
        assert gate_state["held"]
        assert kwargs == {}
        return history

    def validate(actual_runtime, **kwargs):
        assert actual_runtime is runtime
        assert gate_state["held"]
        passed.update(kwargs)
        return None

    monkeypatch.setattr(loop_history, "_read_call_history", read_history)
    monkeypatch.setattr(publication, "validate_selected_accepted_direct_terminal", validate)

    result = loop_history.read_selected_accepted_direct_terminal(runtime, original_call_id="call-a")

    assert result is None
    assert passed == {
        "original_call_id": "call-a",
        "authenticated_effects_history": history[3],
    }
    assert not gate_state["held"]
