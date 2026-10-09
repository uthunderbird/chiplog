"""Selected closed-outcome acquisition avoids a duplicate R14 replay."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
)
from chiplog.composition import r14_native_selected_accounting_source as accounting_module
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
)


def test_closed_outcome_collects_once_under_gate_after_capturing_baseline(monkeypatch, tmp_path):
    events: list[str] = []

    class Gate:
        held = False

        @contextmanager
        def hold(self):
            assert not self.held
            self.held = True
            try:
                yield
            finally:
                self.held = False

    gate = Gate()
    journal = object()
    owner_decisions = SimpleNamespace(snapshot=lambda: journal)

    class Runtime:
        _tenant_id = "tenant"
        _database = tmp_path / "runtime.sqlite"
        _database_identity = ("database", 1, 2)
        _commitment_journal = SimpleNamespace(load=lambda _tenant: "commitment")

        def _authority_gate(self):
            return gate

        def _check_database_identity(self):
            events.append("identity")

        def _owner_decisions(self):
            return owner_decisions

    branch = ConsequentialAcceptedCall(
        initialized=Present(head="initialized", fingerprint="1" * 64),
        accepted=Present(head="accepted", fingerprint="2" * 64),
        execution_intent=Present(head="execution", fingerprint="3" * 64),
        external_effect_intent=Present(head="effect", fingerprint="4" * 64),
        complete_acceptance_manifest=(
            Present(head="accepted", fingerprint="2" * 64),
            Present(head="execution", fingerprint="3" * 64),
        ),
    )
    accepted = SimpleNamespace(
        source=SimpleNamespace(target_inventory_rows=(SimpleNamespace(acceptance=branch),))
    )
    source = object.__new__(R14NativeSelectedAccountingSource)
    source._runtime = Runtime()
    source._original_prepare = SimpleNamespace()
    source._preparation_bindings = SimpleNamespace()

    def collect_one_accepted(*_args, **_kwargs):
        assert gate.held
        events.append("collect-one")
        return accepted

    def duplicate_collect(*_args, **_kwargs):
        events.append("duplicate-collect")
        return accepted.source

    source.collect_one_accepted = collect_one_accepted
    source.collect = duplicate_collect

    @contextmanager
    def connection(_database):
        yield object()

    monkeypatch.setattr(accounting_module, "read_connection", connection)
    monkeypatch.setattr(
        accounting_module,
        "capture_authority_snapshot_commitment",
        lambda *_args: events.append("baseline") or "commitment",
    )

    from chiplog.composition import r16_effects

    def stop_after_selected_source(*_args, **_kwargs):
        raise RuntimeError("stop after source collection")

    monkeypatch.setattr(
        r16_effects, "_read_materialized_effects_with_history", stop_after_selected_source
    )

    result = source._collect_one_accepted_outcome(current_obligation_state="CLOSED")

    assert result is None
    assert events.index("baseline") < events.index("collect-one")
    assert "duplicate-collect" not in events
    assert events.count("collect-one") == 1
