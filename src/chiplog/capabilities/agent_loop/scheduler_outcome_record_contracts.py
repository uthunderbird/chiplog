"""Closed physical codecs for executable scheduler outcomes.

These codecs establish only fixed byte, identity, and representation joins.  They
do not select a batch or register unrelated scheduler members.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from .recovery_contracts import Identity, Present, RecoveryDTO
from .scheduler_contracts import (
    DueCoordinate,
    OccurrenceDisposition,
    OverflowActive,
    SkippedDisposition,
)
from .scheduler_execution_contracts import (
    FinalizedIntervalResultV2,
    FinalizedOccurrenceCommitmentV2,
    FinalizedOverflowHoldV2,
    FinalizedScheduledIntervalOutcomeV2,
    OverflowHoldPrimitiveV2,
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedPrimitiveFirstPublicationV2,
    ScheduledIntervalExactReplayV2,
    ScheduledOverflowIntervalExactReplayV2,
    ScheduledWholeIntervalEnvelopeV2,
    SchedulerExecutionDTO,
)
from .scheduler_materialization import IntervalParentPrimitive, SchedulerCanonicalMember
from .scheduler_overflow_hold_records import (
    ExecutableOverflowHoldRecordV2,
    decode_executable_overflow_hold_record,
    executable_overflow_hold_member,
)
from .scheduler_overflow_hold_records import (
    executable_overflow_hold_reference as _executable_overflow_hold_reference,
)
from .scheduler_seed_producer_contracts import (
    overflow_primitive_reference,
    primitive_parent_reference,
    scheduler_execution_command_reference,
    validate_prepared_overflow_primitive,
)

INTERVAL_PARENT_RECORD_KIND = "interval-parent"
OVERFLOW_PRIMITIVE_RECORD_KIND = "overflow-primitive"
INTERVAL_RESULT_RECORD_KIND = "interval-result"
OVERFLOW_HOLD_RECORD_KIND = "overflow-hold"
WHOLE_ENVELOPE_RECORD_KIND = "whole-envelope"

INTERVAL_PARENT_SCHEMA = "chiplog.scheduler.interval-parent.v1"
OVERFLOW_PRIMITIVE_SCHEMA = "chiplog.scheduler.overflow-primitive.v2"
INTERVAL_RESULT_SCHEMA = "chiplog.scheduler.interval-result.v2"
OVERFLOW_HOLD_SCHEMA = "chiplog.scheduler.overflow-hold.v2"
WHOLE_ENVELOPE_SCHEMA = "chiplog.scheduler.whole-envelope.v2"


class SchedulerOutcomeRecordIntegrityError(ValueError):
    """A member is outside the fixed executable scheduler outcome grammar."""


class ExecutableIntervalResultRecordV2(SchedulerExecutionDTO):
    """The durable ordinary/resolution outcome, without its physical reference."""

    schema_id: Literal["chiplog.scheduler.interval-result.v2"] = (
        "chiplog.scheduler.interval-result.v2"
    )
    parent: IntervalParentPrimitive
    ordered_dispositions: tuple[OccurrenceDisposition, ...]
    ordered_occurrences: tuple[FinalizedOccurrenceCommitmentV2, ...]
    resulting_boundary: DueCoordinate

    @model_validator(mode="after")
    def retains_the_semantic_parent(self) -> ExecutableIntervalResultRecordV2:
        if self.parent.kind not in ("ORDINARY", "RESOLUTION"):
            raise ValueError("interval result parent must be ordinary or resolution")
        if self.resulting_boundary != self.parent.boundary.cutoff_due_coordinate:
            raise ValueError("interval result boundary differs from parent cutoff")
        parent_reference = primitive_parent_reference(self.parent)
        if any(
            isinstance(item, SkippedDisposition) and item.policy_decision != parent_reference
            for item in self.ordered_dispositions
        ):
            raise ValueError("skipped disposition must retain the semantic parent reference")
        return self


DecodedSchedulerOutcomeBody = (
    IntervalParentPrimitive
    | OverflowHoldPrimitiveV2
    | ExecutableIntervalResultRecordV2
    | ExecutableOverflowHoldRecordV2
    | ScheduledWholeIntervalEnvelopeV2
)


class DecodedSchedulerOutcomeMember(SchedulerExecutionDTO):
    member: SchedulerCanonicalMember
    raw: bytes = Field(min_length=1)
    body: DecodedSchedulerOutcomeBody
    subject_id: Identity


class SchedulerValidatedWholeMember(SchedulerExecutionDTO):
    """An already registered and decoded non-outcome member supplied by the owner recipe."""

    member: SchedulerCanonicalMember
    subject_id: Identity


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _member(kind: str, schema: str, record_id: str, raw: bytes) -> SchedulerCanonicalMember:
    return SchedulerCanonicalMember(
        record_kind=kind,
        record_id=record_id,
        schema_id=schema,
        canonical_base64=base64.b64encode(raw).decode(),
        fingerprint=_digest(raw),
    )


def make_scheduled_parent_member(parent: IntervalParentPrimitive) -> SchedulerCanonicalMember:
    reference = primitive_parent_reference(parent)
    return _member(
        INTERVAL_PARENT_RECORD_KIND,
        INTERVAL_PARENT_SCHEMA,
        reference.head,
        parent.canonical_bytes(),
    )


def make_scheduled_overflow_primitive_member(
    primitive: OverflowHoldPrimitiveV2,
) -> SchedulerCanonicalMember:
    reference = overflow_primitive_reference(primitive)
    return _member(
        OVERFLOW_PRIMITIVE_RECORD_KIND,
        OVERFLOW_PRIMITIVE_SCHEMA,
        reference.head,
        primitive.canonical_bytes(),
    )


def executable_interval_result_reference(record: ExecutableIntervalResultRecordV2) -> Present:
    raw = record.canonical_bytes()
    digest = _digest(raw)
    return Present(head="scheduler-interval-result-v2:" + digest, fingerprint=digest)


def make_executable_interval_result_member(
    record: ExecutableIntervalResultRecordV2,
) -> SchedulerCanonicalMember:
    reference = executable_interval_result_reference(record)
    return _member(
        INTERVAL_RESULT_RECORD_KIND,
        INTERVAL_RESULT_SCHEMA,
        reference.head,
        record.canonical_bytes(),
    )


def make_executable_overflow_hold_member(
    record: ExecutableOverflowHoldRecordV2,
) -> SchedulerCanonicalMember:
    """Delegate V2 body construction and its physical identity to the hold owner."""

    return executable_overflow_hold_member(record)


def executable_overflow_hold_reference(record: ExecutableOverflowHoldRecordV2) -> Present:
    """Return the V2 hold owner's native physical reference."""

    return _executable_overflow_hold_reference(record)


