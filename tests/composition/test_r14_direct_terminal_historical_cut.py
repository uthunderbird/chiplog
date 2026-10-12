from __future__ import annotations

from types import SimpleNamespace

from chiplog.composition import r14_accepted_direct_terminal_publication as publication


def test_selected_direct_terminal_rebuild_uses_retained_run_and_sequence(monkeypatch) -> None:
    expected_run = object()
    source_cut = SimpleNamespace(
        current_run=expected_run,
        tenant_commit_sequence=20,
        materialization_commitment="source-commitment",
    )
    seal = SimpleNamespace(
        response_seal_id="response-seal",
        digest=lambda: "a" * 64,
    )
    history = (
        SimpleNamespace(records=(object(),)),
        None,
        None,
        (SimpleNamespace(proposal=SimpleNamespace(fan_out=SimpleNamespace(response_seal=seal))),),
        None,
        None,
    )
    history_calls = []
    monkeypatch.setattr(
        publication,
        "read_execution_recovery_history",
        lambda runtime, **kwargs: history_calls.append(kwargs) or history,
    )

    class PrepareSource:
        def __init__(self, runtime):
            pass

        def collect(self, seal_ref):
            return "authenticated-prepare"

    class AccountingSource:
        def __init__(self, runtime, projection, bindings):
            self.closed_call = None

        def collect_one_accepted_closed_outcome(self, target_run, **kwargs):
            self.closed_call = (target_run, kwargs)
            return "authenticated-closed-outcome"

    source_instances = []

    def accounting_factory(*args):
        source = AccountingSource(*args)
        source_instances.append(source)
        return source

    built = []
    monkeypatch.setattr(publication, "R14OriginalNativePrepareSource", PrepareSource)
    monkeypatch.setattr(publication, "R14NativeSelectedAccountingSource", accounting_factory)
    monkeypatch.setattr(publication, "bind_native_execution_preparation_v2", lambda _: "bindings")
    monkeypatch.setattr(
        publication,
        "build_accepted_direct_terminal_cut",
        lambda runtime, closed, **kwargs: built.append((closed, kwargs)) or "rebuilt-cut",
    )

    result = publication._current_cut(
        object(),
        selected_only=True,
        expected_source_cut=source_cut,
        source_materialization_commitment="source-commitment",
        source_tenant_commit_sequence=20,
    )

    assert result == ("authenticated-closed-outcome", "rebuilt-cut")
    assert history_calls == [{"selected_only": True}]
    assert source_instances[0].closed_call == (
        expected_run,
        {"selected_only": True},
    )
    assert built == [
        (
            "authenticated-closed-outcome",
            {
                "source_run": expected_run,
                "source_materialization_commitment": "source-commitment",
                "source_tenant_commit_sequence": 20,
            },
        )
    ]
