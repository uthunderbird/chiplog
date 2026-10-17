"""Selected cancelled-call evidence rendered into the following Turn."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    OriginalCallKey,
)
from chiplog.capabilities.agent_loop.execution_next_turn_preparation import (
    prepare_next_native_execution_turn,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition import r14_next_turn_records
from chiplog.composition.r14_cancellation_contracts import (
    CANCELLATION_SCHEMA,
    NOT_EXECUTED_SCHEMA,
)
from tests.capabilities.agent_loop.test_execution_next_turn_preparation import _request
from tests.support.execution_cancellation import retained_shape


def _source(record, identity: str, schema: str, decision: CallSubjectHead):
    raw = record.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    reference = CallSubjectHead(
        subject_id=identity,
        revision=Present(head=f"record:{identity}", fingerprint=digest),
    )
    return reference, RecoverySourceRecord(
        owner="agent_loop",
        subject=reference,
        schema_id=schema,
        canonical_record_bytes=raw,
        selected_decision=decision,
        physical_record=reference,
    )


@pytest.mark.asyncio
async def test_cancelled_prior_turn_context_requires_selected_terminal_result_join(monkeypatch):
    prepared = await _request()
    before = prepared.run
    next_turn = prepare_next_native_execution_turn(prepared).run
    response = prepared.cut.complete_ordered_responses[0]
    call = response.ordered_calls[0]
    initialized = response.seal.complete_ordered_initialized[0]
    original = OriginalCallKey(
        tenant_id=before.tenant,
        original_run_id=before.run_id,
        original_turn_id=before.turns[0].turn_id,
        captured_response=response.seal.captured_response,
        ordinal=0,
        model_call_label="cancelled-call",
    )

    cancellation = await retained_shape()
    terminal = cancellation.proposal.terminal.model_copy(
        update={
            "terminal_id": "terminal:selected-cancel",
            "original_call_id": call.original_call_id,
            "original": original,
            "initialized": initialized,
            "not_executed_result_id": "result:selected-cancel",
        }
    )
    terminal_ref, terminal_source = _source(
        terminal,
        terminal.terminal_id,
        CANCELLATION_SCHEMA,
        CallSubjectHead(
            subject_id="decision:next-turn",
            revision=Present(head="decision:next-turn", fingerprint="1" * 64),
        ),
    )
    result = cancellation.proposal.result.model_copy(
        update={
            "result_id": terminal.not_executed_result_id,
            "original_call_id": call.original_call_id,
            "initialized": initialized,
            "terminal": terminal_ref,
        }
    )
    result_ref, result_source = _source(
        result, result.result_id, NOT_EXECUTED_SCHEMA, terminal_source.selected_decision
    )
    sources = (
        *(
            source
            for source in prepared.cut.complete_sources
            if source.schema_id not in {CANCELLATION_SCHEMA, NOT_EXECUTED_SCHEMA}
        ),
        terminal_source,
        result_source,
    )
    selected_call = call.model_copy(
        update={
            "disposition": call.disposition.model_copy(
                update={
                    "terminal": terminal_ref.revision,
                    "not_executed_result": result_ref.revision,
                }
            )
        }
    )
    selected_response = response.model_copy(update={"ordered_calls": (selected_call,)})
    cut = prepared.cut.model_copy(
        update={"complete_ordered_responses": (selected_response,), "complete_sources": sources}
    )
    evidence = SimpleNamespace(
        request=SimpleNamespace(run=before, cut=cut),
        result=SimpleNamespace(run=next_turn),
    )

    class Decisions:
        def entries(self):
            return (
                (
                    "decision:next-turn",
                    0,
                    json.dumps({"kind": "DECIDED", "execution_next_turn_evidence": "{}"}),
                ),
            )

    runtime = SimpleNamespace(_loop_decisions=lambda: Decisions())
    monkeypatch.setattr(r14_next_turn_records, "retained_next_turn", lambda _entry: evidence)

    context = r14_next_turn_records.selected_prior_turn_context(runtime, next_turn)
    rendered = context[0]["results"][0]
    assert rendered["terminal"] == terminal.model_dump(mode="json")
    assert rendered["result"] == result.model_dump(mode="json")

    rival = CallSubjectHead(
        subject_id="terminal:rival",
        revision=Present(head="record:terminal:rival", fingerprint="2" * 64),
    )
    mismatched_result = result.model_copy(update={"terminal": rival})
    mismatched_ref, mismatched_source = _source(
        mismatched_result,
        mismatched_result.result_id,
        NOT_EXECUTED_SCHEMA,
        terminal_source.selected_decision,
    )
    bad_sources = (
        *[source for source in sources if source is not result_source],
        mismatched_source,
    )
    bad_call = selected_call.model_copy(
        update={
            "disposition": selected_call.disposition.model_copy(
                update={"not_executed_result": mismatched_ref.revision}
            )
        }
    )
    bad_response = selected_response.model_copy(update={"ordered_calls": (bad_call,)})
    bad_cut = cut.model_copy(
        update={"complete_ordered_responses": (bad_response,), "complete_sources": bad_sources}
    )
    evidence.request.cut = bad_cut
    with pytest.raises(ValueError, match="cancelled prior-turn terminal/result join differs"):
        r14_next_turn_records.selected_prior_turn_context(runtime, next_turn)