def scheduled_whole_reference(body: ScheduledWholeIntervalEnvelopeV2) -> Present:
    raw = body.canonical_bytes()
    digest = _digest(raw)
    return Present(head="scheduler-whole-envelope-v2:" + digest, fingerprint=digest)


def make_scheduled_whole_member(body: ScheduledWholeIntervalEnvelopeV2) -> SchedulerCanonicalMember:
    reference = scheduled_whole_reference(body)
    return _member(
        WHOLE_ENVELOPE_RECORD_KIND, WHOLE_ENVELOPE_SCHEMA, reference.head, body.canonical_bytes()
    )


def _raw(member: SchedulerCanonicalMember) -> bytes:
    try:
        raw = base64.b64decode(member.canonical_base64, validate=True)
    except (ValueError, binascii.Error) as error:
        raise SchedulerOutcomeRecordIntegrityError("scheduler outcome base64 is invalid") from error
    if not raw or base64.b64encode(raw).decode() != member.canonical_base64:
        raise SchedulerOutcomeRecordIntegrityError("scheduler outcome base64 is not canonical")
    if _digest(raw) != member.fingerprint:
        raise SchedulerOutcomeRecordIntegrityError(
            "scheduler outcome fingerprint differs from bytes"
        )
    return raw


def _decode_body[DecodedBodyT: RecoveryDTO](raw: bytes, model: type[DecodedBodyT]) -> DecodedBodyT:
    try:
        body = model.model_validate_json(raw)
    except ValidationError as error:
        raise SchedulerOutcomeRecordIntegrityError(
            "scheduler outcome bytes do not decode"
        ) from error
    if body.canonical_bytes() != raw:
        raise SchedulerOutcomeRecordIntegrityError("scheduler outcome bytes are not canonical")
    return body


def _decoded(
    member: SchedulerCanonicalMember,
    raw: bytes,
    body: DecodedSchedulerOutcomeBody,
    subject: Identity,
    expected: Present,
) -> DecodedSchedulerOutcomeMember:
    if member.record_id != expected.head or member.fingerprint != expected.fingerprint:
        raise SchedulerOutcomeRecordIntegrityError("scheduler outcome physical identity differs")
    return DecodedSchedulerOutcomeMember(member=member, raw=raw, body=body, subject_id=subject)


