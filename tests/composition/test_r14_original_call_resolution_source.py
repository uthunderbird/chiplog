"""Source mapping keeps the selected closed R16 member as physical evidence."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    AcceptedOpenRecoveryOpeningV1,
    AcceptedOpenTerminalDispositionV1,
)
from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    OriginalEffectEvidenceReducerPolicyV1,
    OriginalResolverAuthorityPolicyV1,
    OriginalResolverInvocationV1,
)
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference
from chiplog.composition import (
    r14_original_call_resolution_source as source_module,
)
from chiplog.composition import (
    r14_original_resolver_invocation_publication,
    r14_original_resolver_policy_publication,
)
from chiplog.composition.r14_original_call_resolution_source import (
    _selected_resolution_anchor_is_valid,
    _selected_witness_source,
    _verify_same_obligation_append,
    capture_original_call_resolution_request,
)
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.support.capabilities.agent_loop.original_call_resolution_preparation import request
from tests.support.capabilities.effects.dispatch_outcomes import request as outcome_request


@pytest.mark.parametrize(
    ("actual", "anchored", "materialized", "valid"),
    (
        ("current", "current", False, True),
        ("resulting", "predecessor", False, True),
        ("resulting", "predecessor", True, False),
        ("resulting", "foreign", False, False),
        ("advanced", "predecessor", False, False),
    ),
)
def test_selected_resolution_anchor_accepts_only_exact_unmaterialized_recovery(
    actual: str, anchored: str, materialized: bool, valid: bool
) -> None:
    assert (
        _selected_resolution_anchor_is_valid(
            actual,
            anchored,
            "predecessor",
            "resulting",
            materialized=materialized,
        )
        is valid
    )


def test_selected_witness_source_uses_exact_selected_member_and_rejects_changed_bytes() -> None:
    cut = request().cut
    source = cut.selected_witness
    outcome = DispatchOutcomeRecordV2.model_validate_json(source.canonical_record_bytes)
    intent_ref = effects_reference(
        outcome.snapshot.intent.intent_id, outcome.snapshot.intent.canonical_bytes()
    )
    member = SimpleNamespace(
        record=ExactHead(
            subject_id=source.physical_record.subject_id,
            head=source.physical_record.revision.head,
            fingerprint=source.physical_record.revision.fingerprint,
        ),
        selected_decision=ExactHead(
            subject_id=source.selected_decision.subject_id,
            head=source.selected_decision.revision.head,
            fingerprint=source.selected_decision.revision.fingerprint,
        ),
        intent=ExactHead(
            subject_id=intent_ref.subject_id,
            head=intent_ref.head,
            fingerprint=intent_ref.fingerprint,
        ),
        kind=outcome.kind,
        record_bytes=source.canonical_record_bytes,
    )
    closed = SimpleNamespace(latest_outcome=outcome, latest_selected_member=member)

    captured = _selected_witness_source(closed)
    assert captured.owner == "effects"
    assert captured.canonical_record_bytes == source.canonical_record_bytes
    assert captured.selected_decision == source.selected_decision
    assert captured.physical_record == CallSubjectHead.model_validate(
        source.physical_record.model_dump()
    )

    changed = SimpleNamespace(**vars(member))
    changed.record_bytes = b"{}"
    with pytest.raises(LoopRejected, match="selected witness differs"):
        _selected_witness_source(
            SimpleNamespace(latest_outcome=outcome, latest_selected_member=changed)
        )


def test_late_evidence_append_preserves_selected_r16_intent_and_obligation() -> None:
    send = original_send()
    unknown = prepare_outcome(outcome_request(send, send))
    confirmed = prepare_outcome(
        outcome_request(unknown, send, positive=("subject",), name="confirmed")
    )
    closed = prepare_outcome(outcome_request(confirmed, send, close=True, name="closed"))
    late = prepare_outcome(outcome_request(closed, send, negative=("subject",), name="late-rival"))

    _verify_same_obligation_append(closed, late)
    with pytest.raises(LoopRejected, match="same-obligation append"):
        _verify_same_obligation_append(unknown, late)

    foreign_send = original_send(members=("foreign-child",))
    foreign_unknown = prepare_outcome(outcome_request(foreign_send, foreign_send))
    foreign = prepare_outcome(
        outcome_request(foreign_unknown, foreign_send, name="foreign-evidence")
    )
    with pytest.raises(LoopRejected, match="same-obligation append"):
        _verify_same_obligation_append(closed, foreign)


def test_capture_builds_cut_from_selected_sources_and_closed_effect_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = request()
    cut = template.cut
    witness = DispatchOutcomeRecordV2.model_validate_json(
        cut.selected_witness.canonical_record_bytes
    )
    intent_ref = effects_reference(
        witness.snapshot.intent.intent_id, witness.snapshot.intent.canonical_bytes()
    )
    selected_member = SimpleNamespace(
        record=ExactHead(
            subject_id=cut.selected_witness.physical_record.subject_id,
            head=cut.selected_witness.physical_record.revision.head,
            fingerprint=cut.selected_witness.physical_record.revision.fingerprint,
        ),
        selected_decision=ExactHead(
            subject_id=cut.selected_witness.selected_decision.subject_id,
            head=cut.selected_witness.selected_decision.revision.head,
            fingerprint=cut.selected_witness.selected_decision.revision.fingerprint,
        ),
        intent=ExactHead(
            subject_id=intent_ref.subject_id,
            head=intent_ref.head,
            fingerprint=intent_ref.fingerprint,
        ),
        kind=witness.kind,
        record_bytes=witness.canonical_bytes(),
    )
    closed = SimpleNamespace(
        latest_outcome=witness,
        latest_selected_member=selected_member,
        tenant_id=cut.tenant_id,
        database_identity=(cut.database_id, 1, 2),
        tenant_commit_sequence=cut.tenant_commit_sequence,
        materialization_commitment=cut.materialization_commitment,
        complete_ordered_evidence_sources=cut.complete_ordered_evidence,
    )

    class Gate:
        @contextmanager
        def hold(self):
            yield

    class Connection:
        def execute(self, sql, _params=()):
            if "SELECT head FROM tenant_heads" in sql:
                return SimpleNamespace(fetchone=lambda: (cut.tenant_commit_sequence,))
            return SimpleNamespace(fetchall=lambda: ())

    @contextmanager
    def connection(_database):
        yield Connection()

    class Runtime:
        _tenant_id = cut.tenant_id
        _database_identity = (cut.database_id, 1, 2)
        _database = tmp_path / "source.sqlite"

        def _authority_gate(self):
            return Gate()

        def _pending(self) -> None:
            return None

        def _check_database_identity(self) -> None:
            return None

        def read_selected_original_obligation_sources(self, *, historical=False):
            assert historical is False
            return cut.current_obligation, cut.call_terminal

        def _loop_decisions(self):
            return SimpleNamespace(entries=lambda: ())

    runtime = Runtime()
    runtime._commitment_journal = SimpleNamespace(
        load=lambda _tenant: cut.materialization_commitment
    )
    opening = AcceptedOpenRecoveryOpeningV1.model_validate_json(
        cut.current_obligation.canonical_record_bytes
    )
    terminal = AcceptedOpenTerminalDispositionV1.model_validate_json(
        cut.call_terminal.canonical_record_bytes
    )
    resolver = OriginalResolverAuthorityPolicyV1.model_validate_json(
        cut.resolver_policy.canonical_record_bytes
    )
    reducer = OriginalEffectEvidenceReducerPolicyV1.model_validate_json(
        cut.reducer_policy.canonical_record_bytes
    )
    invocation = OriginalResolverInvocationV1.model_validate_json(
        cut.authority.authenticated_invocation.canonical_record_bytes
    )
    monkeypatch.setattr(source_module, "read_connection", connection)
    monkeypatch.setattr(
        source_module,
        "capture_authority_snapshot_commitment",
        lambda *_args: cut.materialization_commitment,
    )
    monkeypatch.setattr(
        r14_original_resolver_policy_publication,
        "read_selected_original_resolver_authority_policy_source_held",
        lambda _runtime, **_kwargs: (resolver, cut.resolver_policy),
    )
    monkeypatch.setattr(
        r14_original_resolver_policy_publication,
        "read_selected_original_effect_evidence_reducer_policy_source_held",
        lambda _runtime, **_kwargs: (reducer, cut.reducer_policy),
    )
    monkeypatch.setattr(
        r14_original_resolver_invocation_publication,
        "read_selected_original_resolver_invocation_source_held",
        lambda _runtime, **_kwargs: (invocation, cut.authority.authenticated_invocation),
    )
    monkeypatch.setattr(
        source_module,
        "read_execution_recovery_history",
        lambda *_args, **_kwargs: (
            SimpleNamespace(records=(object(),)),
            None,
            None,
            (
                SimpleNamespace(
                    proposal=SimpleNamespace(
                        fan_out=SimpleNamespace(
                            response_seal=SimpleNamespace(
                                response_seal_id="seal", digest=lambda: "d" * 64
                            )
                        )
                    )
                ),
            ),
            None,
            None,
        ),
    )
    monkeypatch.setattr(source_module, "execution_run_head", lambda _record: object())
    monkeypatch.setattr(
        source_module,
        "R14OriginalNativePrepareSource",
        lambda _runtime: SimpleNamespace(collect=lambda _seal: object()),
    )
    monkeypatch.setattr(
        source_module,
        "bind_native_execution_preparation_v2",
        lambda _projection: object(),
    )

    class Accounting:
        def __init__(self, *_args) -> None:
            pass

        def collect_one_accepted_closed_outcome(self, *_args, **_kwargs):
            return closed

    monkeypatch.setattr(source_module, "R14NativeSelectedAccountingSource", Accounting)

    captured = capture_original_call_resolution_request(runtime)
    assert captured.cut.original == opening.original
    assert captured.cut.call_terminal == cut.call_terminal
    assert captured.cut.selected_witness.canonical_record_bytes == witness.canonical_bytes()
    assert captured.cut.complete_ordered_evidence == cut.complete_ordered_evidence
    assert captured.cut.expected_closure.kind == "ABSENT"
    assert captured.cut.expected_recovered_outcome.kind == "ABSENT"
    assert captured.cut.authority.authenticated_invocation == cut.authority.authenticated_invocation
    assert terminal.original_call_id == opening.original.original_call_id
    assert prepare_original_call_resolution(captured).kind == (
        "PREPARED_ORIGINAL_CALL_RESOLUTION_V1"
    )
