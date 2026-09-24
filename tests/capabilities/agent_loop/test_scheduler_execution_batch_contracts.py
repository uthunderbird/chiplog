"""Consumers exercise complete nonempty scheduler V2 batch contract chains."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.scheduler_execution import present
from tests.support.scheduler_execution_batches import (
    finalize_request,
    finalized_result,
    finalized_result_with_two_non_envelope_members,
    preparation_outputs,
    prepare_request,
    streamed_overflow_finalized_result,
    zero_work_finalized_result,
)

import chiplog.capabilities.agent_loop.execution_initialization_contracts as v1_init
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ChargedScheduledIntervalAccountingV2,
    CompleteSchedulerExecutionDependencyManifestV2,
    FinalizedScheduledIntervalOutcomeV2,
    FinalizeScheduledIntervalV2,
    PreparedIntervalSeedBatchV2,
    PreparedScheduledExecutionInitializationV2,
    SafetyHoldNoDebitAccountingV2,
    ScheduledIntervalAccountingV2,
    ScheduledIntervalExactReplayV2,
    ScheduledIntervalPreparationResultV2,
    ScheduledWholeIntervalEnvelopeV2,
    SelectedExactDecisionV2,
    SelectedScheduledWholeEnvelopeV2,
)


@pytest.mark.parametrize("count", [1, 3])
def test_executable_one_and_n_runs_flow_from_ordered_create_to_finalized_result(count: int) -> None:
    request = prepare_request(count)
    assert TypeAdapter(type(request)).validate_json(request.canonical_bytes()) == request
    assert [command.run_id for command in request.ordered_create_commands] == [
        f"run-{index}" for index in range(count)
    ]

    outputs = preparation_outputs(count)
    assert (
        TypeAdapter(ScheduledIntervalPreparationResultV2).validate_json(outputs.canonical_bytes())
        == outputs
    )
    for expected, prepared in zip(
        request.ordered_create_commands, outputs.ordered_runs, strict=True
    ):
        assert prepared.command == expected
        assert prepared.initialization.run.schema_id == "chiplog.agent-loop.execution-record.v2"
        assert prepared.initialization.run.run_id == expected.run_id
        assert (
            prepared.initialization.scheduled_binding.service_identity
            != prepared.initialization.run.principal
        )

    finalize = finalize_request(count)
    assert (
        TypeAdapter(FinalizeScheduledIntervalV2).validate_json(finalize.canonical_bytes())
        == finalize
    )
    result = finalized_result(count)
    assert TypeAdapter(type(result)).validate_json(result.canonical_bytes()) == result
    assert result.expected_seed_count == count
    member = result.complete_ordered_canonical_records[0]
    raw = base64.b64decode(member.canonical_base64, validate=True)
    assert member.fingerprint == hashlib.sha256(raw).hexdigest()
    assert raw == result.outcome.canonical_bytes()
    assert (
        result.complete_ordered_record_bytes[-1] == result.whole_envelope.canonical_envelope_bytes
    )
    assert result.whole_envelope.proposed_record_id == result.whole_envelope.external_reference.head


def test_zero_work_and_overflow_accounting_roundtrip() -> None:
    ordinary = finalize_request(0)
    charged_overflow = finalize_request(0, overflow=True)
    no_debit_overflow = finalize_request(0, overflow=True, no_debit=True)
    for request in (ordinary, charged_overflow, no_debit_overflow):
        seed: PreparedIntervalSeedBatchV2 = TypeAdapter(PreparedIntervalSeedBatchV2).validate_json(
            request.seed_batch.canonical_bytes()
        )
        accounting: ScheduledIntervalAccountingV2 = TypeAdapter(
            ScheduledIntervalAccountingV2
        ).validate_json(request.accounting.canonical_bytes())
        assert seed == request.seed_batch
        assert accounting == request.accounting
        assert (
            TypeAdapter(FinalizeScheduledIntervalV2).validate_json(request.canonical_bytes())
            == request
        )
    assert isinstance(ordinary.accounting, ChargedScheduledIntervalAccountingV2)
    assert isinstance(charged_overflow.accounting, ChargedScheduledIntervalAccountingV2)
    assert isinstance(no_debit_overflow.accounting, SafetyHoldNoDebitAccountingV2)
    assert ordinary.accounting.proposed_consumption.basis.delta_runs == 0
    assert charged_overflow.accounting.proposed_consumption.basis.delta_runs == 0
    assert no_debit_overflow.accounting.deltas == (0, 0, 0)


def test_finalized_outcomes_preserve_records_and_overflow_is_empty() -> None:
    ordinary = finalized_result(1)
    overflow = finalized_result(0, overflow=True)
    for result in (ordinary, overflow):
        assert (
            TypeAdapter(FinalizedScheduledIntervalOutcomeV2).validate_json(
                result.outcome.canonical_bytes()
            )
            == result.outcome
        )
        member = result.complete_ordered_canonical_records[0]
        raw = base64.b64decode(member.canonical_base64, validate=True)
        assert member.fingerprint == hashlib.sha256(raw).hexdigest()
        assert raw == result.outcome.canonical_bytes()
    assert overflow.seed_kind == "OVERFLOW"
    assert overflow.finalized_occurrences == ()
    assert overflow.expected_seed_count == 0


def test_prepare_finalize_reject_order_count_and_run_substitution() -> None:
    request = prepare_request(3)
    wire = json.loads(request.canonical_bytes())
    wire["ordered_create_commands"].reverse()
    with pytest.raises(ValidationError, match="ordered scheduled seed"):
        type(request).model_validate_json(json.dumps(wire))
    wire = json.loads(request.canonical_bytes())
    wire["ordered_create_commands"].pop()
    with pytest.raises(ValidationError, match="ordered scheduled seed"):
        type(request).model_validate_json(json.dumps(wire))

    finalize = finalize_request(3)
    wire = json.loads(finalize.canonical_bytes())
    wire["ordered_prepared_runs"][1]["command"]["run_id"] = "run-substituted"
    with pytest.raises(ValidationError):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))
    wire = json.loads(finalize.canonical_bytes())
    wire["ordered_prepared_runs"].pop()
    with pytest.raises(ValidationError, match="exact ordered seed"):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))


def test_ordinary_rejects_safety_and_overflow_rejects_prepared_runs() -> None:
    ordinary = finalize_request(1)
    wire = json.loads(ordinary.canonical_bytes())
    wire["accounting"] = json.loads(
        finalize_request(0, overflow=True, no_debit=True).accounting.canonical_bytes()
    )
    wire["dependency_manifest"] = json.loads(
        finalize_request(0, overflow=True, no_debit=True).dependency_manifest.canonical_bytes()
    )
    with pytest.raises(ValidationError, match="ordinary seed batch requires charged"):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))

    overflow = finalize_request(0, overflow=True)
    wire = json.loads(overflow.canonical_bytes())
    wire["ordered_prepared_runs"] = json.loads(finalize_request(1).canonical_bytes())[
        "ordered_prepared_runs"
    ]
    with pytest.raises(ValidationError, match="exact ordered seed"):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))


@pytest.mark.parametrize(
    "finalization",
    [
        finalize_request(1),
        finalize_request(0, overflow=True),
        finalize_request(0, overflow=True, no_debit=True),
    ],
)
def test_registered_dag_branch_and_budget_edges_are_exact(
    finalization: FinalizeScheduledIntervalV2,
) -> None:
    wire = json.loads(finalization.canonical_bytes())
    wire["dependency_manifest"]["branch"] = (
        "OVERFLOW_CHARGED"
        if finalization.dependency_manifest.branch == "OVERFLOW_SAFETY_NO_DEBIT"
        else "OVERFLOW_SAFETY_NO_DEBIT"
    )
    with pytest.raises(ValidationError):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))

    wire = json.loads(finalization.canonical_bytes())
    wire["dependency_manifest"]["registered_edges"] = wire["dependency_manifest"][
        "registered_edges"
    ][:-1]
    with pytest.raises(ValidationError, match="exact registered edge"):
        CompleteSchedulerExecutionDependencyManifestV2.model_validate_json(
            json.dumps(wire["dependency_manifest"])
        )
    wire = json.loads(finalization.canonical_bytes())
    wire["dependency_manifest"]["registered_edges"][-1][1].append("whole_envelope")
    with pytest.raises(ValidationError, match="exact registered edge"):
        CompleteSchedulerExecutionDependencyManifestV2.model_validate_json(
            json.dumps(wire["dependency_manifest"])
        )


def test_budget_predecessor_and_deltas_cannot_be_substituted() -> None:
    request = finalize_request(1)
    wire = json.loads(request.canonical_bytes())
    wire["accounting"]["proposed_consumption"]["basis"]["delta_runs"] = 2
    with pytest.raises(ValidationError, match=r"exact \(1, N, 0\)"):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))
    wire = json.loads(request.canonical_bytes())
    consumption = wire["accounting"]["proposed_consumption"]
    consumption["selected_predecessor"]["budget_head"]["head"] = "other"
    consumption["basis"]["selected_predecessor"]["budget_head"]["head"] = "other"
    consumption["successor"]["budget"]["consumption_basis"]["selected_predecessor"]["budget_head"][
        "head"
    ] = "other"
    with pytest.raises(ValidationError, match="seed selected budget predecessor"):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))


def test_selected_replay_retains_original_debit_bytes_and_v1_wire_is_closed() -> None:
    selected = SelectedExactDecisionV2(
        selected_decision=present("decision"),
        canonical_selected_decision_bytes=b"decision-v1-original",
        canonical_selected_result_bytes=b"result-v1-original",
        exact_prefix_materialization=present("prefix"),
        selected_debit=present("debit"),
        canonical_selected_debit_bytes=b"debit-v1-original",
    )
    finalized = finalized_result(1)
    replay = ScheduledIntervalExactReplayV2(
        source=selected,
        complete_selected_records=finalized.complete_ordered_canonical_records,
        complete_selected_member_subject_ids=finalized.complete_ordered_member_subject_ids,
        selected_whole_envelope=SelectedScheduledWholeEnvelopeV2(
            selected_reference=finalized.whole_envelope.external_reference,
            canonical_envelope_bytes=finalized.whole_envelope.canonical_envelope_bytes,
            body=finalized.whole_envelope.body,
        ),
    )
    restored: ScheduledIntervalPreparationResultV2 = TypeAdapter(
        ScheduledIntervalPreparationResultV2
    ).validate_json(replay.canonical_bytes())
    assert isinstance(restored, ScheduledIntervalExactReplayV2)
    assert restored.source.canonical_selected_debit_bytes == b"debit-v1-original"
    with pytest.raises(ValidationError):
        SelectedExactDecisionV2.model_validate({**selected.model_dump(), "unknown": "field"})
    with pytest.raises(ValidationError):
        v1_init.PreparedExecutionInitialization.model_validate(
            PreparedScheduledExecutionInitializationV2.model_validate(
                preparation_outputs(1).ordered_runs[0].initialization.model_dump()
            ).model_dump()
        )


def test_finalize_rejects_injected_output_and_overflow_nonzero() -> None:
    request = finalize_request(1)
    wire = json.loads(request.canonical_bytes())
    wire["outcome"] = json.loads(finalized_result(1).outcome.canonical_bytes())
    with pytest.raises(ValidationError):
        FinalizeScheduledIntervalV2.model_validate_json(json.dumps(wire))

    output = finalized_result(0, overflow=True)
    wire = json.loads(output.canonical_bytes())
    wire["expected_seed_count"] = 1
    wire["finalized_occurrences"] = json.loads(finalized_result(1).canonical_bytes())[
        "finalized_occurrences"
    ]
    with pytest.raises(ValidationError, match="overflow finalization"):
        type(output).model_validate_json(json.dumps(wire))


def test_whole_membership_rejects_changed_or_reordered_members() -> None:
    result = finalized_result_with_two_non_envelope_members()

    wire = result.model_dump()
    wire["complete_ordered_record_bytes"] = (
        b"changed-non-envelope-record",
        *wire["complete_ordered_record_bytes"][1:],
    )
    with pytest.raises(ValidationError, match="member bytes differ"):
        type(result).model_validate(wire)

    wire = result.model_dump()
    first, second, envelope = wire["complete_ordered_record_bytes"]
    wire["complete_ordered_record_bytes"] = (second, first, envelope)
    with pytest.raises(ValidationError, match="member bytes differ"):
        type(result).model_validate(wire)

    wire = result.model_dump()
    descriptors = wire["whole_envelope"]["body"]["ordered_non_envelope_members"]
    wire["whole_envelope"]["body"]["ordered_non_envelope_members"] = tuple(reversed(descriptors))
    reordered_body = ScheduledWholeIntervalEnvelopeV2.model_validate(wire["whole_envelope"]["body"])
    reordered_bytes = reordered_body.canonical_bytes()
    wire["whole_envelope"]["canonical_envelope_bytes"] = reordered_bytes
    wire["whole_envelope"]["external_reference"]["fingerprint"] = hashlib.sha256(
        reordered_bytes
    ).hexdigest()
    wire["complete_ordered_record_bytes"] = (
        *wire["complete_ordered_record_bytes"][:-1],
        reordered_bytes,
    )
    with pytest.raises(ValidationError, match="body descriptor differs"):
        type(result).model_validate(wire)


def test_selected_replay_rejects_reordered_members_against_selected_whole() -> None:
    result = finalized_result_with_two_non_envelope_members()
    selected_whole = SelectedScheduledWholeEnvelopeV2(
        selected_reference=result.whole_envelope.external_reference,
        canonical_envelope_bytes=result.whole_envelope.canonical_envelope_bytes,
        body=result.whole_envelope.body,
    )
    with pytest.raises(ValidationError, match="selected WHOLE descriptor differs"):
        ScheduledIntervalExactReplayV2(
            source=SelectedExactDecisionV2(
                selected_decision=present("decision"),
                canonical_selected_decision_bytes=b"decision-v1-original",
                canonical_selected_result_bytes=b"result-v1-original",
                exact_prefix_materialization=present("prefix"),
                selected_debit=present("debit"),
                canonical_selected_debit_bytes=b"debit-v1-original",
            ),
            complete_selected_records=tuple(reversed(result.complete_ordered_canonical_records)),
            complete_selected_member_subject_ids=tuple(
                reversed(result.complete_ordered_member_subject_ids)
            ),
            selected_whole_envelope=selected_whole,
        )


def _refresh_proposed_whole_bytes(wire: dict[str, object]) -> None:
    whole = wire["whole_envelope"]
    assert isinstance(whole, dict)
    body_wire = whole["body"]
    assert isinstance(body_wire, dict)
    body = ScheduledWholeIntervalEnvelopeV2.model_validate(body_wire)
    canonical_bytes = body.canonical_bytes()
    whole["canonical_envelope_bytes"] = canonical_bytes
    reference = whole["external_reference"]
    assert isinstance(reference, dict)
    reference["fingerprint"] = hashlib.sha256(canonical_bytes).hexdigest()
    record_bytes = wire["complete_ordered_record_bytes"]
    assert isinstance(record_bytes, tuple)
    wire["complete_ordered_record_bytes"] = (*record_bytes[:-1], canonical_bytes)


def test_whole_ordinary_boundary_exactly_matches_zero_work_result() -> None:
    result = zero_work_finalized_result()
    assert type(result).model_validate(result.model_dump()) == result

    wire = result.model_dump()
    whole = wire["whole_envelope"]
    assert isinstance(whole, dict)
    body = whole["body"]
    assert isinstance(body, dict)
    body["resulting_boundary"] = "different-boundary"
    _refresh_proposed_whole_bytes(wire)
    with pytest.raises(ValidationError, match="WHOLE branch or outcome differs"):
        type(result).model_validate(wire)


def test_whole_overflow_primitive_exactly_matches_streamed_hold() -> None:
    result = streamed_overflow_finalized_result()
    assert type(result).model_validate(result.model_dump()) == result

    wire = result.model_dump()
    whole = wire["whole_envelope"]
    assert isinstance(whole, dict)
    body = whole["body"]
    assert isinstance(body, dict)
    body["primitive_reference"] = present("different-overflow").model_dump()
    _refresh_proposed_whole_bytes(wire)
    with pytest.raises(ValidationError, match="WHOLE branch or outcome differs"):
        type(result).model_validate(wire)