def decode_scheduler_outcome_member(
    member: SchedulerCanonicalMember,
) -> DecodedSchedulerOutcomeMember:
    """Decode one of the five literal physical rows, preserving its exact raw bytes."""

    raw = _raw(member)
    try:
        if (
            member.record_kind == INTERVAL_PARENT_RECORD_KIND
            and member.schema_id == INTERVAL_PARENT_SCHEMA
        ):
            parent = _decode_body(raw, IntervalParentPrimitive)
            return _decoded(
                member,
                raw,
                parent,
                parent.command.command_id,
                primitive_parent_reference(parent),
            )
        elif (
            member.record_kind == OVERFLOW_PRIMITIVE_RECORD_KIND
            and member.schema_id == OVERFLOW_PRIMITIVE_SCHEMA
        ):
            primitive = _decode_body(raw, OverflowHoldPrimitiveV2)
            return _decoded(
                member,
                raw,
                primitive,
                primitive.command.subject_id,
                overflow_primitive_reference(primitive),
            )
        elif (
            member.record_kind == INTERVAL_RESULT_RECORD_KIND
            and member.schema_id == INTERVAL_RESULT_SCHEMA
        ):
            result = _decode_body(raw, ExecutableIntervalResultRecordV2)
            return _decoded(
                member,
                raw,
                result,
                result.parent.command.command_id,
                executable_interval_result_reference(result),
            )
        elif (
            member.record_kind == OVERFLOW_HOLD_RECORD_KIND
            and member.schema_id == OVERFLOW_HOLD_SCHEMA
        ):
            hold = decode_executable_overflow_hold_record(member).record
            return _decoded(
                member,
                raw,
                hold,
                hold.command.command_id,
                executable_overflow_hold_reference(hold),
            )
        elif (
            member.record_kind == WHOLE_ENVELOPE_RECORD_KIND
            and member.schema_id == WHOLE_ENVELOPE_SCHEMA
        ):
            whole = _decode_body(raw, ScheduledWholeIntervalEnvelopeV2)
            return _decoded(
                member,
                raw,
                whole,
                whole.interval_command.subject_id,
                scheduled_whole_reference(whole),
            )
        else:
            raise SchedulerOutcomeRecordIntegrityError(
                "unregistered scheduler outcome kind or schema"
            )
    except (ValueError, AttributeError) as error:
        if isinstance(error, SchedulerOutcomeRecordIntegrityError):
            raise
        raise SchedulerOutcomeRecordIntegrityError(
            "scheduler outcome body violates its fixed row"
        ) from error
    raise AssertionError("unreachable fixed scheduler outcome row")


def scheduler_outcome_subject(decoded: DecodedSchedulerOutcomeMember) -> Identity:
    return decoded.subject_id


def _physical_reference(decoded: DecodedSchedulerOutcomeMember) -> Present:
    return Present(head=decoded.member.record_id, fingerprint=decoded.member.fingerprint)


