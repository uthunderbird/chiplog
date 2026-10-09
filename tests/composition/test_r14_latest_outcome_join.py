"""Fast checks for the inert R14 retained-cause/current-outcome join."""

import hashlib
from dataclasses import replace

import pytest

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.composition.r14_native_selected_accounting_source import (
    _open_outcome_join_is_valid,
)
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from tests.capabilities.effects.support.dispatch_outcomes import original_send
from tests.capabilities.effects.test_dispatch_outcomes import request


def _selected_tuple(
    outcome: DispatchOutcomeRecordV2, *, ordinal: int = 1, commit_sequence: int = 1
) -> tuple[DispatchOutcomeRecordV2, SelectedEffectHistoryMember, StoredEffectRow]:
    raw = outcome.canonical_bytes()
    member = SelectedEffectHistoryMember(
        kind="EVIDENCE_RECORDED",
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
def retained_and_latest():
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
def test_join_accepts_current_open_r16_states(retained_and_latest, positive, negative, expected):
    send, retained = retained_and_latest
    latest = (
        retained
        if expected == "OUTCOME_UNKNOWN"
        else prepare_outcome(request(retained, send, positive, negative, name="late"))
    )
    assert latest.snapshot.state == expected
    assert latest.snapshot.obligation.state == "OPEN"
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        _selected_tuple(retained),
        _selected_tuple(latest, ordinal=2),
    ) == latest


def test_join_rejects_closed_or_rebound_latest_and_bad_physical_members(retained_and_latest):
    send, retained = retained_and_latest
    latest = prepare_outcome(
        request(retained, send, ("subject", "second"), name="late-confirmed")
    )
    retained_tuple = _selected_tuple(retained)
    latest_tuple = _selected_tuple(latest, ordinal=2)
    _, latest_member, latest_row = latest_tuple
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        retained_tuple,
        (send, latest_member, latest_row),
    ) is None
    assert _open_outcome_join_is_valid(
        "another-intent", retained_tuple, latest_tuple
    ) is None

    closed_snapshot = latest.snapshot.model_copy(
        update={"obligation": latest.snapshot.obligation.model_copy(update={"state": "CLOSED"})}
    )
    closed = latest.model_copy(update={"snapshot": closed_snapshot})
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id, retained_tuple, _selected_tuple(closed, ordinal=2)
    ) is None

    changed_original = latest.snapshot.model_copy(
        update={
            "obligation": latest.snapshot.obligation.model_copy(
                update={"original_intent": reference("other", b"other")}
            )
        }
    )
    rebound = latest.model_copy(update={"snapshot": changed_original})
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id, retained_tuple, _selected_tuple(rebound, ordinal=2)
    ) is None

    changed_intent = latest.snapshot.intent.model_copy(
        update={"mandate": latest.snapshot.intent.mandate.model_copy(update={"payload": b"other"})}
    )
    changed_intent_record = latest.model_copy(
        update={"snapshot": latest.snapshot.model_copy(update={"intent": changed_intent})}
    )
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        retained_tuple,
        _selected_tuple(changed_intent_record, ordinal=2),
    ) is None

    closed_retained = retained.model_copy(
        update={
            "snapshot": retained.snapshot.model_copy(
                update={"state": "CONFIRMED"}
            )
        }
    )
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        _selected_tuple(closed_retained),
        latest_tuple,
    ) is None

    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        retained_tuple,
        (latest, latest_member.model_copy(update={"record_bytes": b"wrong"}), latest_row),
    ) is None
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        retained_tuple,
        (latest, latest_member, replace(latest_row, publication_ordinal=3)),
    ) is None
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        retained_tuple,
        (latest, latest_member, replace(latest_row, commit_sequence=0)),
    ) is None
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        retained_tuple,
        (latest, latest_member, replace(latest_row, batch_record_ids=())),
    ) is None
    _, retained_member, retained_row = retained_tuple
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        (retained, retained_member.model_copy(update={"record_bytes": b"wrong"}), retained_row),
        latest_tuple,
    ) is None
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        (retained, retained_member, replace(retained_row, publication_ordinal=3)),
        latest_tuple,
    ) is None
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        (retained, retained_member, replace(retained_row, commit_sequence=0)),
        latest_tuple,
    ) is None
    assert _open_outcome_join_is_valid(
        send.snapshot.intent.intent_id,
        (retained, retained_member, replace(retained_row, batch_record_ids=())),
        latest_tuple,
    ) is None
