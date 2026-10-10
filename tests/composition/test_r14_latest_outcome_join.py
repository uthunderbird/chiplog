"""Fast checks for the inert R14 retained-cause/current-outcome join."""

import hashlib
from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import evidence_head, prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2, reference
from chiplog.composition.r14_native_evidence_sources import (
    complete_ordered_dispatch_sources,
    complete_ordered_evidence_sources,
)
from chiplog.composition.r14_native_selected_accounting_source import (
    _closed_outcome_join_is_valid,
    _open_outcome_join_is_valid,
    _selected_outcome_publication,
)
from chiplog.composition.r16_dispatch_history import VerifiedDispatchHistory
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.capabilities.effects.test_dispatch_outcomes import request


def _selected_tuple(
    outcome: DispatchOutcomeRecordV2, *, ordinal: int = 1, commit_sequence: int = 1
) -> tuple[DispatchOutcomeRecordV2, SelectedEffectHistoryMember, StoredEffectRow]:
    raw = outcome.canonical_bytes()
    member = SelectedEffectHistoryMember(
        kind=outcome.kind,
        selected_decision=reference("decision", b"selected decision"),
        publication_ordinal=ordinal,
        original_selected_cut=reference("cut", b"selected cut"),
        record=outcome.record,
        predecessor=outcome.predecessor,
        intent=reference(
            outcome.snapshot.intent.intent_id, outcome.snapshot.intent.canonical_bytes()
        ),
        semantics=outcome.snapshot.intent.mandate.semantics,
        command_bytes=outcome.command.canonical_bytes(),
        record_bytes=raw,
    )
    physical = OwnerRecordBytes(
        owner="effects",
        record_kind="effects." + outcome.kind,
        record_id=outcome.record.head,
        schema_id=outcome.schema_id,
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    row = StoredEffectRow(
        commit_sequence=commit_sequence,
        publication_ordinal=ordinal,
        batch_record_ids=(outcome.record.head,),
        record=physical,
    )
    return outcome, member, row


@pytest.fixture
def retained_and_latest() -> tuple[DispatchRecordV2, DispatchOutcomeRecordV2]:
    send = original_send(members=("subject", "second"))
    retained = prepare_outcome(request(send, send))
    assert retained.snapshot.state == "OUTCOME_UNKNOWN"
    return send, retained


@pytest.mark.parametrize(
    ("positive", "negative", "expected"),
    [
        ((), (), "OUTCOME_UNKNOWN"),
        (("subject",), (), "PARTIAL"),
        (("subject", "second"), (), "CONFIRMED"),
        ((), ("subject", "second"), "FAILED_NO_EFFECT"),
        (("subject",), ("second",), "PARTIAL_CONFIRMED"),
    ],
)
def test_join_accepts_current_open_r16_states(
    retained_and_latest: tuple[DispatchRecordV2, DispatchOutcomeRecordV2],
    positive: tuple[str, ...],
    negative: tuple[str, ...],
    expected: str,
) -> None:
    send, retained = retained_and_latest
    latest = (
        retained
        if expected == "OUTCOME_UNKNOWN"
        else prepare_outcome(request(retained, send, positive, negative, name="late"))
    )
    assert latest.snapshot.state == expected
    assert latest.snapshot.obligation.state == "OPEN"
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            _selected_tuple(retained),
            _selected_tuple(latest, ordinal=2),
        )
        == latest
    )