def validate_selected_scheduler_replay_native(
    replay: ScheduledIntervalExactReplayV2 | ScheduledOverflowIntervalExactReplayV2,
) -> None:
    """Bind selected replay bytes to its fixed primitive, outcome, and WHOLE rows."""

    selected = replay.selected_whole_envelope
    try:
        whole = ScheduledWholeIntervalEnvelopeV2.model_validate_json(
            selected.canonical_envelope_bytes
        )
    except ValidationError as error:
        raise SchedulerOutcomeRecordIntegrityError("selected WHOLE bytes do not decode") from error
    if whole.canonical_bytes() != selected.canonical_envelope_bytes:
        raise SchedulerOutcomeRecordIntegrityError("selected WHOLE bytes are not canonical")
    if scheduled_whole_reference(whole) != selected.selected_reference:
        raise SchedulerOutcomeRecordIntegrityError("selected WHOLE physical reference differs")

    literal_rows = {
        INTERVAL_PARENT_RECORD_KIND: INTERVAL_PARENT_SCHEMA,
        OVERFLOW_PRIMITIVE_RECORD_KIND: OVERFLOW_PRIMITIVE_SCHEMA,
        INTERVAL_RESULT_RECORD_KIND: INTERVAL_RESULT_SCHEMA,
        OVERFLOW_HOLD_RECORD_KIND: OVERFLOW_HOLD_SCHEMA,
        WHOLE_ENVELOPE_RECORD_KIND: WHOLE_ENVELOPE_SCHEMA,
    }
    recognized: list[tuple[int, DecodedSchedulerOutcomeMember]] = []
    for index, member in enumerate(replay.complete_selected_records):
        kind_known = member.record_kind in literal_rows
        schema_known = member.schema_id in literal_rows.values()
        if kind_known or schema_known:
            if literal_rows.get(member.record_kind) != member.schema_id:
                raise SchedulerOutcomeRecordIntegrityError(
                    "selected fixed row kind and schema differ"
                )
            if member.record_kind == WHOLE_ENVELOPE_RECORD_KIND:
                raise SchedulerOutcomeRecordIntegrityError("selected records cannot include WHOLE")
            recognized.append((index, decode_scheduler_outcome_member(member)))
    if len(recognized) != 2:
        raise SchedulerOutcomeRecordIntegrityError(
            "selected replay needs one primitive and one outcome"
        )
    if len({item.member.record_kind for _, item in recognized}) != 2:
        raise SchedulerOutcomeRecordIntegrityError("selected replay duplicates a fixed role")
    primitive_index, primitive = recognized[0]
    outcome_index, outcome = recognized[1]
    if primitive_index >= outcome_index:
        raise SchedulerOutcomeRecordIntegrityError(
            "selected primitive must precede selected outcome"
        )
    if (
        whole.budget_successor != replay.source.selected_debit
        or (
            isinstance(replay.source.selected_debit, Present)
            and replay.source.canonical_selected_debit_bytes is None
        )
        or (
            not isinstance(replay.source.selected_debit, Present)
            and replay.source.canonical_selected_debit_bytes is not None
        )
    ):
        raise SchedulerOutcomeRecordIntegrityError("selected debit marker differs from WHOLE")

    if isinstance(replay, ScheduledIntervalExactReplayV2):
        if not isinstance(primitive.body, IntervalParentPrimitive) or not isinstance(
            outcome.body, ExecutableIntervalResultRecordV2
        ):
            raise SchedulerOutcomeRecordIntegrityError("ordinary replay fixed roles differ")
        parent = primitive.body
        result = outcome.body
        if (
            replay.source.selected_decision != primitive_parent_reference(parent)
            or replay.source.canonical_selected_decision_bytes != primitive.raw
            or primitive.raw != parent.canonical_bytes()
            or result.parent != parent
            or replay.source.canonical_selected_result_bytes != outcome.raw
            or outcome.raw != result.canonical_bytes()
            or whole.interval_command != scheduler_execution_command_reference(parent.command)
            or whole.branch != parent.branch
            or whole.primitive_reference != replay.source.selected_decision
            or whole.outcome != _physical_reference(outcome)
            or whole.resulting_boundary != result.resulting_boundary.canonical_coordinate
            or whole.expected_ordinary_seed_count != len(result.ordered_occurrences)
            or whole.ordered_occurrence_materializations
            != tuple(item.materialization for item in result.ordered_occurrences)
        ):
            raise SchedulerOutcomeRecordIntegrityError(
                "ordinary selected replay native join differs"
            )
        return
    if not isinstance(primitive.body, OverflowHoldPrimitiveV2) or not isinstance(
        outcome.body, ExecutableOverflowHoldRecordV2
    ):
        raise SchedulerOutcomeRecordIntegrityError("overflow replay fixed roles differ")
    overflow = primitive.body
    hold = outcome.body
    if (
        replay.source.selected_decision != overflow_primitive_reference(overflow)
        or replay.source.canonical_selected_decision_bytes != primitive.raw
        or primitive.raw != overflow.canonical_bytes()
        or replay.source.canonical_selected_result_bytes != outcome.raw
        or outcome.raw != hold.canonical_bytes()
        or not isinstance(hold.state, OverflowActive)
        or hold.primitive != overflow
        or overflow.command != scheduler_execution_command_reference(hold.command)
        or whole.interval_command != overflow.command
        or whole.branch != "OVERFLOW_HOLD"
        or whole.primitive_reference != replay.source.selected_decision
        or whole.outcome != _physical_reference(outcome)
        or whole.expected_ordinary_seed_count != 0
        or whole.ordered_occurrence_materializations != ()
        or whole.resulting_boundary is not None
    ):
        raise SchedulerOutcomeRecordIntegrityError("overflow selected replay native join differs")


