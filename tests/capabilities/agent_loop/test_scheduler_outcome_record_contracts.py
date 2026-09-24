"""Physical scheduler outcome-row contracts."""

from __future__ import annotations

import base64
from collections.abc import Callable

import pytest
from tests.support.scheduler_execution import ordinary_seed_batch
from tests.support.scheduler_execution_batches import finalized_result, overflow_seed_batch
from tests.support.scheduler_overflow_hold_records import v2_overflow_hold_record

from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    FinalizedIntervalResultV2,
    FinalizedOverflowHoldV2,
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedPrimitiveFirstPublicationV2,
    ScheduledWholeIntervalMemberDescriptorV2,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    IntervalParentPrimitive,
    SchedulerCanonicalMember,
)
from chiplog.capabilities.agent_loop.scheduler_outcome_record_contracts import (
    DecodedSchedulerOutcomeMember,
    ExecutableIntervalResultRecordV2,
    SchedulerOutcomeRecordIntegrityError,
    SchedulerValidatedWholeMember,
    decode_scheduler_outcome_member,
    executable_interval_result_reference,
    executable_overflow_hold_reference,
    make_executable_interval_result_member,
    make_executable_overflow_hold_member,
    make_scheduled_overflow_primitive_member,
    make_scheduled_parent_member,
    make_scheduled_whole_member,
    validate_scheduler_outcome_pair,
    validate_scheduler_whole_members,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    overflow_primitive_reference,
    primitive_parent_reference,
    scheduler_execution_command_reference,
)


def _parent() -> IntervalParentPrimitive:
    source = ordinary_seed_batch(0).source
    assert isinstance(source, PreparedPrimitiveFirstPublicationV2)
    return source.primitive_parent


def _result() -> ExecutableIntervalResultRecordV2:
    parent = _parent()
    return ExecutableIntervalResultRecordV2(
        parent=parent,
        ordered_dispositions=(),
        ordered_occurrences=(),
        resulting_boundary=parent.boundary.cutoff_due_coordinate,
    )


def _descriptor(
    member: SchedulerCanonicalMember, subject_id: str
) -> ScheduledWholeIntervalMemberDescriptorV2:
    return ScheduledWholeIntervalMemberDescriptorV2(
        record_kind=member.record_kind,
        subject_id=subject_id,
        record_id=member.record_id,
        schema_id=member.schema_id,
        fingerprint=member.fingerprint,
    )


def _ordinary_rows() -> tuple[
    PreparedPrimitiveFirstPublicationV2,
    ExecutableIntervalResultRecordV2,
    DecodedSchedulerOutcomeMember,
    DecodedSchedulerOutcomeMember,
    DecodedSchedulerOutcomeMember,
]:
    original = ordinary_seed_batch(0).source
    assert isinstance(original, PreparedPrimitiveFirstPublicationV2)
    source = original.model_copy(
        update={
            "primitive_parent_reference": primitive_parent_reference(original.primitive_parent),
            "canonical_primitive_parent_bytes": original.primitive_parent.canonical_bytes(),
        }
    )
    parent_member = make_scheduled_parent_member(source.primitive_parent)
    result = _result()
    result_member = make_executable_interval_result_member(result)
    prototype = finalized_result(0).whole_envelope.body
    whole_body = prototype.model_copy(
        update={
            "interval_command": scheduler_execution_command_reference(
                source.primitive_parent.command
            ),
            "branch": source.primitive_parent.branch,
            "primitive_reference": source.primitive_parent_reference,
            "outcome": executable_interval_result_reference(result),
            "ordered_non_envelope_members": (
                _descriptor(parent_member, source.primitive_parent.command.command_id),
                _descriptor(result_member, source.primitive_parent.command.command_id),
            ),
            "expected_ordinary_seed_count": 0,
            "ordered_occurrence_materializations": (),
            "resulting_boundary": result.resulting_boundary.canonical_coordinate,
            "budget_successor": Absent(),
        }
    )
    return (
        source,
        result,
        decode_scheduler_outcome_member(parent_member),
        decode_scheduler_outcome_member(result_member),
        decode_scheduler_outcome_member(make_scheduled_whole_member(whole_body)),
    )


def _overflow_rows() -> tuple[
    PreparedOverflowPrimitiveFirstPublicationV2,
    DecodedSchedulerOutcomeMember,
    DecodedSchedulerOutcomeMember,
    DecodedSchedulerOutcomeMember,
]:
    record = v2_overflow_hold_record()
    original = overflow_seed_batch().source
    assert isinstance(original, PreparedOverflowPrimitiveFirstPublicationV2)
    source = original.model_copy(
        update={
            "overflow_primitive": record.primitive,
            "overflow_primitive_reference": overflow_primitive_reference(record.primitive),
            "canonical_overflow_primitive_bytes": record.primitive.canonical_bytes(),
        }
    )
    primitive_member = make_scheduled_overflow_primitive_member(record.primitive)
    hold_member = make_executable_overflow_hold_member(record)
    prototype = finalized_result(0, overflow=True).whole_envelope.body
    whole_body = prototype.model_copy(
        update={
            "interval_command": scheduler_execution_command_reference(record.command),
            "primitive_reference": source.overflow_primitive_reference,
            "outcome": executable_overflow_hold_reference(record),
            "ordered_non_envelope_members": (
                _descriptor(primitive_member, record.primitive.command.subject_id),
                _descriptor(hold_member, record.command.command_id),
            ),
        }
    )
    return (
        source,
        decode_scheduler_outcome_member(primitive_member),
        decode_scheduler_outcome_member(hold_member),
        decode_scheduler_outcome_member(make_scheduled_whole_member(whole_body)),
    )


