"""Structural checks for the inert scoped-authorization V3 batch codec."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_producer_history import (
    H1ProducerHistoryError,
    H1ProducerHistoryHold,
    interpret_h1_producer_effects_history,
)
from chiplog.composition.h1_scoped_authorization_historical_binder import (
    H1ScopedAuthorizationHistoricalBinder,
    ScopedAuthorizationHistoricalBindError,
)
from chiplog.composition.h1_scoped_authorization_issuance_v3 import (
    SCHEMA,
    ScopedAuthorizationIssuanceV3,
    decode_scoped_authorization_issuance_v3,
)
from chiplog.composition.r16_dispatch_history import (
    validate_selected_dispatch_sources,
    verify_dispatch_history,
)
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.composition.scoped_authorization_history import (
    decode_scoped_authorization_batch_v3,
)
from chiplog.platform._owner_publication_contracts import (
    OwnerCommandBytes,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _issuance() -> ScopedAuthorizationIssuanceV3:
    raw = (
        Path(__file__).with_name("fixtures") / "h1_scoped_authorization_issuance_v3.json"
    ).read_bytes()
    return decode_scoped_authorization_issuance_v3(raw)


def _batch(issuance: ScopedAuthorizationIssuanceV3) -> SingleOwnerBatch:
    request_bytes = issuance.sent.canonical_payload
    command = OwnerCommandBytes(
        owner="effects",
        schema_id="chiplog.effects.scoped-dispatch-request.v3",
        canonical_bytes=request_bytes,
        fingerprint=_digest(request_bytes),
    )
    authentication = WorkerAuthentication(
        invocation=issuance.invocation,
        applicability_schema=SCHEMA,
        applicability_bytes=issuance.canonical_bytes(),
        applicability_fingerprint=_digest(issuance.canonical_bytes()),
    )
    records = issuance.output_records
    return SingleOwnerBatch(
        operation="effects.authorize_scoped_delivery_v3",
        identity=issuance.identity,
        authentication=authentication,
        expected=issuance.expected,
        command=command,
        complete_records=records,
        complete_batch_fingerprint=_digest(
            _canonical([record.model_dump(mode="json") for record in records])
        ),
    )


def _history(
    batch: SingleOwnerBatch, *, rows: tuple[object, ...] | None = None
) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot]:
    decision = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=batch,
            issuance_id="issuance",
            fence_generation="generation",
            fence_frontier=0,
            predecessor_commitment="0" * 64,
        ),
        decision_id="decision",
        decision_head="decision/head",
        decision_fingerprint="1" * 64,
        resulting_commitment="2" * 64,
        tenant_commit_sequence=1,
    )
    actual = batch.complete_records if rows is None else rows
    record_ids = tuple(record.record_id for record in batch.complete_records)
    cut = MaterializedEffectsCut(
        tenant_id=batch.identity.tenant_id,
        tenant_frontier=1,
        materialization_commitment="3" * 64,
        owner_journal_head="journal-head",
        physical_path="/structural",
        physical_device=0,
        physical_inode=0,
        rows=tuple(
            StoredEffectRow(1, ordinal, record_ids, record)  # type: ignore[arg-type]
            for ordinal, record in enumerate(actual)
        ),
        worker=None,
        latest_runs=(),
    )
    return cut, OwnerJournalSnapshot(
        batch.identity.tenant_id, "journal-head", (decision,), frozenset()
    )


def test_decodes_only_the_exact_two_record_v3_issuance_batch() -> None:
    issuance = _issuance()
    batch = _batch(issuance)

    assert decode_scoped_authorization_batch_v3(batch) == issuance


@pytest.mark.parametrize(
    "update",
    (
        lambda batch: {"complete_records": tuple(reversed(batch.complete_records))},
        lambda batch: {"complete_records": batch.complete_records[:1]},
        lambda batch: {"complete_records": batch.complete_records + batch.complete_records[:1]},
        lambda batch: {"complete_batch_fingerprint": "0" * 64},
    ),
)
def test_rejects_reordered_partial_extra_or_wrong_record_witness(update: object) -> None:
    batch = _batch(_issuance())
    with pytest.raises(ValueError):
        decode_scoped_authorization_batch_v3(batch.model_copy(update=update(batch)))  # type: ignore[operator]


def test_rejects_rebound_outer_identity_command_or_authentication() -> None:
    batch = _batch(_issuance())
    altered_identity = batch.identity.model_copy(update={"command_id": "other-command"})
    wrong_command = batch.command.model_copy(update={"fingerprint": "0" * 64})
    wrong_authentication = batch.authentication.model_copy(
        update={"applicability_fingerprint": "0" * 64}
    )

    for altered in (
        batch.model_copy(update={"operation": "effects.authorize"}),
        batch.model_copy(update={"identity": altered_identity}),
        batch.model_copy(update={"command": wrong_command}),
        batch.model_copy(update={"authentication": wrong_authentication}),
    ):
        with pytest.raises(ValueError):
            decode_scoped_authorization_batch_v3(altered)


def test_rejects_the_synthetic_v3_marker_without_the_closed_issuance_pair() -> None:
    batch = _batch(_issuance())
    marker = _canonical({"schema_id": SCHEMA})
    authentication = batch.authentication.model_copy(
        update={
            "applicability_bytes": marker,
            "applicability_fingerprint": _digest(marker),
        }
    )

    with pytest.raises(ValueError):
        decode_scoped_authorization_batch_v3(
            batch.model_copy(update={"authentication": authentication})
        )


def test_exact_scoped_authorization_pair_is_structural_history_not_v2_member() -> None:
    cut, journal = _history(_batch(_issuance()))

    verified = verify_dispatch_history(cut, journal)

    assert verified.members == ()
    assert verified.v2_records == ()


@pytest.mark.parametrize(
    "records",
    (
        lambda batch: batch.complete_records[:1],
        lambda batch: tuple(reversed(batch.complete_records)),
        lambda batch: batch.complete_records + batch.complete_records[:1],
        lambda batch: (
            batch.complete_records[0].model_copy(
                update={"schema_id": "chiplog.effects.scoped-authorization-unknown.v3"}
            ),
            batch.complete_records[1],
        ),
    ),
)
def test_history_rejects_orphan_reordered_extra_and_unknown_authorization_rows(
    records: object,
) -> None:
    batch = _batch(_issuance())
    altered = batch.model_copy(update={"complete_records": records(batch)})  # type: ignore[operator]
    cut, journal = _history(altered)

    with pytest.raises(ValueError, match="scoped authorization V3 batch"):
        verify_dispatch_history(cut, journal)


def test_history_rejects_authorization_rows_split_across_selected_decisions() -> None:
    complete = _batch(_issuance())
    first = complete.model_copy(update={"complete_records": complete.complete_records[:1]})
    second = complete.model_copy(update={"complete_records": complete.complete_records[1:]})
    cut, initial = _history(first)
    other = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=second,
            issuance_id="issuance-2",
            fence_generation="generation",
            fence_frontier=1,
            predecessor_commitment="4" * 64,
        ),
        decision_id="decision-2",
        decision_head="decision-2/head",
        decision_fingerprint="5" * 64,
        resulting_commitment="6" * 64,
        tenant_commit_sequence=2,
    )
    journal = OwnerJournalSnapshot(
        initial.tenant_id, initial.head, (*initial.decisions, other), frozenset()
    )

    with pytest.raises(ValueError, match="scoped authorization V3 batch"):
        verify_dispatch_history(cut, journal)


def test_history_rejects_pair_with_sql_order_metadata_substituted() -> None:
    batch = _batch(_issuance())
    cut, journal = _history(batch)
    first, second = cut.rows
    altered_cut = replace(
        cut,
        rows=(first, StoredEffectRow(1, 0, first.batch_record_ids, second.record)),
    )

    with pytest.raises(ValueError, match="SQL membership or order"):
        verify_dispatch_history(altered_cut, journal)


def test_h1_holds_a_valid_authorization_pair_without_runtime() -> None:
    cut, journal = _history(_batch(_issuance()))

    with pytest.raises(H1ProducerHistoryHold, match="without authenticated runtime"):
        interpret_h1_producer_effects_history(cut, journal)


def test_h1_rejects_malformed_authorization_pair_before_holding() -> None:
    original = _batch(_issuance())
    batch = original.model_copy(update={"complete_records": original.complete_records[:1]})
    cut, journal = _history(batch)

    with pytest.raises(H1ProducerHistoryError, match="pair is malformed"):
        interpret_h1_producer_effects_history(cut, journal)


def test_startup_rejects_pair_until_historical_binder_is_installed() -> None:
    batch = _batch(_issuance())
    _, journal = _history(batch)
    runtime = type(
        "Runtime",
        (),
        {"_owner_decisions": lambda self: type("Journal", (), {"snapshot": lambda _: journal})()},
    )()

    with pytest.raises(ValueError, match="installed historical binder"):
        validate_selected_dispatch_sources(runtime)  # type: ignore[arg-type]


def test_historical_binder_uses_raw_predecessor_locator_not_retained_digest() -> None:
    """The retained ExactHead identifies a raw predecessor ID, never a head digest."""
    issuance = _issuance()
    batch = _batch(issuance)
    _, journal = _history(batch)
    decision = replace(
        journal.decisions[0],
        decision_fingerprint=_digest(b"selected"),
        decision_head="decision",
    )
    cut = issuance.original_cut.current.observation.cut.model_copy(
        update={"selected_journal_head": reference("owner-journal", _canonical("predecessor"))}
    )
    observation = issuance.original_cut.current.observation.model_copy(update={"cut": cut})
    current = issuance.original_cut.current.model_copy(update={"observation": observation})
    fixed = issuance.model_copy(
        update={"original_cut": issuance.original_cut.model_copy(update={"current": current})}
    )
    calls: list[str | None] = []
    predecessor = OwnerJournalSnapshot(fixed.identity.tenant_id, "predecessor", (), frozenset())
    owner_journal = type(
        "OwnerJournal",
        (),
        {
            "_raw": type(
                "Raw",
                (),
                {
                    "entries": lambda _: (
                        ("predecessor", None, b"predecessor"),
                        ("decision", "predecessor", b"selected"),
                    )
                },
            )(),
            "snapshot_at": lambda _, head: (calls.append(head), predecessor)[1],
        },
    )()
    runtime = object.__new__(CommonCliExecutionRuntime)
    runtime._owner_decisions = lambda: owner_journal  # type: ignore[method-assign]
    binder = object.__new__(H1ScopedAuthorizationHistoricalBinder)
    binder._runtime = runtime

    assert binder._authenticated_predecessor(decision, fixed) == ("predecessor", predecessor)
    assert calls == ["predecessor"]


def test_historical_binder_rejects_digest_as_snapshot_locator() -> None:
    issuance = _issuance()
    batch = _batch(issuance)
    _, journal = _history(batch)
    decision = replace(
        journal.decisions[0],
        decision_fingerprint=_digest(b"selected"),
        decision_head="decision",
    )
    owner_journal = type(
        "OwnerJournal",
        (),
        {
            "_raw": type(
                "Raw",
                (),
                {"entries": lambda _: (("decision", None, b"selected"),)},
            )(),
            "snapshot_at": lambda _, _head: pytest.fail("must reject before snapshot_at"),
        },
    )()
    runtime = object.__new__(CommonCliExecutionRuntime)
    runtime._owner_decisions = lambda: owner_journal  # type: ignore[method-assign]
    binder = object.__new__(H1ScopedAuthorizationHistoricalBinder)
    binder._runtime = runtime

    with pytest.raises(
        ScopedAuthorizationHistoricalBindError, match="retained journal predecessor"
    ):
        binder._authenticated_predecessor(decision, issuance)