def validate_scheduler_outcome_pair(
    source: PreparedPrimitiveFirstPublicationV2 | PreparedOverflowPrimitiveFirstPublicationV2,
    outcome: DecodedSchedulerOutcomeMember,
    whole: DecodedSchedulerOutcomeMember,
    *,
    finalized_outcome: FinalizedScheduledIntervalOutcomeV2,
    expected_seed_count: int,
    finalized_occurrences: tuple[FinalizedOccurrenceCommitmentV2, ...],
    ordered_dispositions: tuple[OccurrenceDisposition, ...],
) -> None:
    """Bind a retained primitive source, physical outcome, and physical WHOLE body."""

    outcome = decode_scheduler_outcome_member(outcome.member)
    whole = decode_scheduler_outcome_member(whole.member)
    if not isinstance(whole.body, ScheduledWholeIntervalEnvelopeV2):
        raise SchedulerOutcomeRecordIntegrityError("pair requires a physical WHOLE row")
    body = whole.body
    outcome_reference = _physical_reference(outcome)
    if isinstance(source, PreparedPrimitiveFirstPublicationV2):
        parent = source.primitive_parent
        if (
            source.canonical_primitive_parent_bytes != parent.canonical_bytes()
            or source.primitive_parent_reference != primitive_parent_reference(parent)
        ):
            raise SchedulerOutcomeRecordIntegrityError(
                "prepared parent source differs from its primitive"
            )
        if not isinstance(outcome.body, ExecutableIntervalResultRecordV2):
            raise SchedulerOutcomeRecordIntegrityError(
                "ordinary source requires an interval-result row"
            )
        result = outcome.body
        if (
            result.parent != parent
            or result.ordered_occurrences != finalized_occurrences
            or result.ordered_dispositions != ordered_dispositions
            or len(result.ordered_occurrences) != expected_seed_count
            or not isinstance(finalized_outcome, FinalizedIntervalResultV2)
            or finalized_outcome.interval_result != outcome_reference
            or finalized_outcome.branch != parent.branch
            or finalized_outcome.resulting_boundary
            != result.resulting_boundary.canonical_coordinate
            or body.branch != parent.branch
            or body.interval_command != scheduler_execution_command_reference(parent.command)
            or body.primitive_reference != source.primitive_parent_reference
            or body.outcome != outcome_reference
            or body.expected_ordinary_seed_count != expected_seed_count
            or body.ordered_occurrence_materializations
            != tuple(item.materialization for item in finalized_occurrences)
            or body.resulting_boundary != result.resulting_boundary.canonical_coordinate
        ):
            raise SchedulerOutcomeRecordIntegrityError(
                "ordinary source, outcome, or WHOLE join differs"
            )
        return
    primitive = validate_prepared_overflow_primitive(source)
    if not isinstance(outcome.body, ExecutableOverflowHoldRecordV2):
        raise SchedulerOutcomeRecordIntegrityError("overflow source requires an overflow-hold row")
    hold = outcome.body
    if (
        hold.primitive != primitive
        or hold.primitive.command != scheduler_execution_command_reference(hold.command)
        or not isinstance(hold.state, OverflowActive)
        or not isinstance(finalized_outcome, FinalizedOverflowHoldV2)
        or finalized_outcome.hold != outcome_reference
        or finalized_outcome.overflow_primitive != source.overflow_primitive_reference
        or body.branch != "OVERFLOW_HOLD"
        or body.interval_command != primitive.command
        or body.primitive_reference != source.overflow_primitive_reference
        or body.outcome != outcome_reference
        or body.expected_ordinary_seed_count != 0
        or body.ordered_occurrence_materializations != ()
        or body.resulting_boundary is not None
        or expected_seed_count != 0
        or finalized_occurrences != ()
        or ordered_dispositions != ()
    ):
        raise SchedulerOutcomeRecordIntegrityError(
            "overflow source, outcome, or WHOLE join differs"
        )


