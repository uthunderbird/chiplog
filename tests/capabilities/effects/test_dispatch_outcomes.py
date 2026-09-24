"""Closed original-child transition, complete evidence and late conflict mutations."""

import pytest
from tests.capabilities.effects.support.dispatch_outcomes import original_send

from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchEvidenceV2,
    DispatchOutcomeCommandV2,
    DispatchOutcomePreparationV2,
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2, digest, reference
from chiplog.capabilities.effects.domain import EffectRuleViolation


def request(
    previous: DispatchRecordV2 | DispatchOutcomeRecordV2,
    send: DispatchRecordV2,
    positive: tuple[str, ...] = (),
    negative: tuple[str, ...] = (),
    *,
    close: bool = False,
    name: str = "receipt",
) -> DispatchOutcomePreparationV2:
    evidence = (
        None
        if close
        else DispatchEvidenceV2(
            evidence_id=name,
            source_identity="source",
            transmission=send.snapshot.transmissions[0].transmission,
            raw_bytes=name.encode(),
            raw_digest=digest(name.encode()),
            observation="PROVIDER_RECEIPT",
            occurred_members=positive,
            permanently_incapable_members=negative,
        )
    )
    command = DispatchOutcomeCommandV2(
        schema_id="chiplog.effects.dispatch-outcome-command.v2",
        identity=CommandIdentity(
            command_id=name, fingerprint=digest(name.encode()), expected_tenant_head=1
        ),
        operation="RESOLVE_OBLIGATION" if close else "APPEND_EVIDENCE",
        intent=reference(send.snapshot.intent.intent_id, send.snapshot.intent.canonical_bytes()),
        original_send=send.record,
        expected_attempt=previous.snapshot.attempt,
        complete_children=tuple(c.transmission for c in send.snapshot.transmissions),
        prior_evidence=previous.snapshot.evidence_heads
        if isinstance(previous, DispatchOutcomeRecordV2)
        else (),
        evidence=evidence,
        resolver=reference("principal", b"principal") if close else None,
    )
    return DispatchOutcomePreparationV2(
        schema_id="chiplog.effects.dispatch-outcome-preparation.v2",
        original_send=send,
        previous=previous,
        command=command,
    )


def test_full_public_preparation_and_separate_resolver_contract() -> None:
    send = original_send()
    pending = request(send, send)
    assert DispatchOutcomePreparationV2.model_validate_json(pending.canonical_bytes()) == pending
    unknown = prepare_outcome(pending)
    assert unknown.snapshot.state == "OUTCOME_UNKNOWN"
    with pytest.raises(EffectRuleViolation, match="not every"):
        prepare_outcome(request(unknown, send, close=True))
    confirmed = prepare_outcome(request(unknown, send, ("subject",), name="confirm"))
    assert confirmed.snapshot.state == "CONFIRMED"
    assert confirmed.snapshot.obligation.state == "OPEN"
    closed = prepare_outcome(request(confirmed, send, close=True))
    assert closed.snapshot.obligation.state == "CLOSED"
    late = prepare_outcome(request(closed, send, negative=("subject",), name="rival"))
    assert late.snapshot.state == "CONFIRMED"
    assert late.snapshot.conflicting_evidence
    assert late.snapshot.evidence_heads[-1] not in closed.snapshot.evidence_heads
    assert late.snapshot.obligation.obligation == unknown.snapshot.obligation.obligation


@pytest.mark.parametrize(
    "mutation", ("omit", "extra", "substitute", "duplicate", "stale", "version")
)
def test_wrong_original_child_and_exact_current_head_reject(mutation: str) -> None:
    send = original_send()
    value = request(send, send)
    child = send.snapshot.transmissions[0].transmission
    changes: dict[str, dict[str, object]] = {
        "omit": {"complete_children": ()},
        "extra": {"complete_children": (child, child)},
        "substitute": {"original_send": reference("alias", b"alias")},
        "duplicate": {"prior_evidence": (child, child)},
        "stale": {"expected_attempt": reference("stale", b"stale")},
        "version": {"intent": reference("different-version", b"version")},
    }
    with pytest.raises(EffectRuleViolation):
        prepare_outcome(
            value.model_copy(update={"command": value.command.model_copy(update=changes[mutation])})
        )


@pytest.mark.parametrize(
    "positive,negative",
    ((("foreign",), ()), (("subject", "subject"), ()), (("subject",), ("subject",))),
)
def test_invalid_member_partition_rejects(
    positive: tuple[str, ...], negative: tuple[str, ...]
) -> None:
    send = original_send()
    with pytest.raises(EffectRuleViolation):
        prepare_outcome(request(send, send, positive, negative))


@pytest.mark.parametrize("mixed", (False, True))
def test_definite_no_effect_and_fully_mixed_need_separate_closure(mixed: bool) -> None:
    members = ("first", "second") if mixed else ("subject",)
    send = original_send(members)
    unknown = prepare_outcome(request(send, send))
    confirmed = prepare_outcome(
        request(
            unknown,
            send,
            members[:1] if mixed else (),
            members[1:] if mixed else members,
            name="definite",
        )
    )
    expected = "PARTIAL_CONFIRMED" if mixed else "FAILED_NO_EFFECT"
    assert confirmed.snapshot.state == expected
    assert confirmed.snapshot.obligation.state == "OPEN"
    closed = prepare_outcome(request(confirmed, send, close=True))
    assert closed.snapshot.state == expected
    assert closed.snapshot.obligation.state == "CLOSED"
    if mixed:
        direct = prepare_outcome(request(send, send, members[:1], members[1:]))
        assert direct.snapshot.state == "PARTIAL"
        assert prepare_outcome(request(direct, send, close=True)).snapshot.state == expected