@pytest.mark.parametrize(
    ("member", "subject"),
    [
        pytest.param(
            lambda: make_scheduled_parent_member(_parent()), lambda: _parent().command.command_id
        ),
        pytest.param(
            lambda: make_scheduled_overflow_primitive_member(v2_overflow_hold_record().primitive),
            lambda: v2_overflow_hold_record().primitive.command.subject_id,
        ),
        pytest.param(
            lambda: make_executable_interval_result_member(_result()),
            lambda: _parent().command.command_id,
        ),
        pytest.param(
            lambda: make_executable_overflow_hold_member(v2_overflow_hold_record()),
            lambda: v2_overflow_hold_record().command.command_id,
        ),
    ],
)
def test_fixed_rows_round_trip_exact_canonical_bytes(
    member: Callable[[], SchedulerCanonicalMember], subject: Callable[[], str]
) -> None:
    decoded = decode_scheduler_outcome_member(member())
    assert decoded.subject_id == subject()
    assert decoded.raw == base64.b64decode(decoded.member.canonical_base64, validate=True)


@pytest.mark.parametrize("mutation", ["id", "schema", "fingerprint", "noncanonical-base64"])
def test_fixed_result_row_rejects_identity_and_encoding_mutations(mutation: str) -> None:
    member = make_executable_interval_result_member(_result())
    if mutation == "id":
        forged = member.model_copy(update={"record_id": "scheduler-interval-result-v2:" + "0" * 64})
    elif mutation == "schema":
        forged = member.model_copy(update={"schema_id": "chiplog.scheduler.overflow-hold.v2"})
    elif mutation == "fingerprint":
        forged = member.model_copy(update={"fingerprint": "0" * 64})
    else:
        raw = base64.b64decode(member.canonical_base64, validate=True)
        forged = member.model_copy(
            update={"canonical_base64": base64.b64encode(raw).decode() + "\n"}
        )
    with pytest.raises(SchedulerOutcomeRecordIntegrityError):
        decode_scheduler_outcome_member(forged)


def test_result_retains_semantic_parent_not_its_physical_outcome_reference() -> None:
    result = _result()
    assert primitive_parent_reference(result.parent).head.startswith("scheduler-parent-v1:")
    assert result.resulting_boundary == result.parent.boundary.cutoff_due_coordinate


def test_ordinary_pair_and_whole_membership_bind_real_physical_rows() -> None:
    source, result, parent, outcome, whole = _ordinary_rows()
    validate_scheduler_outcome_pair(
        source,
        outcome,
        whole,
        finalized_outcome=FinalizedIntervalResultV2(
            interval_result=executable_interval_result_reference(result),
            branch=result.parent.branch,
            resulting_boundary=result.resulting_boundary.canonical_coordinate,
        ),
        expected_seed_count=0,
        finalized_occurrences=(),
        ordered_dispositions=(),
    )
    validate_scheduler_whole_members((parent, outcome, whole))


def test_whole_membership_rejects_outcome_before_required_parent() -> None:
    _, _, parent, outcome, whole = _ordinary_rows()
    with pytest.raises(SchedulerOutcomeRecordIntegrityError, match="descriptor"):
        validate_scheduler_whole_members((outcome, parent, whole))


def test_streamed_overflow_pair_and_whole_bind_delegated_active_hold() -> None:
    source, primitive, hold, whole = _overflow_rows()
    validate_scheduler_outcome_pair(
        source,
        hold,
        whole,
        finalized_outcome=FinalizedOverflowHoldV2(
            hold=executable_overflow_hold_reference(v2_overflow_hold_record()),
            overflow_primitive=source.overflow_primitive_reference,
        ),
        expected_seed_count=0,
        finalized_occurrences=(),
        ordered_dispositions=(),
    )
    validate_scheduler_whole_members((primitive, hold, whole))
    assert whole.member.record_id.startswith("scheduler-whole-envelope-v2:")


def test_v2_resolved_hold_round_trips_but_cannot_be_automatic_overflow_outcome() -> None:
    member = make_executable_overflow_hold_member(v2_overflow_hold_record(resolved=True))
    decoded = decode_scheduler_outcome_member(member)
    assert decoded.member == member


def test_whole_membership_rejects_fixed_row_smuggled_as_external_member() -> None:
    _, _, parent, outcome, whole = _ordinary_rows()
    smuggled = SchedulerValidatedWholeMember(
        member=parent.member,
        subject_id=parent.subject_id,
    )
    with pytest.raises(SchedulerOutcomeRecordIntegrityError, match="cannot be supplied"):
        validate_scheduler_whole_members((smuggled, outcome, whole))