def validate_scheduler_whole_members(
    members: tuple[DecodedSchedulerOutcomeMember | SchedulerValidatedWholeMember, ...],
) -> None:
    """Check literal primitive/outcome membership and exact WHOLE descriptors/order."""

    if not members or not isinstance(members[-1], DecodedSchedulerOutcomeMember):
        raise SchedulerOutcomeRecordIntegrityError("WHOLE must be the final physical member")
    checked: list[DecodedSchedulerOutcomeMember | SchedulerValidatedWholeMember] = []
    for item in members:
        if isinstance(item, DecodedSchedulerOutcomeMember):
            checked.append(decode_scheduler_outcome_member(item.member))
        elif item.member.record_kind in {
            INTERVAL_PARENT_RECORD_KIND,
            OVERFLOW_PRIMITIVE_RECORD_KIND,
            INTERVAL_RESULT_RECORD_KIND,
            OVERFLOW_HOLD_RECORD_KIND,
            WHOLE_ENVELOPE_RECORD_KIND,
        }:
            raise SchedulerOutcomeRecordIntegrityError(
                "fixed scheduler outcome row cannot be supplied as an external member"
            )
        else:
            checked.append(item)
    whole = checked[-1]
    assert isinstance(whole, DecodedSchedulerOutcomeMember)
    if not isinstance(whole.body, ScheduledWholeIntervalEnvelopeV2):
        raise SchedulerOutcomeRecordIntegrityError("WHOLE must be the final physical member")
    preceding = tuple(checked[:-1])
    ids = tuple(item.member.record_id for item in checked)
    if len(ids) != len(set(ids)):
        raise SchedulerOutcomeRecordIntegrityError(
            "WHOLE membership has duplicate physical records"
        )
    descriptors = whole.body.ordered_non_envelope_members
    if len(descriptors) != len(preceding):
        raise SchedulerOutcomeRecordIntegrityError("WHOLE descriptor count differs from members")
    for item, descriptor in zip(preceding, descriptors, strict=True):
        member = item.member
        if (
            descriptor.owner != "agent_loop"
            or descriptor.record_kind != member.record_kind
            or descriptor.subject_id != item.subject_id
            or descriptor.record_id != member.record_id
            or descriptor.schema_id != member.schema_id
            or descriptor.fingerprint != member.fingerprint
        ):
            raise SchedulerOutcomeRecordIntegrityError("WHOLE descriptor differs from member")
    outcomes = tuple(item for item in preceding if isinstance(item, DecodedSchedulerOutcomeMember))
    if any(isinstance(item.body, ScheduledWholeIntervalEnvelopeV2) for item in outcomes):
        raise SchedulerOutcomeRecordIntegrityError(
            "WHOLE can occur only as the final physical member"
        )
    parents = tuple(item for item in outcomes if isinstance(item.body, IntervalParentPrimitive))
    primitives = tuple(item for item in outcomes if isinstance(item.body, OverflowHoldPrimitiveV2))
    results = tuple(
        item for item in outcomes if isinstance(item.body, ExecutableIntervalResultRecordV2)
    )
    holds = tuple(
        item for item in outcomes if isinstance(item.body, ExecutableOverflowHoldRecordV2)
    )
    if len(parents) + len(primitives) != 1 or len(results) + len(holds) != 1:
        raise SchedulerOutcomeRecordIntegrityError(
            "WHOLE needs exactly one primitive and one outcome"
        )
    primitive_index = next(
        index for index, item in enumerate(preceding) if item in (*parents, *primitives)
    )
    outcome_index = next(
        index for index, item in enumerate(preceding) if item in (*results, *holds)
    )
    if primitive_index >= outcome_index:
        raise SchedulerOutcomeRecordIntegrityError("WHOLE primitive must precede its outcome")
    if (parents and not results) or (primitives and not holds):
        raise SchedulerOutcomeRecordIntegrityError("WHOLE primitive and outcome roles differ")
    if parents:
        parent = parents[0].body
        result = results[0]
        assert isinstance(parent, IntervalParentPrimitive)
        if (
            whole.body.branch != parent.branch
            or whole.body.interval_command != scheduler_execution_command_reference(parent.command)
            or whole.body.primitive_reference != primitive_parent_reference(parent)
            or whole.body.outcome != _physical_reference(result)
        ):
            raise SchedulerOutcomeRecordIntegrityError(
                "WHOLE differs from ordinary primitive/outcome"
            )
    else:
        primitive = primitives[0].body
        hold = holds[0]
        assert isinstance(primitive, OverflowHoldPrimitiveV2)
        assert isinstance(hold.body, ExecutableOverflowHoldRecordV2)
        if (
            whole.body.branch != "OVERFLOW_HOLD"
            or whole.body.interval_command != primitive.command
            or whole.body.primitive_reference != overflow_primitive_reference(primitive)
            or whole.body.outcome != _physical_reference(hold)
            or hold.body.primitive != primitive
        ):
            raise SchedulerOutcomeRecordIntegrityError(
                "WHOLE differs from overflow primitive/outcome"
            )
