from dataclasses import replace
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NotApplicable,
    OriginalObligationBinding,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_domain import (
    RecoveredClosure,
    RecoveryHold,
    RecoveryIntegrityFault,
    model_continuation_ready,
    sealed_call_accounting,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    EvidenceReduction,
    ReadOnlyAcceptedCall,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    RecoveredOutcomeSubject,
    RecoveryRequiredResult,
    SuccessResult,
    TerminalCallFrontier,
)


def head(name: str) -> Present:
    import hashlib

    return Present(head=name, fingerprint=hashlib.sha256(name.encode()).hexdigest())


def recovery_call(*, recovered: bool = False) -> TerminalCallFrontier:
    return TerminalCallFrontier(
        original_call_id="call",
        response_id="response",
        acceptance=ConsequentialAcceptedCall(
            initialized=head("initialized"),
            accepted=head("accepted"),
            execution_intent=head("execution"),
            external_effect_intent=NotApplicable(),
            complete_acceptance_manifest=(head("accepted"), head("execution")),
        ),
        disposition=RecoveryRequiredResult(
            terminal=head("terminal"),
            result=Absent(),
            obligation=OriginalObligationBinding(
                original_run_id="run",
                original_call_id="call",
                obligation_id="obligation",
                obligation_stream_id="obligation-stream",
                obligation_head="obligation-head",
                closure_predicate_id="closure",
                closure_predicate_version="v1",
                resolver_id="resolver",
                resolver_version="v1",
                reducer_id="reducer",
                reducer_version="v1",
                evidence_stream_id="evidence-stream",
                evidence_head=Absent(),
            ),
            recovered_outcome=RecoveredOutcomeSubject(
                original_call_id="call",
                recovery_obligation_id="obligation",
                outcome=head("outcome") if recovered else Absent(),
            ),
        ),
    )


def closure() -> RecoveredClosure:
    return RecoveredClosure(
        original_call_id="call",
        obligation_id="obligation",
        original_obligation_head="obligation-head",
        terminal_disposition=head("terminal"),
        outcome=head("outcome"),
        accepted_evidence=head("witness"),
        closure=head("closure"),
        resolver_batch=head("resolver-batch"),
        resolver_id="resolver",
        resolver_version="v1",
        reducer_id="reducer",
        reducer_version="v1",
        reduction_id="reduction",
        accepted_semantic_class="confirmed",
    )


def reduction() -> EvidenceReduction:
    return EvidenceReduction(
        stream_id="evidence-stream",
        reduction_id="reduction",
        reducer_id="reducer",
        reducer_version="v1",
        current_reduction=head("reduction0"),
        ordered_consumed_evidence=(head("witness"),),
        accepted_witness=head("witness"),
        accepted_outcome=head("outcome"),
        obligation_closure=head("closure"),
        resolver_batch=head("resolver-batch"),
        semantic_class="confirmed",
        consumability="CONSUMABLE",
    )


def test_open_recovery_is_terminal_accountable_but_not_model_consumable() -> None:
    accounted = sealed_call_accounting("response", "manifest", ("call",), (recovery_call(),))
    assert accounted.calls[0].disposition.kind == "RECOVERY_REQUIRED"
    with pytest.raises(RecoveryHold):
        model_continuation_ready(accounted, (), ())
    # Even independently appended evidence/reduction cannot replace the absent
    # original resolver outcome at the earlier immutable frontier.
    with pytest.raises(RecoveryHold):
        model_continuation_ready(accounted, (closure(),), (reduction(),))


def test_recovered_pair_requires_exact_closure_and_current_reduction() -> None:
    accounted = sealed_call_accounting(
        "response", "manifest", ("call",), (recovery_call(recovered=True),)
    )
    ready = model_continuation_ready(accounted, (closure(),), (reduction(),))
    assert ready.recovered[0][0].outcome == head("outcome")
    with pytest.raises(RecoveryHold):
        model_continuation_ready(accounted, (), (reduction(),))
    with pytest.raises(RecoveryHold):
        model_continuation_ready(accounted, (closure(),), ())


@pytest.mark.parametrize(
    "field",
    [
        "obligation_id",
        "original_obligation_head",
        "resolver_id",
        "resolver_version",
        "reducer_id",
        "reducer_version",
        "reduction_id",
        "accepted_semantic_class",
    ],
)
def test_substituted_recovery_binding_cannot_unlock_continuation(field: str) -> None:
    accounted = sealed_call_accounting(
        "response", "manifest", ("call",), (recovery_call(recovered=True),)
    )
    with pytest.raises(RecoveryHold):
        model_continuation_ready(
            accounted, (replace(closure(), **cast(Any, {field: "rival"})),), (reduction(),)
        )


@pytest.mark.parametrize(
    "field",
    [
        "accepted_witness",
        "accepted_outcome",
        "obligation_closure",
        "resolver_batch",
    ],
)
def test_current_reduction_cannot_substitute_an_immutable_accepted_head(field: str) -> None:
    accounted = sealed_call_accounting(
        "response", "manifest", ("call",), (recovery_call(recovered=True),)
    )
    changed = EvidenceReduction.model_validate({**reduction().model_dump(), field: head("rival")})
    with pytest.raises(RecoveryHold):
        model_continuation_ready(accounted, (closure(),), (changed,))


