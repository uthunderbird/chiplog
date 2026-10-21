"""Physical codecs for executable scheduler overflow holds.

Decoding establishes only byte and identity integrity.  It does not establish
journal selection or publication authority.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .recovery_contracts import Identity, Present
from .scheduler_contracts import (
    OverflowActive,
    OverflowResolved,
    SchedulerCommandIdentity,
    SchedulerOverflowHold,
)
from .scheduler_execution_contracts import (
    FinalizedOverflowHoldV2,
    OverflowHoldPrimitiveV2,
    SchedulerExecutionDTO,
)
from .scheduler_materialization import SchedulerCanonicalMember
from .scheduler_seed_producer_contracts import (
    overflow_primitive_reference,
    scheduler_execution_command_reference,
)

OVERFLOW_HOLD_RECORD_KIND = "overflow-hold"
OVERFLOW_HOLD_V2_SCHEMA = "chiplog.scheduler.overflow-hold.v2"


class OverflowHoldRecordIntegrityError(ValueError):
    """A fixed overflow-hold physical row disagrees with its bytes."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _physical_reference(member: SchedulerCanonicalMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _member_bytes(member: SchedulerCanonicalMember) -> bytes:
    try:
        return base64.b64decode(member.canonical_base64, validate=True)
    except ValueError as error:
        raise OverflowHoldRecordIntegrityError(
            "overflow hold canonical base64 is invalid"
        ) from error


def _canonical_member_bytes(member: SchedulerCanonicalMember) -> bytes:
    raw = _member_bytes(member)
    if base64.b64encode(raw).decode() != member.canonical_base64:
        raise OverflowHoldRecordIntegrityError("overflow hold canonical base64 is not canonical")
    return raw


def _require_member_shape(member: SchedulerCanonicalMember, schema_id: str) -> bytes:
    if member.record_kind != OVERFLOW_HOLD_RECORD_KIND or member.schema_id != schema_id:
        raise OverflowHoldRecordIntegrityError("overflow hold physical kind or schema differs")
    raw = _canonical_member_bytes(member)
    if member.fingerprint != _digest(raw):
        raise OverflowHoldRecordIntegrityError("overflow hold physical fingerprint differs")
    return raw


class ExecutableOverflowHoldRecordV2(SchedulerExecutionDTO):
    """The V2 durable body, deliberately excluding its external physical reference."""

    kind: Literal["EXECUTABLE_OVERFLOW_HOLD_RECORD_V2"] = "EXECUTABLE_OVERFLOW_HOLD_RECORD_V2"
    schema_id: Literal["chiplog.scheduler.overflow-hold.v2"] = "chiplog.scheduler.overflow-hold.v2"
    command: SchedulerCommandIdentity
    primitive: OverflowHoldPrimitiveV2
    operator_recovery_owner: Identity
    state: Annotated[OverflowActive | OverflowResolved, Field(discriminator="kind")]

    @model_validator(mode="after")
    def active_command_is_primitive_command(self) -> ExecutableOverflowHoldRecordV2:
        if isinstance(self.state, OverflowActive) and self.primitive.command != (
            scheduler_execution_command_reference(self.command)
        ):
            raise ValueError("active overflow hold command reference differs from primitive")
        return self


def executable_overflow_hold_reference(record: ExecutableOverflowHoldRecordV2) -> Present:
    raw = record.canonical_bytes()
    digest = _digest(raw)
    return Present(head="scheduler-overflow-hold-v2:" + digest, fingerprint=digest)


def executable_overflow_hold_member(
    record: ExecutableOverflowHoldRecordV2,
) -> SchedulerCanonicalMember:
    raw = record.canonical_bytes()
    reference = executable_overflow_hold_reference(record)
    return SchedulerCanonicalMember(
        record_kind=OVERFLOW_HOLD_RECORD_KIND,
        record_id=reference.head,
        schema_id=OVERFLOW_HOLD_V2_SCHEMA,
        canonical_base64=base64.b64encode(raw).decode(),
        fingerprint=reference.fingerprint,
    )


class DecodedExecutableOverflowHoldRecordV2(SchedulerExecutionDTO):
    """One verified V2 physical row, including terminal successor states."""

    kind: Literal["DECODED_EXECUTABLE_OVERFLOW_HOLD_RECORD_V2"] = (
        "DECODED_EXECUTABLE_OVERFLOW_HOLD_RECORD_V2"
    )
    record: ExecutableOverflowHoldRecordV2
    canonical_record_bytes: bytes
    physical_record: Present


class SelectedExecutableActiveOverflowHoldV2(SchedulerExecutionDTO):
    kind: Literal["SELECTED_EXECUTABLE_ACTIVE_OVERFLOW_HOLD_V2"] = (
        "SELECTED_EXECUTABLE_ACTIVE_OVERFLOW_HOLD_V2"
    )
    record: ExecutableOverflowHoldRecordV2
    hold: SchedulerOverflowHold
    physical_record: Present


def _project_v2_hold(record: ExecutableOverflowHoldRecordV2) -> SchedulerOverflowHold:
    reference = executable_overflow_hold_reference(record)
    primitive = record.primitive
    return SchedulerOverflowHold(
        hold=reference,
        command=record.command,
        boundary=primitive.boundary,
        bound_head=primitive.bound_head,
        exceeded_dimension=primitive.exceeded_dimension,
        actual_value=primitive.actual_value,
        limit=primitive.limit,
        evidence=primitive.evidence,
        operator_recovery_owner=record.operator_recovery_owner,
        state=record.state,
    )


def decode_executable_overflow_hold_record(
    member: SchedulerCanonicalMember,
) -> DecodedExecutableOverflowHoldRecordV2:
    """Decode one fixed V2 row, including a resolved terminal successor."""

    raw = _require_member_shape(member, OVERFLOW_HOLD_V2_SCHEMA)
    try:
        record = ExecutableOverflowHoldRecordV2.model_validate_json(raw)
    except ValidationError as error:
        raise OverflowHoldRecordIntegrityError("overflow hold V2 bytes are invalid") from error
    if record.canonical_bytes() != raw:
        raise OverflowHoldRecordIntegrityError("overflow hold V2 bytes are not canonical")
    reference = executable_overflow_hold_reference(record)
    if member.record_id != reference.head or member.fingerprint != reference.fingerprint:
        raise OverflowHoldRecordIntegrityError("overflow hold V2 physical identity differs")
    return DecodedExecutableOverflowHoldRecordV2(
        record=record,
        canonical_record_bytes=raw,
        physical_record=_physical_reference(member),
    )


def decode_selected_active_overflow_hold(
    member: SchedulerCanonicalMember,
) -> SelectedExecutableActiveOverflowHoldV2:
    """Decode an active executable hold under its registered schema."""

    if member.schema_id != OVERFLOW_HOLD_V2_SCHEMA:
        raise OverflowHoldRecordIntegrityError("unregistered overflow hold schema")
    decoded = decode_executable_overflow_hold_record(member)
    if not isinstance(decoded.record.state, OverflowActive):
        raise OverflowHoldRecordIntegrityError("resolved overflow hold cannot be selected active")
    return SelectedExecutableActiveOverflowHoldV2(
        record=decoded.record,
        hold=_project_v2_hold(decoded.record),
        physical_record=decoded.physical_record,
    )


def finalize_selected_overflow_hold(
    selected: SelectedExecutableActiveOverflowHoldV2,
) -> FinalizedOverflowHoldV2:
    """Return the exact executable hold and primitive finalization references."""

    return FinalizedOverflowHoldV2(
        hold=selected.physical_record,
        overflow_primitive=overflow_primitive_reference(selected.record.primitive),
    )
