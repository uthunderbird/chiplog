"""Closed V2 scheduler-execution consumer wires."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from typing import Literal

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.execution_versions import execution_run_v3
from tests.support.scheduler_execution import DIGEST, call_head, ordinary_seed_batch, present
from tests.support.scheduler_overflow_hold_records import v2_overflow_hold_record

from chiplog.capabilities.agent_loop import execution_initialization_contracts as v1_init
from chiplog.capabilities.agent_loop.execution_history_transition_contracts import (
    CreateExecutionRunV3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_contracts import StreamingEligibilityEvidence
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ORDINARY_CHARGED_EDGES_V2,
    OVERFLOW_SAFETY_EDGES_V2,
    CompleteSchedulerExecutionDependencyManifestV2,
    FinalizeScheduledIntervalV2,
    InitialRunAbsenceV2,
    OverflowHoldPrimitiveV2,
    PreparedFinalizedScheduledIntervalV2,
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedOverflowSeedBatchV2,
    PreparedPrimitiveFirstPublicationV2,
    PreparedScheduledExecutionInitializationV2,
    PreparedScheduledRunV2,
    PrepareScheduledIntervalExecutionsV2,
    ProposedScheduledWholeEnvelopeV2,
    SafetyHoldNoDebitAccountingV2,
    ScheduledExecutionBindingV2,
    ScheduledIntervalExactReplayV2,
    ScheduledIntervalPreparationResultV2,
    ScheduledOverflowIntervalExactReplayV2,
    ScheduledWholeIntervalEnvelopeV2,
    ScheduledWholeIntervalMemberDescriptorV2,
    SelectedExactDecisionV2,
    SelectedExactOverflowDecisionV2,
    SelectedScheduledWholeEnvelopeV2,
    mandate_is_fresh_at,
)
from chiplog.capabilities.agent_loop.scheduler_outcome_record_contracts import (
    ExecutableIntervalResultRecordV2,
    executable_interval_result_reference,
    make_executable_interval_result_member,
    make_executable_overflow_hold_member,
    make_scheduled_overflow_primitive_member,
    make_scheduled_parent_member,
)
from chiplog.capabilities.agent_loop.scheduler_overflow_hold_records import (
    executable_overflow_hold_reference,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    overflow_primitive_reference,
    primitive_parent_reference,
    scheduler_execution_command_reference,
)


@pytest.mark.parametrize("count", [0, 1, 3])
def test_ordinary_seed_batches_preserve_zero_one_or_n_subject_qualified_absences(
    count: int,
) -> None:
    batch = ordinary_seed_batch(count)
    assert type(batch).model_validate_json(batch.canonical_bytes()) == batch
    assert len(batch.occurrence_seeds) == count
    assert tuple(seed.initial_run_absence for seed in batch.occurrence_seeds) == (
        batch.cut.ordered_initial_run_absences
    )


def test_absence_fields_are_closed_and_mandate_keeps_zero_capacity_and_principal_split() -> None:
    batch = ordinary_seed_batch(1)
    absence = batch.cut.ordered_initial_run_absences[0].model_dump()
    del absence["subject"]
    with pytest.raises(ValidationError):
        InitialRunAbsenceV2.model_validate(absence)
    with pytest.raises(ValidationError):
        InitialRunAbsenceV2.model_validate(
            {**batch.cut.ordered_initial_run_absences[0].model_dump(), "unknown": "value"}
        )
    scope = batch.selected_mandate.mandate.scope.model_copy(
        update={
            "horizon": batch.selected_mandate.mandate.scope.horizon.model_copy(
                update={"max_cycles": 0, "max_runs": 0, "max_consequential_calls": 0}
            )
        }
    )
    assert type(scope).model_validate_json(scope.canonical_bytes()) == scope
    assert scope.service_identity != scope.beneficiary_principal
    assert mandate_is_fresh_at(scope.horizon, 99)
    assert not mandate_is_fresh_at(scope.horizon, 100)
    assert not mandate_is_fresh_at(scope.horizon, 101)


def overflow_batch() -> PreparedOverflowSeedBatchV2:
    ordinary = ordinary_seed_batch()
    assert isinstance(ordinary.source, PreparedPrimitiveFirstPublicationV2)
    primitive = OverflowHoldPrimitiveV2(
        command=call_head("overflow-command"),
        boundary=ordinary.source.primitive_parent.boundary,
        bound_head=ordinary.source.primitive_parent.current_bound,
        exceeded_dimension="MANIFEST_BYTES",
        actual_value=101,
        limit=100,
        evidence=StreamingEligibilityEvidence(
            manifest_digest=DIGEST,
            member_count=3,
            first_member=Absent(),
            last_member=Absent(),
            order_contract_version="order.v1",
            enumeration_completeness_proof=present("enumeration"),
        ),
    )
    return PreparedOverflowSeedBatchV2(
        configuration_source=ordinary.configuration_source,
        source=PreparedOverflowPrimitiveFirstPublicationV2(
            overflow_primitive=primitive,
            overflow_primitive_reference=present("overflow"),
            canonical_overflow_primitive_bytes=primitive.canonical_bytes(),
        ),
        cut=ordinary.cut,
        selected_mandate=ordinary.selected_mandate,
        selected_budget_predecessor=ordinary.selected_budget_predecessor,
        occurrence_seeds=(),
        skipped_dispositions=(),
    )


def test_streaming_overflow_source_has_no_ordinary_parent_anywhere() -> None:
    batch = overflow_batch()
    assert isinstance(batch.source, PreparedOverflowPrimitiveFirstPublicationV2)
    encoded = batch.canonical_bytes()
    assert b"primitive_parent" not in encoded
    assert batch.source.overflow_primitive.evidence.kind == "STREAMING_MANIFEST"
    malformed = json.loads(encoded)
    malformed["source"] = ordinary_seed_batch().source.model_dump(mode="json")
    with pytest.raises(ValidationError):
        PreparedOverflowSeedBatchV2.model_validate_json(json.dumps(malformed))


def test_seed_branch_and_overflow_cut_reject_valid_json_baseline_mutations() -> None:
    ordinary = ordinary_seed_batch()
    ordinary_wire = json.loads(ordinary.canonical_bytes())
    ordinary_wire["branch"] = "OVERFLOW_HOLD"
    with pytest.raises(ValidationError):
        type(ordinary).model_validate_json(json.dumps(ordinary_wire))

    overflow = overflow_batch()
    overflow_wire = json.loads(overflow.canonical_bytes())
    overflow_wire["cut"]["ordered_initial_run_absences"] = [
        ordinary_seed_batch(1).cut.ordered_initial_run_absences[0].model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="empty initial-Run absence"):
        PreparedOverflowSeedBatchV2.model_validate_json(json.dumps(overflow_wire))


def manifest(
    branch: Literal["ORDINARY_CHARGED", "OVERFLOW_CHARGED", "OVERFLOW_SAFETY_NO_DEBIT"],
    edges: tuple[tuple[str, tuple[str, ...]], ...],
) -> CompleteSchedulerExecutionDependencyManifestV2:
    return CompleteSchedulerExecutionDependencyManifestV2(branch=branch, registered_edges=edges)


def test_dependency_manifest_requires_exact_budget_branch_edges_without_backedge() -> None:
    value = manifest("ORDINARY_CHARGED", ORDINARY_CHARGED_EDGES_V2)
    assert ("budget_basis", ("selected_budget", "primitive_identity")) in value.registered_edges
    changed = list(ORDINARY_CHARGED_EDGES_V2)
    changed[-1] = ("whole_envelope", ("result",))
    with pytest.raises(ValidationError, match="exact registered edge"):
        manifest("ORDINARY_CHARGED", tuple(changed))
    with pytest.raises(ValidationError, match="exact registered edge"):
        manifest("ORDINARY_CHARGED", ORDINARY_CHARGED_EDGES_V2[:-1])


def test_finalize_request_is_input_only_and_result_carries_produced_members() -> None:
    overflow = overflow_batch()
    request = FinalizeScheduledIntervalV2(
        seed_batch=overflow,
        ordered_prepared_runs=(),
        accounting=SafetyHoldNoDebitAccountingV2(
            retained_selected_budget_predecessor=overflow.selected_budget_predecessor,
            proposed_successor=Absent(),
            delta_cycles=0,
            delta_runs=0,
            delta_consequential_calls=0,
        ),
        dependency_manifest=manifest("OVERFLOW_SAFETY_NO_DEBIT", OVERFLOW_SAFETY_EDGES_V2),
    )
    assert "outcome" not in type(request).model_fields
    assert PreparedFinalizedScheduledIntervalV2.model_fields["outcome"].is_required()


def test_strict_zero_rejects_python_and_json_booleans() -> None:
    overflow = overflow_batch()
    raw = SafetyHoldNoDebitAccountingV2(
        retained_selected_budget_predecessor=overflow.selected_budget_predecessor,
        proposed_successor=Absent(),
        delta_cycles=0,
        delta_runs=0,
        delta_consequential_calls=0,
    ).model_dump(mode="json")
    raw["delta_cycles"] = True
    with pytest.raises(ValidationError):
        SafetyHoldNoDebitAccountingV2.model_validate(raw)
    with pytest.raises(ValidationError):
        SafetyHoldNoDebitAccountingV2.model_validate_json(json.dumps(raw))


def test_selected_replay_retains_bytes_and_v2_initialization_does_not_widen_v1() -> None:
    replay = SelectedExactDecisionV2(
        selected_decision=present("decision"),
        canonical_selected_decision_bytes=b"decision",
        canonical_selected_result_bytes=b"result",
        exact_prefix_materialization=present("prefix"),
        selected_debit=Absent(),
        canonical_selected_debit_bytes=None,
    )
    assert replay.canonical_selected_result_bytes == b"result"
    assert "PREPARED_SCHEDULED_EXECUTION_INITIALIZATION_V2" not in str(
        v1_init.ExecutionInitializationResult
    )
    assert PreparedScheduledExecutionInitializationV2.model_fields["run"].is_required()


def test_scheduler_v3_create_and_prepared_run_roundtrip_and_version_mismatch_reject() -> None:
    run = execution_run_v3()
    batch = ordinary_seed_batch(1)
    command = CreateExecutionRunV3(
        command_id="create-v3",
        tenant=run.tenant,
        principal=run.principal,
        run_id=run.run_id,
        prompt=run.prompt,
        policy=run.policy,
        origin=run.origin,
        contour_head=run.contour_head,
        policy_head=run.policy_head,
        worker_session=run.worker_session,
    )
    prepared = PreparedScheduledRunV2(
        command=command,
        initialization=PreparedScheduledExecutionInitializationV2(
            source_request_fingerprint=DIGEST,
            run=run,
            scheduled_binding=ScheduledExecutionBindingV2(
                service_identity=batch.selected_mandate.mandate.scope.service_identity,
                mandate_head=batch.selected_mandate.mandate_head,
                primitive_parent=present("parent"),
                materialization=batch.occurrence_seeds[0].materialization,
                original_configuration=batch.configuration_source.configuration,
            ),
            proposal_fingerprint=DIGEST,
        ),
    )
    assert type(prepared).model_validate_json(prepared.canonical_bytes()) == prepared
    mutant = json.loads(prepared.canonical_bytes())
    mutant["command"]["kind"] = "CREATE_EXECUTION_RUN_V2"
    with pytest.raises(ValidationError, match="version differ"):
        PreparedScheduledRunV2.model_validate_json(json.dumps(mutant))

    request_command = command.model_copy(update={"run_id": "run-0"})
    request = PrepareScheduledIntervalExecutionsV2(
        seed_batch=batch, ordered_create_commands=(request_command,)
    )
    assert type(request).model_validate_json(request.canonical_bytes()) == request


def test_whole_envelope_body_and_proposal_reject_descriptor_and_byte_mutations() -> None:
    batch = ordinary_seed_batch()
    descriptor = ScheduledWholeIntervalMemberDescriptorV2(
        record_kind="scheduler.interval-result",
        subject_id="command",
        record_id="result",
        schema_id="chiplog.scheduler.interval-result.v2",
        fingerprint=DIGEST,
    )
    body = ScheduledWholeIntervalEnvelopeV2(
        tenant_id="tenant",
        interval_command=call_head("command"),
        branch=batch.branch,
        primitive_reference=present("parent"),
        selected_configuration=batch.configuration_source.configuration,
        selected_mandate=batch.selected_mandate.mandate_head,
        selected_applicability=call_head("applicability"),
        selected_budget_predecessor=batch.selected_budget_predecessor.budget_head,
        budget_successor=Absent(),
        selected_clock_cut_fingerprint=DIGEST,
        outcome=present("result"),
        ordered_non_envelope_members=(descriptor,),
        expected_ordinary_seed_count=0,
        ordered_occurrence_materializations=(),
        resulting_boundary="10",
        dependency_manifest_fingerprint=DIGEST,
    )
    payload = body.canonical_bytes()
    proposal = ProposedScheduledWholeEnvelopeV2(
        proposed_record_id="whole",
        body=body,
        canonical_envelope_bytes=payload,
        external_reference=present_with_fingerprint("whole", payload),
    )
    assert type(proposal).model_validate_json(proposal.canonical_bytes()) == proposal
    wire = json.loads(body.canonical_bytes())
    wire["ordered_non_envelope_members"].append(descriptor.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        ScheduledWholeIntervalEnvelopeV2.model_validate_json(json.dumps(wire))
    with pytest.raises(ValidationError, match="canonical body"):
        ProposedScheduledWholeEnvelopeV2(
            proposed_record_id="whole",
            body=body,
            canonical_envelope_bytes=b"wrong",
            external_reference=present("whole"),
        )


def present_with_fingerprint(name: str, payload: bytes) -> Present:
    return Present(head=name, fingerprint=hashlib.sha256(payload).hexdigest())


def ordinary_exact_replay() -> ScheduledIntervalExactReplayV2:
    seed_batch = ordinary_seed_batch()
    assert isinstance(seed_batch.source, PreparedPrimitiveFirstPublicationV2)
    parent = seed_batch.source.primitive_parent
    parent_reference = primitive_parent_reference(parent)
    parent_member = make_scheduled_parent_member(parent)
    result = ExecutableIntervalResultRecordV2(
        parent=parent,
        ordered_dispositions=(),
        ordered_occurrences=(),
        resulting_boundary=parent.boundary.cutoff_due_coordinate,
    )
    result_member = make_executable_interval_result_member(result)
    records = (parent_member, result_member)
    subjects = (parent.command.command_id, parent.command.command_id)
    descriptors = tuple(
        ScheduledWholeIntervalMemberDescriptorV2(
            record_kind=member.record_kind,
            subject_id=subject_id,
            record_id=member.record_id,
            schema_id=member.schema_id,
            fingerprint=member.fingerprint,
        )
        for member, subject_id in zip(records, subjects, strict=True)
    )
    body = ScheduledWholeIntervalEnvelopeV2(
        tenant_id="tenant",
        interval_command=scheduler_execution_command_reference(parent.command),
        branch=parent.branch,
        primitive_reference=parent_reference,
        selected_configuration=seed_batch.configuration_source.configuration,
        selected_mandate=seed_batch.selected_mandate.mandate_head,
        selected_applicability=call_head("applicability"),
        selected_budget_predecessor=seed_batch.selected_budget_predecessor.budget_head,
        budget_successor=Absent(),
        selected_clock_cut_fingerprint=DIGEST,
        outcome=executable_interval_result_reference(result),
        ordered_non_envelope_members=descriptors,
        expected_ordinary_seed_count=0,
        ordered_occurrence_materializations=(),
        resulting_boundary=parent.boundary.cutoff_due_coordinate.canonical_coordinate,
        dependency_manifest_fingerprint=DIGEST,
    )
    whole_raw = body.canonical_bytes()
    whole_digest = hashlib.sha256(whole_raw).hexdigest()
    whole = SelectedScheduledWholeEnvelopeV2(
        selected_reference=Present(
            head="scheduler-whole-envelope-v2:" + whole_digest, fingerprint=whole_digest
        ),
        canonical_envelope_bytes=whole_raw,
        body=body,
    )
    return ScheduledIntervalExactReplayV2(
        source=SelectedExactDecisionV2(
            selected_decision=parent_reference,
            canonical_selected_decision_bytes=parent.canonical_bytes(),
            canonical_selected_result_bytes=result.canonical_bytes(),
            exact_prefix_materialization=parent_reference,
            selected_debit=Absent(),
            canonical_selected_debit_bytes=None,
        ),
        complete_selected_records=records,
        complete_selected_member_subject_ids=subjects,
        selected_whole_envelope=whole,
    )


def overflow_exact_replay() -> ScheduledOverflowIntervalExactReplayV2:
    record = v2_overflow_hold_record()
    primitive = record.primitive
    primitive_raw = primitive.canonical_bytes()
    primitive_reference = overflow_primitive_reference(primitive)
    primitive_member = make_scheduled_overflow_primitive_member(primitive)
    outcome_member = make_executable_overflow_hold_member(record)
    records = (primitive_member, outcome_member)
    subjects = (record.command.command_id, record.command.command_id)
    descriptors = tuple(
        ScheduledWholeIntervalMemberDescriptorV2(
            record_kind=member.record_kind,
            subject_id=subject_id,
            record_id=member.record_id,
            schema_id=member.schema_id,
            fingerprint=member.fingerprint,
        )
        for member, subject_id in zip(records, subjects, strict=True)
    )
    batch = ordinary_seed_batch(1)
    body = ScheduledWholeIntervalEnvelopeV2(
        tenant_id="tenant",
        interval_command=scheduler_execution_command_reference(record.command),
        branch="OVERFLOW_HOLD",
        primitive_reference=primitive_reference,
        selected_configuration=batch.configuration_source.configuration,
        selected_mandate=batch.selected_mandate.mandate_head,
        selected_applicability=call_head("applicability"),
        selected_budget_predecessor=batch.selected_budget_predecessor.budget_head,
        budget_successor=Absent(),
        selected_clock_cut_fingerprint=DIGEST,
        outcome=executable_overflow_hold_reference(record),
        ordered_non_envelope_members=descriptors,
        expected_ordinary_seed_count=0,
        ordered_occurrence_materializations=(),
        resulting_boundary=None,
        dependency_manifest_fingerprint=DIGEST,
    )
    whole_raw = body.canonical_bytes()
    whole_digest = hashlib.sha256(whole_raw).hexdigest()
    whole = SelectedScheduledWholeEnvelopeV2(
        selected_reference=Present(
            head="scheduler-whole-envelope-v2:" + whole_digest, fingerprint=whole_digest
        ),
        canonical_envelope_bytes=whole_raw,
        body=body,
    )
    return ScheduledOverflowIntervalExactReplayV2(
        source=SelectedExactOverflowDecisionV2(
            selected_decision=primitive_reference,
            canonical_selected_decision_bytes=primitive_raw,
            canonical_selected_result_bytes=record.canonical_bytes(),
            exact_prefix_materialization=executable_overflow_hold_reference(record),
            selected_debit=Absent(),
            canonical_selected_debit_bytes=None,
        ),
        complete_selected_records=records,
        complete_selected_member_subject_ids=subjects,
        selected_whole_envelope=whole,
    )


def test_exact_replay_accepts_complete_nonempty_ordinary_and_overflow_wholes() -> None:
    ordinary = ordinary_exact_replay()
    overflow = overflow_exact_replay()
    adapter: TypeAdapter[ScheduledIntervalPreparationResultV2] = TypeAdapter(
        ScheduledIntervalPreparationResultV2
    )
    assert adapter.validate_json(ordinary.canonical_bytes()) == ordinary
    assert adapter.validate_json(overflow.canonical_bytes()) == overflow
    assert base64.b64decode(ordinary.complete_selected_records[0].canonical_base64) == (
        ordinary.source.canonical_selected_decision_bytes
    )
    assert base64.b64decode(ordinary.complete_selected_records[1].canonical_base64) == (
        ordinary.source.canonical_selected_result_bytes
    )
    assert base64.b64decode(overflow.complete_selected_records[0].canonical_base64) == (
        overflow.source.canonical_selected_decision_bytes
    )
    assert base64.b64decode(overflow.complete_selected_records[1].canonical_base64) == (
        overflow.source.canonical_selected_result_bytes
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "UNREGISTERED_SCHEDULED_REPLAY"})


@pytest.mark.parametrize("builder", [ordinary_exact_replay, overflow_exact_replay])
@pytest.mark.parametrize("mutation", ["omit", "reorder", "duplicate", "rehash", "substitute"])
def test_exact_replay_rejects_member_mutations_against_fixed_selected_whole(
    builder: Callable[[], ScheduledIntervalExactReplayV2 | ScheduledOverflowIntervalExactReplayV2],
    mutation: str,
) -> None:
    replay = builder()
    records = replay.complete_selected_records
    if mutation == "omit":
        changed = records[:1]
    elif mutation == "reorder":
        changed = tuple(reversed(records))
    elif mutation == "duplicate":
        changed = (records[0], records[0])
    elif mutation == "rehash":
        changed = (records[0].model_copy(update={"fingerprint": "b" * 64}), records[1])
    else:
        replacement_payload = b"substituted-selected-member"
        changed = (
            records[0].model_copy(
                update={
                    "record_id": "substituted:" + hashlib.sha256(replacement_payload).hexdigest(),
                    "canonical_base64": base64.b64encode(replacement_payload).decode(),
                    "fingerprint": hashlib.sha256(replacement_payload).hexdigest(),
                }
            ),
            records[1],
        )
    with pytest.raises(ValidationError):
        if isinstance(replay, ScheduledIntervalExactReplayV2):
            ScheduledIntervalExactReplayV2(
                source=replay.source,
                complete_selected_records=changed,
                complete_selected_member_subject_ids=replay.complete_selected_member_subject_ids,
                selected_whole_envelope=replay.selected_whole_envelope,
            )
        else:
            ScheduledOverflowIntervalExactReplayV2(
                source=replay.source,
                complete_selected_records=changed,
                complete_selected_member_subject_ids=replay.complete_selected_member_subject_ids,
                selected_whole_envelope=replay.selected_whole_envelope,
            )


def test_exact_replay_keeps_ordinary_and_overflow_branch_in_separate_closed_sources() -> None:
    ordinary = ordinary_exact_replay()
    overflow = overflow_exact_replay()
    with pytest.raises(ValidationError, match="ordinary"):
        ScheduledIntervalExactReplayV2(
            source=ordinary.source,
            complete_selected_records=ordinary.complete_selected_records,
            complete_selected_member_subject_ids=ordinary.complete_selected_member_subject_ids,
            selected_whole_envelope=overflow.selected_whole_envelope,
        )
    with pytest.raises(ValidationError):
        ScheduledIntervalExactReplayV2.model_validate(overflow.model_dump())
    malformed = json.loads(overflow.canonical_bytes())
    malformed["selected_whole_envelope"]["body"]["expected_ordinary_seed_count"] = 1
    with pytest.raises(ValidationError, match="no ordinary members"):
        ScheduledOverflowIntervalExactReplayV2.model_validate_json(json.dumps(malformed))
    with pytest.raises(ValidationError):
        SelectedExactOverflowDecisionV2(
            selected_decision=present("overflow-decision"),
            canonical_selected_decision_bytes=b"overflow-decision",
            canonical_selected_result_bytes=b"overflow-result",
            exact_prefix_materialization=present("overflow-prefix"),
            selected_debit=present("debit"),
            canonical_selected_debit_bytes=b"",
        )


@pytest.mark.parametrize("selected_part", ["primitive", "result"])
def test_ordinary_replay_rejects_foreign_valid_selected_bytes_while_whole_is_unchanged(
    selected_part: Literal["primitive", "result"],
) -> None:
    replay = ordinary_exact_replay()
    parent_body = ExecutableIntervalResultRecordV2.model_validate_json(
        base64.b64decode(replay.complete_selected_records[1].canonical_base64, validate=True)
    ).parent
    foreign_parent = parent_body.model_copy(
        update={"command": parent_body.command.model_copy(update={"command_id": "foreign-command"})}
    )
    foreign_result = ExecutableIntervalResultRecordV2(
        parent=foreign_parent,
        ordered_dispositions=(),
        ordered_occurrences=(),
        resulting_boundary=foreign_parent.boundary.cutoff_due_coordinate,
    )
    source = replay.source.model_copy(
        update=(
            {
                "selected_decision": primitive_parent_reference(foreign_parent),
                "canonical_selected_decision_bytes": foreign_parent.canonical_bytes(),
            }
            if selected_part == "primitive"
            else {"canonical_selected_result_bytes": foreign_result.canonical_bytes()}
        )
    )
    with pytest.raises(ValidationError, match="native join"):
        ScheduledIntervalExactReplayV2(
            source=source,
            complete_selected_records=replay.complete_selected_records,
            complete_selected_member_subject_ids=replay.complete_selected_member_subject_ids,
            selected_whole_envelope=replay.selected_whole_envelope,
        )


@pytest.mark.parametrize("selected_part", ["primitive", "result"])
def test_overflow_replay_rejects_foreign_valid_selected_bytes_while_whole_is_unchanged(
    selected_part: Literal["primitive", "result"],
) -> None:
    replay = overflow_exact_replay()
    foreign = v2_overflow_hold_record(command_suffix="-foreign")
    source = replay.source.model_copy(
        update=(
            {
                "selected_decision": overflow_primitive_reference(foreign.primitive),
                "canonical_selected_decision_bytes": foreign.primitive.canonical_bytes(),
            }
            if selected_part == "primitive"
            else {"canonical_selected_result_bytes": foreign.canonical_bytes()}
        )
    )
    with pytest.raises(ValidationError, match="native join"):
        ScheduledOverflowIntervalExactReplayV2(
            source=source,
            complete_selected_records=replay.complete_selected_records,
            complete_selected_member_subject_ids=replay.complete_selected_member_subject_ids,
            selected_whole_envelope=replay.selected_whole_envelope,
        )