def test_join_rejects_closed_or_rebound_latest_and_bad_physical_members(
    retained_and_latest: tuple[DispatchRecordV2, DispatchOutcomeRecordV2],
) -> None:
    send, retained = retained_and_latest
    latest = prepare_outcome(request(retained, send, ("subject", "second"), name="late-confirmed"))
    retained_tuple = _selected_tuple(retained)
    latest_tuple = _selected_tuple(latest, ordinal=2)
    _, latest_member, latest_row = latest_tuple
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            (send, latest_member, latest_row),
        )
        is None
    )
    assert _open_outcome_join_is_valid("another-intent", retained_tuple, latest_tuple) is None

    closed_snapshot = latest.snapshot.model_copy(
        update={"obligation": latest.snapshot.obligation.model_copy(update={"state": "CLOSED"})}
    )
    closed = latest.model_copy(update={"snapshot": closed_snapshot})
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id, retained_tuple, _selected_tuple(closed, ordinal=2)
        )
        is None
    )

    changed_original = latest.snapshot.model_copy(
        update={
            "obligation": latest.snapshot.obligation.model_copy(
                update={"original_intent": reference("other", b"other")}
            )
        }
    )
    rebound = latest.model_copy(update={"snapshot": changed_original})
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id, retained_tuple, _selected_tuple(rebound, ordinal=2)
        )
        is None
    )

    changed_intent = latest.snapshot.intent.model_copy(
        update={"mandate": latest.snapshot.intent.mandate.model_copy(update={"payload": b"other"})}
    )
    changed_intent_record = latest.model_copy(
        update={"snapshot": latest.snapshot.model_copy(update={"intent": changed_intent})}
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            _selected_tuple(changed_intent_record, ordinal=2),
        )
        is None
    )

    closed_retained = retained.model_copy(
        update={"snapshot": retained.snapshot.model_copy(update={"state": "CONFIRMED"})}
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            _selected_tuple(closed_retained),
            latest_tuple,
        )
        is None
    )

    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            (latest, latest_member.model_copy(update={"record_bytes": b"wrong"}), latest_row),
        )
        is None
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            (latest, latest_member, replace(latest_row, publication_ordinal=3)),
        )
        is None
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            (latest, latest_member, replace(latest_row, commit_sequence=0)),
        )
        is None
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            (latest, latest_member, replace(latest_row, batch_record_ids=())),
        )
        is None
    )
    _, retained_member, retained_row = retained_tuple
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            (retained, retained_member.model_copy(update={"record_bytes": b"wrong"}), retained_row),
            latest_tuple,
        )
        is None
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            (retained, retained_member, replace(retained_row, publication_ordinal=3)),
            latest_tuple,
        )
        is None
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            (retained, retained_member, replace(retained_row, commit_sequence=0)),
            latest_tuple,
        )
        is None
    )
    assert (
        _open_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            (retained, retained_member, replace(retained_row, batch_record_ids=())),
            latest_tuple,
        )
        is None
    )


@pytest.mark.parametrize(
    ("positive", "negative", "expected"),
    [(("subject",), (), "CONFIRMED"), ((), ("subject",), "FAILED_NO_EFFECT")],
)
def test_closed_join_accepts_exact_selected_terminal_chain(
    positive: tuple[str, ...], negative: tuple[str, ...], expected: str
) -> None:
    send = original_send(members=("subject",))
    retained = prepare_outcome(request(send, send))
    evidence = prepare_outcome(request(retained, send, positive, negative, name="closed-evidence"))
    closed = prepare_outcome(request(evidence, send, close=True, name="close"))
    assert retained.snapshot.state == "OUTCOME_UNKNOWN"
    assert retained.snapshot.obligation.state == "OPEN"
    assert closed.snapshot.state == expected
    assert closed.snapshot.obligation.state == "CLOSED"
    retained_tuple = _selected_tuple(retained, ordinal=1)
    evidence_tuple = _selected_tuple(evidence, ordinal=2, commit_sequence=2)
    closed_tuple = _selected_tuple(closed, ordinal=3, commit_sequence=3)
    current_publication = _selected_outcome_publication(
        closed_tuple[0], closed_tuple[1], closed_tuple[2]
    )
    cause_publication = _selected_outcome_publication(
        retained_tuple[0], retained_tuple[1], retained_tuple[2]
    )
    assert current_publication is not None
    assert cause_publication is not None
    assert current_publication.record_id == closed.record.head
    assert current_publication.commit_sequence == 3
    assert current_publication.publication_ordinal == 3
    assert current_publication.batch_record_ids == (closed.record.head,)
    assert current_publication != cause_publication
    assert (
        _selected_outcome_publication(
            closed_tuple[0], closed_tuple[1], replace(closed_tuple[2], commit_sequence=0)
        )
        is None
    )
    history = VerifiedDispatchHistory(
        members=(retained_tuple[1], evidence_tuple[1], closed_tuple[1]),
        v2_records=(retained, evidence, closed),
    )

    assert (
        _closed_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            closed_tuple,
            history.v2_records,
            history.members,
        )
        == closed
    )


def test_closed_join_rejects_open_foreign_and_stale_chain_members() -> None:
    send = original_send(members=("subject",))
    retained = prepare_outcome(request(send, send))
    evidence = prepare_outcome(request(retained, send, ("subject",), name="evidence"))
    closed = prepare_outcome(request(evidence, send, close=True, name="close"))
    retained_tuple = _selected_tuple(retained, ordinal=1)
    evidence_tuple = _selected_tuple(evidence, ordinal=2, commit_sequence=2)
    closed_tuple = _selected_tuple(closed, ordinal=3, commit_sequence=3)
    history = VerifiedDispatchHistory(
        members=(retained_tuple[1], evidence_tuple[1], closed_tuple[1]),
        v2_records=(retained, evidence, closed),
    )
    assert (
        _closed_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            evidence_tuple,
            history.v2_records,
            history.members,
        )
        is None
    )
    assert (
        _closed_outcome_join_is_valid(
            "foreign-intent",
            retained_tuple,
            closed_tuple,
            history.v2_records,
            history.members,
        )
        is None
    )

    alternate_evidence = prepare_outcome(
        request(retained, send, (), ("subject",), name="alternate-evidence")
    )
    alternate_closed = prepare_outcome(
        request(alternate_evidence, send, close=True, name="alternate-close")
    )
    stale_tuple = _selected_tuple(alternate_closed, ordinal=5, commit_sequence=5)
    assert (
        _closed_outcome_join_is_valid(
            send.snapshot.intent.intent_id,
            retained_tuple,
            stale_tuple,
            history.v2_records,
            history.members,
        )
        is None
    )


