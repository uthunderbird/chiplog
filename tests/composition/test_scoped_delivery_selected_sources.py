"""Focused fail-closed checks for the selected scoped V3 historical reader."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV3
from chiplog.composition.h1_historical_selected_sources import H1HistoricalSelection
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.composition.scoped_delivery_selected_sources import ScopedDeliverySelectedSources
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2, OwnerRecordBytes
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import scoped_resources, write_scoped_pins
from tests.support.h1_v3_recovery import (
    _issue_reconstructed_grant,
    _recover_b_prefix,
    _selected_scoped_root,
)
from tests.support.scoped_intent_records import delivery_publication_fixture


class _Gate:
    held = False

    @contextmanager
    def hold(self):
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def require_held(self) -> None:
        if not self.held:
            raise RuntimeError("gate is not held")


def _selected(record: OwnerRecordBytes) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot]:
    batch = CompleteDeliveryBatchV2.model_construct(
        identity=SimpleNamespace(command_id="selected-v3-command"),
        complete_records=(record,),
    )
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
    return (
        MaterializedEffectsCut(
            tenant_id="tenant",
            tenant_frontier=1,
            materialization_commitment="3" * 64,
            owner_journal_head="journal-head",
            physical_path="/structural",
            physical_device=0,
            physical_inode=0,
            rows=(StoredEffectRow(1, 0, (record.record_id,), record),),
            worker=None,
            latest_runs=(),
        ),
        OwnerJournalSnapshot("tenant", "journal-head", (decision,), frozenset()),
    )


@pytest.mark.asyncio
async def test_selected_v3_intent_returns_authenticated_absent_first_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader uses its own complete cut and returns inert absence evidence."""
    import chiplog.composition.scoped_delivery_selected_sources as subject

    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    record = OwnerRecordBytes(
        owner="effects",
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    cut, journal = _selected(record)
    issuance = H1CompletionIssuanceV3.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    gate = _Gate()

    class Runtime:
        def _authority_gate(self) -> _Gate:
            return gate

        def _owner_decisions(self) -> object:
            return object()

    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", Runtime)
    monkeypatch.setattr(
        subject, "_read_materialized_effects_with_history", lambda *_: (cut, journal)
    )
    monkeypatch.setattr(subject, "verify_dispatch_history", lambda *_: None)
    monkeypatch.setattr(
        subject,
        "bind_selected_h1_completion",
        lambda *_: H1HistoricalSelection(journal.decisions[0], issuance),
    )

    result = subject.ScopedDeliverySelectedSources(Runtime()).read(
        intent_id=fixture.intent.intent_id, act_id="future-first-send"
    )

    assert result.intent.canonical_record_bytes == member.canonical_record_bytes
    assert result.selected_h1.issuance is issuance
    assert result.first_send.kind == "FIRST_SEND_ABSENT"
    assert result.first_send.intent_id == fixture.intent.intent_id
    assert result.first_send.act_id == "future-first-send"
    assert result.frontier.owner_journal_head == journal.head
    assert not gate.held


@pytest.mark.asyncio
async def test_send_like_selected_member_holds_instead_of_proving_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed future SEND member tied to the intent cannot be ignored."""
    import chiplog.composition.scoped_delivery_selected_sources as subject

    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    intent_record = OwnerRecordBytes(
        owner="effects",
        record_kind=member.record_kind,
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    send_bytes = b"{}"
    future_send = OwnerRecordBytes(
        owner="effects",
        record_kind="ScopedFirstSend",
        record_id="future-send",
        schema_id="chiplog.effects.scoped-first-send-decision.v3",
        canonical_bytes=send_bytes,
        fingerprint=hashlib.sha256(send_bytes).hexdigest(),
    )
    cut, journal = _selected(intent_record)
    batch = journal.decisions[0].prepared.request
    object.__setattr__(batch, "complete_records", (intent_record, future_send))
    issuance = H1CompletionIssuanceV3.model_construct(
        effects_result=SimpleNamespace(intent_result=fixture.result)
    )
    gate = _Gate()

    class Runtime:
        def _authority_gate(self) -> _Gate:
            return gate

        def _owner_decisions(self) -> object:
            return object()

    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", Runtime)
    monkeypatch.setattr(
        subject, "_read_materialized_effects_with_history", lambda *_: (cut, journal)
    )
    monkeypatch.setattr(subject, "verify_dispatch_history", lambda *_: None)
    monkeypatch.setattr(
        subject,
        "bind_selected_h1_completion",
        lambda *_: H1HistoricalSelection(journal.decisions[0], issuance),
    )

    with pytest.raises(subject.ScopedDeliverySelectedSourcesError, match="first-SEND material"):
        subject.ScopedDeliverySelectedSources(Runtime()).read(intent_id=fixture.intent.intent_id)


@pytest.mark.asyncio
async def test_mounted_selected_v3_reader_binds_journal_sql_and_reopens(tmp_path: Path) -> None:
    """One mounted fixture proves the reader has no caller-supplied historical cut."""
    from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV3
    from chiplog.composition.h1_historical_selected_sources import bind_selected_h1_completion
    from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch

    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            request, locator, state = await _selected_scoped_root(runtime)
            state = await _recover_b_prefix(runtime, request, locator, state)
            await _issue_reconstructed_grant(runtime, request, locator)
            completed = await runtime._h1_postseal_recovery_coordinator.resume_selected(
                request.identity, request.original_driver_command_fingerprint(), locator
            )
            assert len(completed.inputs) == len(completed.results) == 4
            assert (
                await runtime.finalize_execution(
                    request.identity, request.original_driver_command_fingerprint()
                )
            ).disposition == "COMMITTED"

            decision = next(
                item
                for item in runtime._owner_decisions().snapshot().decisions
                if type(item.prepared.request) is CompleteDeliveryBatchV2
            )
            batch = decision.prepared.request
            historical = bind_selected_h1_completion(batch, runtime)
            assert type(historical.issuance) is H1CompletionIssuanceV3
            expected_member = make_scoped_intent_member(
                historical.issuance.effects_result.intent_result.intent
            )
            result = ScopedDeliverySelectedSources(runtime).read(
                intent_id=expected_member.record_id
            )
            assert result.selected_h1 == historical
            assert result.intent.canonical_record_bytes == expected_member.canonical_record_bytes
            assert result.first_send.kind == "FIRST_SEND_ABSENT"

            with sqlite3.connect(runtime._database) as connection:
                physical = connection.execute(
                    "SELECT owner, schema_id, canonical_bytes, commit_sequence FROM main.records "
                    "WHERE tenant_id = ? AND record_id = ?",
                    (runtime._tenant_id, expected_member.record_id),
                ).fetchone()
            assert physical == (
                "effects",
                expected_member.schema_id,
                expected_member.canonical_record_bytes,
                decision.tenant_commit_sequence,
            )

    with _open_installed_h1_launch(slot) as reopened_launch:
        async with open_installed_h1_scoped_runtime(
            reopened_launch, resources=scoped_resources(tmp_path)
        ) as reopened:
            replay = ScopedDeliverySelectedSources(reopened).read(
                intent_id=expected_member.record_id
            )
            assert replay.intent.canonical_record_bytes == expected_member.canonical_record_bytes