def test_compatible_reduction_advance_preserves_witness_and_prior_join_observation() -> None:
    accounted = sealed_call_accounting(
        "response", "manifest", ("call",), (recovery_call(recovered=True),)
    )
    initial = reduction()
    before = model_continuation_ready(accounted, (closure(),), (initial,))
    advanced = EvidenceReduction.model_validate(
        {
            **initial.model_dump(),
            "current_reduction": head("reduction1"),
            "ordered_consumed_evidence": (head("witness"), head("compatible-later-evidence")),
        }
    )
    after = model_continuation_ready(accounted, (closure(),), (advanced,))
    assert before.recovered[0][1].current_reduction == head("reduction0")
    assert after.recovered[0][1].current_reduction == head("reduction1")
    assert after.recovered[0][0] == before.recovered[0][0]
    held = EvidenceReduction.model_validate({**advanced.model_dump(), "consumability": "HOLD"})
    with pytest.raises(RecoveryHold):
        model_continuation_ready(accounted, (closure(),), (held,))
    assert before.recovered[0][1].consumability == "CONSUMABLE"


@pytest.mark.parametrize("members", [(), ("call", "extra"), ("extra",)])
def test_accounting_requires_complete_sealed_membership(members: tuple[str, ...]) -> None:
    with pytest.raises(RecoveryHold):
        sealed_call_accounting("response", "manifest", members, (recovery_call(),))


def test_duplicate_call_or_partial_acceptance_batch_is_integrity_fault() -> None:
    with pytest.raises(RecoveryIntegrityFault):
        sealed_call_accounting("response", "manifest", ("call", "call"), (recovery_call(),) * 2)
    call = recovery_call()
    assert isinstance(call.acceptance, ConsequentialAcceptedCall)
    acceptance = ConsequentialAcceptedCall.model_validate(
        {
            **call.acceptance.model_dump(),
            "complete_acceptance_manifest": (head("accepted"), head("rival-execution")),
        }
    )
    changed = TerminalCallFrontier.model_validate({**call.model_dump(), "acceptance": acceptance})
    with pytest.raises(RecoveryIntegrityFault):
        sealed_call_accounting("response", "manifest", ("call",), (changed,))


def test_reduction_cannot_change_semantic_class_or_omit_accepted_witness() -> None:
    accounted = sealed_call_accounting(
        "response", "manifest", ("call",), (recovery_call(recovered=True),)
    )
    for mutation in (
        {"semantic_class": "contradictory"},
        {"ordered_consumed_evidence": (head("unrelated"),)},
        {"ordered_consumed_evidence": (head("witness"), head("witness"))},
    ):
        changed = EvidenceReduction.model_validate({**reduction().model_dump(), **mutation})
        with pytest.raises(RecoveryHold):
            model_continuation_ready(accounted, (closure(),), (changed,))


def test_readonly_call_outcome_requires_entire_non_resettable_lineage() -> None:
    attempts = (
        ReadOnlyAttemptMember(
            ordinal=0,
            attempt_id="attempt0",
            initialized=head("init0"),
            accepted=head("accept0"),
            outcome=head("failure0"),
            result_or_obligation=head("error0"),
            predecessor=Absent(),
        ),
        ReadOnlyAttemptMember(
            ordinal=1,
            attempt_id="attempt1",
            initialized=head("init1"),
            accepted=head("accept1"),
            outcome=head("success1"),
            result_or_obligation=head("result1"),
            predecessor=head("accept0"),
        ),
    )
    accepted = ReadOnlyAcceptedCall(
        initialized=head("init1"),
        accepted=head("accept1"),
        lineage_id="lineage",
        ordinal=1,
        readonly_proof=head("readonly"),
        snapshot_binding=head("snapshot"),
        lineage=ReadOnlyRetryLineage(
            lineage_id="lineage",
            original_call_id="call",
            max_attempts=2,
            budget_version="budget.v1",
            reducer_id="reducer",
            reducer_version="reducer.v1",
        ),
        complete_ordered_attempts=attempts,
        shared_counter=head("counter2"),
        attempts_consumed=2,
        call_level_outcome=head("call-result"),
        complete_lineage_reducer_batch=head("reducer-batch"),
    )
    call = TerminalCallFrontier(
        original_call_id="call",
        response_id="response",
        acceptance=accepted,
        disposition=SuccessResult(
            terminal=head("terminal"),
            result=head("call-result"),
            recovered_outcome=NotApplicable(),
        ),
    )
    accounted = sealed_call_accounting("response", "manifest", ("call",), (call,))
    assert model_continuation_ready(accounted, (), ()).accounting == accounted
    for mutation in (
        {"complete_ordered_attempts": attempts[1:]},
        {"complete_ordered_attempts": tuple(reversed(attempts))},
        {"attempts_consumed": 1},
        {"lineage_id": "replacement-lineage"},
        {"call_level_outcome": head("result1")},
    ):
        changed_acceptance = ReadOnlyAcceptedCall.model_validate(
            {**accepted.model_dump(), **mutation}
        )
        changed = TerminalCallFrontier.model_validate(
            {
                **call.model_dump(),
                "acceptance": changed_acceptance,
            }
        )
        with pytest.raises(RecoveryIntegrityFault):
            sealed_call_accounting("response", "manifest", ("call",), (changed,))