def test_complete_evidence_sources_bind_ordered_items_to_unique_selected_carriers() -> None:
    send = original_send(members=("first", "second"))
    pending = prepare_outcome(request(send, send))
    first_evidence = prepare_outcome(request(pending, send, ("first",), name="evidence-first"))
    second_evidence = prepare_outcome(
        request(first_evidence, send, ("second",), name="evidence-second")
    )
    terminal = prepare_outcome(request(second_evidence, send, close=True, name="close"))
    chain = (
        _selected_tuple(pending, ordinal=1),
        _selected_tuple(first_evidence, ordinal=2, commit_sequence=2),
        _selected_tuple(second_evidence, ordinal=3, commit_sequence=3),
        _selected_tuple(terminal, ordinal=4, commit_sequence=4),
    )
    history = VerifiedDispatchHistory(
        members=tuple(item[1] for item in chain),
        v2_records=tuple(item[0] for item in chain),
    )

    intent = reference(
        terminal.snapshot.intent.intent_id,
        terminal.snapshot.intent.canonical_bytes(),
    )
    call_intent = CallSubjectHead(
        subject_id=intent.subject_id,
        revision=Present(head=intent.head, fingerprint=intent.fingerprint),
    )
    dispatch_sources = complete_ordered_dispatch_sources(
        call_intent, history
    )
    assert dispatch_sources is not None
    assert tuple(source.physical_record.subject_id for source in dispatch_sources) == tuple(
        item.record.subject_id for item in history.members
    )
    assert tuple(source.canonical_record_bytes for source in dispatch_sources) == tuple(
        item.record_bytes for item in history.members
    )
    assert complete_ordered_dispatch_sources(
        call_intent,
        replace(history, members=history.members[:-1]),
    ) is None

    sources = complete_ordered_evidence_sources(terminal, history)

    assert sources is not None
    assert tuple(source.subject.subject_id for source in sources) == tuple(
        evidence_head(item).subject_id for item in terminal.snapshot.evidence
    )
    expected_carriers = tuple(
        next(
            record
            for record in history.v2_records
            if isinstance(record, DispatchOutcomeRecordV2)
            and record.kind == "EVIDENCE_RECORDED"
            and record.command.evidence == item
        )
        for item in terminal.snapshot.evidence
    )
    assert tuple(source.physical_record.subject_id for source in sources) == tuple(
        record.record.subject_id for record in expected_carriers
    )
    assert tuple(source.canonical_record_bytes for source in sources) == tuple(
        record.canonical_bytes() for record in expected_carriers
    )

    duplicate_carrier = replace(
        history,
        members=(*history.members, history.members[1]),
    )
    omitted_carrier = replace(
        history,
        members=tuple(
            member for member in history.members if member.record != second_evidence.record
        ),
    )
    reversed_manifest = terminal.model_copy(
        update={
            "snapshot": terminal.snapshot.model_copy(
                update={"evidence_heads": tuple(reversed(terminal.snapshot.evidence_heads))}
            )
        }
    )
    assert complete_ordered_evidence_sources(terminal, duplicate_carrier) is None
    assert complete_ordered_evidence_sources(terminal, omitted_carrier) is None
    assert complete_ordered_evidence_sources(reversed_manifest, history) is None

    foreign_send = original_send(members=("first", "second"))
    foreign_pending = prepare_outcome(request(foreign_send, foreign_send))
    foreign = prepare_outcome(request(foreign_pending, foreign_send, ("first",), name="foreign"))
    foreign_evidence = foreign.snapshot.evidence[-1]
    foreign_terminal = terminal.model_copy(
        update={
            "snapshot": terminal.snapshot.model_copy(
                update={
                    "evidence": (foreign_evidence,),
                    "evidence_heads": (evidence_head(foreign_evidence),),
                }
            )
        }
    )
    assert complete_ordered_evidence_sources(foreign_terminal, history) is None

    malformed_member = history.members[1].model_copy(
        update={"kind": "OUTCOME_UNKNOWN"},
    )
    malformed_history = replace(
        history,
        members=(history.members[0], malformed_member, *history.members[2:]),
    )
    assert complete_ordered_evidence_sources(terminal, malformed_history) is None
