from __future__ import annotations

from types import SimpleNamespace

from chiplog.composition import r14_accepted_direct_terminal_publication as publication
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeHistoricalClosedOutcomeLocator,
)


def test_selected_direct_terminal_rebuild_uses_retained_run_and_sequence(monkeypatch) -> None:
    expected_run = object()
    source_cut = SimpleNamespace(
        current_run=expected_run,
        tenant_commit_sequence=20,
        materialization_commitment="source-commitment",
        original_call_id="call-a",
        selected_outcome_record_id="closed-a",
        selected_outcome_bytes=b"closed-outcome",
        selected_outcome_fingerprint="a" * 64,
        selected_effect_member_bytes=b"member",
        selected_decision=SimpleNamespace(
            subject_id="decision-a",
            revision=SimpleNamespace(head="head-a", fingerprint="b" * 64),
        ),
        outcome_commit_sequence=18,
        publication_ordinal=2,
        batch_record_ids=("closed-a", "evidence-a"),
    )
    seal = SimpleNamespace(
        response_seal_id="response-seal",
        digest=lambda: "a" * 64,
    )
    history = (
        SimpleNamespace(records=(object(),)),
        None,
        None,
        (
            SimpleNamespace(
                proposal=SimpleNamespace(
                    fan_out=SimpleNamespace(
                        response_seal=seal,
                        initialized_records=(SimpleNamespace(original_call_id="call-a"),),
                    )
                )
            ),
        ),
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
            return SimpleNamespace(selected_response_seal=seal_ref)

    class AccountingSource:
        def __init__(self, runtime, projection, bindings):
            self.collect_call = None
            self.accepted_call = None
            self.accepted_value = None
            self.closed_call = None

        def collect(self, target_run, **kwargs):
            self.collect_call = (target_run, kwargs)
            return "authenticated-source-facts"

        def collect_one_accepted(self, target_run, **kwargs):
            self.accepted_call = (target_run, kwargs)
            self.accepted_value = SimpleNamespace(original_call_id="call-a")
            return self.accepted_value

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
    closed_call = source_instances[0].closed_call
    assert closed_call is not None
    closed_run, closed_kwargs = closed_call
    assert closed_run is expected_run
    assert closed_kwargs.keys() == {
        "selected_only",
        "original_call_id",
        "_accepted_facts",
        "authenticated_effects_history",
        "allow_authenticated_later_suffix",
        "expected_closed_outcome_locator",
    }
    assert closed_kwargs["selected_only"] is True
    assert closed_kwargs["original_call_id"] == "call-a"
    assert closed_kwargs["_accepted_facts"] is source_instances[0].accepted_value
    assert closed_kwargs["authenticated_effects_history"] is None
    assert closed_kwargs["allow_authenticated_later_suffix"] is True
    locator = closed_kwargs["expected_closed_outcome_locator"]
    assert isinstance(locator, R14NativeHistoricalClosedOutcomeLocator)
    assert locator.selected_outcome_record_id == "closed-a"
    assert locator.selected_outcome_bytes == b"closed-outcome"
    assert locator.selected_outcome_fingerprint == "a" * 64
    assert locator.selected_effect_member_bytes == b"member"
    assert locator.outcome_commit_sequence == 18
    assert locator.publication_ordinal == 2
    assert locator.batch_record_ids == ("closed-a", "evidence-a")
    assert locator.selected_decision.subject_id == "decision-a"
    assert locator.selected_decision.revision.head == "head-a"
    assert locator.selected_decision.revision.fingerprint == "b" * 64
    assert built == [
        (
            "authenticated-closed-outcome",
            {
                "source_run": expected_run,
                "source_materialization_commitment": "source-commitment",
                "source_tenant_commit_sequence": 20,
                "authenticated_snapshot": None,
            },
        )
    ]
