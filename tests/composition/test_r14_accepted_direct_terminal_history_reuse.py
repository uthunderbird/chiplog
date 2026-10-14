from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from chiplog.composition import r14_accepted_direct_terminal_publication as publication
from chiplog.composition import r14_loop_history as loop_history


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
