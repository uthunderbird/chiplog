"""Pure execution pre-accept cancellation production; no runtime mounting claim."""

from __future__ import annotations

import hashlib
import json

import pytest

from chiplog.composition.r14_execution_cancellation import (
    build_execution_cancellation_envelope,
    execution_cancellation_command,
)
from chiplog.composition.r14_execution_cancellation_contracts import (
    RetainedExecutionCancellationPreparation,
)
from chiplog.composition.r14_execution_fanout_records import reference
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from tests.support.execution_cancellation import retained_shape


def _proposal_fingerprint(value: object) -> str:
    assert hasattr(value, "canonical_bytes")
    body = json.loads(value.canonical_bytes())
    del body["proposal_fingerprint"]
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


async def _retained_execution_cancellation() -> RetainedExecutionCancellationPreparation:
    """Make a complete frozen owner exchange around the executable Run fixture."""
    retained = await retained_shape()
    request = retained.request
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
        CancelledBeforeAcceptRecord,
        NotExecutedCallResultRecord,
        PreparedPreAcceptCancellation,
    )

    terminal_record = CancelledBeforeAcceptRecord(
        terminal_id="cancelled:" + request.digest(),
        source_command_id=request.command_id,
        original_call_id=request.original_call_id,
        original=request.original,
        initialized=request.initialized,
        cancellation_act=request.cancellation_act,
        cut=request.cut,
        not_executed_result_id="not-executed:" + request.digest(),
    )
    result = NotExecutedCallResultRecord(
        result_id=terminal_record.not_executed_result_id,
        original_call_id=request.original_call_id,
        initialized=request.initialized,
        terminal=reference(terminal_record.terminal_id, terminal_record),
        outcome="NOT_EXECUTED",
    )
    proposal = PreparedPreAcceptCancellation(
        source_request_fingerprint=request.digest(),
        terminal=terminal_record,
        result=result,
        complete_ordered_record_manifest=(
            reference(terminal_record.terminal_id, terminal_record),
            reference(result.result_id, result),
        ),
        proposal_fingerprint="0" * 64,
    )
    proposal = proposal.model_copy(update={"proposal_fingerprint": _proposal_fingerprint(proposal)})
    owner_request = PublicPortCall(
        operation_id="agent_loop.prepare_pre_accept_cancellation",
        request_id="execution-cancellation-owner-request",
        caller=retained.owner_request.caller,
        callee=retained.owner_request.callee,
        schema_id="chiplog.call.cancellation-preparation.v1",
        canonical_payload=request.canonical_bytes(),
        budget=retained.owner_request.budget,
    )
    owner_response = PublicPortSuccess(
        request_id=owner_request.request_id,
        responder=owner_request.callee,
        schema_id="chiplog.call.preparation-result.v1",
        canonical_payload=proposal.canonical_bytes(),
    )
    return retained.model_copy(
        update={
            "proposal": proposal,
            "owner_request": owner_request,
            "owner_response": owner_response,
        }
    )


async def test_execution_cancel_producer_derives_exact_two_record_envelope() -> None:
    retained = await _retained_execution_cancellation()

    envelope = build_execution_cancellation_envelope(retained)
    command = execution_cancellation_command(retained)

    assert tuple(member.record_id for member in envelope.records) == (
        retained.proposal.terminal.terminal_id,
        retained.proposal.result.result_id,
    )
    assert tuple(member.schema_id for member in envelope.records) == (
        "chiplog.call.cancelled-before-accept.v1",
        "chiplog.call.not-executed-result.v1",
    )
    assert tuple(record.canonical_bytes for record in command.records) == (
        retained.proposal.terminal.canonical_bytes(),
        retained.proposal.result.canonical_bytes(),
    )
    assert len(command.records) == 2
    assert retained.owner_request.canonical_payload == retained.request.canonical_bytes()
    assert retained.owner_response.canonical_payload == retained.proposal.canonical_bytes()


@pytest.mark.parametrize("mutation", ("owner", "request", "source", "proposal"))
async def test_execution_cancel_producer_rejects_substituted_owner_or_source_bytes(
    mutation: str,
) -> None:
    retained = await _retained_execution_cancellation()
    if mutation == "owner":
        retained = retained.model_copy(
            update={
                "owner_response": retained.owner_response.model_copy(update={"request_id": "other"})
            }
        )
    elif mutation == "request":
        retained = retained.model_copy(
            update={
                "owner_request": retained.owner_request.model_copy(
                    update={"canonical_payload": b"{}"}
                )
            }
        )
    elif mutation == "source":
        source = retained.request.cut.sources[0].model_copy(
            update={"canonical_value_base64": "eyJzdWJzdGl0dXRlZCI6dHJ1ZX0="}
        )
        retained = retained.model_copy(
            update={
                "request": retained.request.model_copy(
                    update={
                        "cut": retained.request.cut.model_copy(
                            update={"sources": (source, *retained.request.cut.sources[1:])}
                        )
                    }
                )
            }
        )
    else:
        terminal = retained.proposal.terminal.model_copy(update={"terminal_id": "other-terminal"})
        retained = retained.model_copy(
            update={"proposal": retained.proposal.model_copy(update={"terminal": terminal})}
        )

    with pytest.raises(ValueError):
        build_execution_cancellation_envelope(retained)
