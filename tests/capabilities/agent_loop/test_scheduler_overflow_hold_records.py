"""Integrity checks for scheduler overflow-hold rows."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from pydantic import ValidationError
from tests.support.scheduler_overflow_hold_records import (
    v2_overflow_hold_member,
    v2_overflow_hold_record,
)
from tests.support.scheduler_seed_producer import overflow_request

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import OverflowActive
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    FinalizedOverflowHoldV2,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerCanonicalMember
from chiplog.capabilities.agent_loop.scheduler_overflow_hold_records import (
    ExecutableOverflowHoldRecordV2,
    OverflowHoldRecordIntegrityError,
    SelectedExecutableActiveOverflowHoldV2,
    decode_executable_overflow_hold_record,
    decode_selected_active_overflow_hold,
    executable_overflow_hold_member,
    finalize_selected_overflow_hold,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    AutomaticSchedulerCycleRequestV2,
    automatic_request_reference,
    overflow_primitive_reference,
)


def _with_raw(
    member: SchedulerCanonicalMember, raw: bytes, **updates: object
) -> SchedulerCanonicalMember:
    return member.model_copy(
        update={
            "canonical_base64": base64.b64encode(raw).decode(),
            "fingerprint": hashlib.sha256(raw).hexdigest(),
            **updates,
        }
    )


def test_v2_body_projects_to_domain_hold_and_finalization_uses_v2_references() -> None:
    record = v2_overflow_hold_record()
    member = executable_overflow_hold_member(record)
    selected = decode_selected_active_overflow_hold(member)
    assert isinstance(selected, SelectedExecutableActiveOverflowHoldV2)
    assert selected.hold.hold == selected.physical_record
    assert selected.hold.command == record.command
    finalized = finalize_selected_overflow_hold(selected)
    assert isinstance(finalized, FinalizedOverflowHoldV2)
    assert finalized.hold == selected.physical_record
    assert finalized.overflow_primitive == overflow_primitive_reference(record.primitive)


def test_general_v2_decoder_preserves_resolved_terminal_successor_bytes_and_reference() -> None:
    member = v2_overflow_hold_member(resolved=True)
    decoded = decode_executable_overflow_hold_record(member)
    assert decoded.record.state.kind == "RESOLVED_BY_OPERATOR"
    assert decoded.canonical_record_bytes == base64.b64decode(member.canonical_base64)
    assert decoded.physical_record == Present(head=member.record_id, fingerprint=member.fingerprint)
    with pytest.raises(OverflowHoldRecordIntegrityError, match="resolved"):
        decode_selected_active_overflow_hold(member)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-id",
        "wrong-hash",
        "relabel",
        "noncanonical",
        "wrong-native-bytes",
        "wrong-command",
        "resolved",
    ],
)
def test_v2_physical_and_body_mutants_reject_or_cannot_enter_active_selection(
    mutation: str,
) -> None:
    member = v2_overflow_hold_member(resolved=mutation == "resolved")
    raw = base64.b64decode(member.canonical_base64)
    if mutation == "wrong-id":
        forged = member.model_copy(update={"record_id": "scheduler-overflow-hold-v2:" + "0" * 64})
    elif mutation == "wrong-hash":
        forged = member.model_copy(update={"fingerprint": "0" * 64})
    elif mutation == "relabel":
        forged = member.model_copy(update={"schema_id": "unknown.overflow-hold"})
    elif mutation == "noncanonical":
        forged = _with_raw(member, json.dumps(json.loads(raw), indent=1).encode())
    elif mutation == "wrong-native-bytes":
        record = ExecutableOverflowHoldRecordV2.model_validate_json(raw)
        forged = _with_raw(
            member,
            record.model_copy(
                update={"operator_recovery_owner": "other-operator"}
            ).canonical_bytes(),
        )
    elif mutation == "wrong-command":
        record = ExecutableOverflowHoldRecordV2.model_validate_json(raw)
        primitive = record.primitive.model_copy(
            update={"command": record.primitive.command.model_copy(update={"subject_id": "wrong"})}
        )
        forged = _with_raw(
            member, record.model_copy(update={"primitive": primitive}).canonical_bytes()
        )
    else:
        forged = member
    with pytest.raises(OverflowHoldRecordIntegrityError):
        decode_selected_active_overflow_hold(forged)


def test_v2_active_requires_scheduler_execution_command_reference_not_request_reference() -> None:
    record = v2_overflow_hold_record()
    with pytest.raises(ValidationError, match="command reference"):
        ExecutableOverflowHoldRecordV2(
            command=record.command,
            primitive=record.primitive.model_copy(
                update={
                    "command": automatic_request_reference(
                        AutomaticSchedulerCycleRequestV2.model_validate_json(
                            overflow_request().source.observation.canonical_request_bytes
                        )
                    )
                }
            ),
            operator_recovery_owner=record.operator_recovery_owner,
            state=OverflowActive(),
        )
