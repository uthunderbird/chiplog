"""Canonical complete-owner bytes for scheduler execution V2 batches.

This module measures physical members only.  It does not select, authenticate,
or publish those members.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json

from .recovery_contracts import Identity, Present
from .scheduler_contracts import SchedulerIntervalBound
from .scheduler_execution_contracts import (
    PreparedFinalizedScheduledIntervalV2,
    ScheduledWholeIntervalEnvelopeV2,
    SchedulerExecutionDTO,
)
from .scheduler_materialization import SchedulerCanonicalMember

SCHEDULED_WHOLE_RECORD_KIND_V2 = "whole-envelope"
SCHEDULED_WHOLE_SCHEMA_V2 = "chiplog.scheduler.whole-envelope.v2"
_SCHEDULED_WHOLE_RECORD_ID_PREFIX_V2 = "scheduler-whole-envelope-v2:"


class ScheduledExecutionBatchValidationContextV2(SchedulerExecutionDTO):
    """Information absent from a physical member but retained by finalization."""

    ordered_member_subject_ids: tuple[Identity, ...]
    whole_reference: Present

    @classmethod
    def from_finalization(
        cls, finalization: PreparedFinalizedScheduledIntervalV2
    ) -> ScheduledExecutionBatchValidationContextV2:
        """Retain the fields needed to bind a physical batch to finalization.

        Re-validation deliberately executes the existing prepared-finalization
        membership validator before its subject IDs are used here.
        """

        checked = PreparedFinalizedScheduledIntervalV2.model_validate(finalization.model_dump())
        return cls(
            ordered_member_subject_ids=checked.complete_ordered_member_subject_ids,
            whole_reference=checked.whole_envelope.external_reference,
        )


def _ordered(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _ordered(value[key]) for key in sorted(value, key=lambda key: (key != "kind", key))
        }
    if isinstance(value, (list, tuple)):
        return [_ordered(item) for item in value]
    return value


def _canonical_bytes(records: tuple[SchedulerCanonicalMember, ...]) -> bytes:
    return json.dumps(
        _ordered([item.model_dump(mode="json") for item in records]),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def _member_bytes(member: SchedulerCanonicalMember) -> bytes:
    try:
        raw = base64.b64decode(member.canonical_base64, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("scheduler batch member has invalid canonical base64") from error
    if not raw or base64.b64encode(raw).decode() != member.canonical_base64:
        raise ValueError("scheduler batch member has noncanonical base64")
    if hashlib.sha256(raw).hexdigest() != member.fingerprint:
        raise ValueError("scheduler batch member fingerprint differs from canonical bytes")
    return raw


def _validate_terminal_whole(
    records: tuple[SchedulerCanonicalMember, ...],
    raw: bytes,
    context: ScheduledExecutionBatchValidationContextV2,
) -> None:
    whole = records[-1]
    if (
        whole.record_kind != SCHEDULED_WHOLE_RECORD_KIND_V2
        or whole.schema_id != SCHEDULED_WHOLE_SCHEMA_V2
    ):
        raise ValueError("final member must be the registered V2 WHOLE physical row")
    expected_record_id = _SCHEDULED_WHOLE_RECORD_ID_PREFIX_V2 + whole.fingerprint
    if whole.record_id != expected_record_id:
        raise ValueError("terminal WHOLE record ID differs from its fixed physical reference")
    try:
        body = ScheduledWholeIntervalEnvelopeV2.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("terminal WHOLE bytes do not decode as the registered V2 body") from error
    if raw != body.canonical_bytes():
        raise ValueError("terminal WHOLE bytes are not canonical body bytes")

    preceding = records[:-1]
    descriptors = body.ordered_non_envelope_members
    if len(descriptors) != len(preceding):
        raise ValueError("WHOLE descriptor count differs from physical batch membership")
    for member, descriptor in zip(preceding, descriptors, strict=True):
        if (
            descriptor.owner != "agent_loop"
            or descriptor.record_kind != member.record_kind
            or descriptor.record_id != member.record_id
            or descriptor.schema_id != member.schema_id
            or descriptor.fingerprint != member.fingerprint
        ):
            raise ValueError("WHOLE descriptor differs from ordered physical member")

    if len(context.ordered_member_subject_ids) != len(preceding):
        raise ValueError("WHOLE validation context subjects differ from physical membership")
    for descriptor, subject_id in zip(descriptors, context.ordered_member_subject_ids, strict=True):
        if descriptor.subject_id != subject_id:
            raise ValueError("WHOLE descriptor subject differs from finalization context")
    if (
        context.whole_reference.head != whole.record_id
        or context.whole_reference.fingerprint != whole.fingerprint
    ):
        raise ValueError("terminal WHOLE differs from finalization context reference")


def scheduled_execution_batch_bytes(
    records_including_whole: tuple[SchedulerCanonicalMember, ...],
    *,
    context: ScheduledExecutionBatchValidationContextV2,
) -> bytes:
    """Return the fixed V1-compatible wire for a complete V2 owner batch.

    The context binds descriptor subjects and the external WHOLE reference to a
    revalidated prepared finalization.
    """

    if not records_including_whole:
        raise ValueError("scheduler batch must contain a terminal WHOLE member")
    record_ids = tuple(member.record_id for member in records_including_whole)
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("scheduler batch cannot contain duplicate record identities")
    whole_indexes = tuple(
        index
        for index, member in enumerate(records_including_whole)
        if (
            member.record_kind == SCHEDULED_WHOLE_RECORD_KIND_V2
            and member.schema_id == SCHEDULED_WHOLE_SCHEMA_V2
        )
    )
    if whole_indexes != (len(records_including_whole) - 1,):
        raise ValueError("scheduler batch must contain exactly one registered WHOLE member last")
    raw_members = tuple(_member_bytes(member) for member in records_including_whole)
    _validate_terminal_whole(records_including_whole, raw_members[-1], context)
    return _canonical_bytes(records_including_whole)


def scheduled_execution_batch_bytes_within_bound(
    records_including_whole: tuple[SchedulerCanonicalMember, ...],
    bound: SchedulerIntervalBound,
    *,
    context: ScheduledExecutionBatchValidationContextV2,
) -> bytes:
    """Return a complete batch wire only when its final physical size is admitted."""

    encoded = scheduled_execution_batch_bytes(records_including_whole, context=context)
    if len(encoded) > bound.max_serialized_batch_bytes:
        raise ValueError("complete scheduler batch exceeds selected serialized-byte bound")
    return encoded
