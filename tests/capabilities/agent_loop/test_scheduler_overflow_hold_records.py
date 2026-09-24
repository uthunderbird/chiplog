"""Fixed compatibility and integrity checks for scheduler overflow-hold rows."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from pydantic import ValidationError
from tests.support.scheduler_overflow_hold_records import (
    v1_overflow_hold_member,
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
    OVERFLOW_HOLD_V1_SCHEMA,
    OVERFLOW_HOLD_V2_SCHEMA,
    ExecutableOverflowHoldRecordV2,
    OverflowHoldRecordIntegrityError,
    SelectedExecutableActiveOverflowHoldV2,
    SelectedLegacyActiveOverflowHoldV1,
    UnsupportedLegacyOverflowHoldExecutionV2,
    decode_executable_overflow_hold_record,
    decode_selected_active_overflow_hold,
    executable_overflow_hold_member,
    finalize_selected_overflow_hold,
    scheduler_hold_canonical_bytes,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    AutomaticSchedulerCycleRequestV2,
    automatic_request_reference,
    overflow_primitive_reference,
)

V1_PRODUCER_ROW_SHA256 = "e41c2ae7c22b93204fa622ecaf465a6e859225dee0193834797ef695c7e364a0"
BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


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


def test_actual_v1_producer_row_keeps_semantic_hold_reference_distinct_from_physical_hash() -> None:
    member = v1_overflow_hold_member()
    selected = decode_selected_active_overflow_hold(member)
    assert isinstance(selected, SelectedLegacyActiveOverflowHoldV1)
    assert member.schema_id == OVERFLOW_HOLD_V1_SCHEMA
    raw = base64.b64decode(member.canonical_base64)
    assert hashlib.sha256(raw).hexdigest() == V1_PRODUCER_ROW_SHA256
    assert raw == selected.hold.canonical_bytes()
    assert member.fingerprint == hashlib.sha256(raw).hexdigest()
    assert selected.hold.hold.head == member.record_id
    assert selected.hold.hold.fingerprint != member.fingerprint
    exact_inner = {
        "command": selected.hold.command.model_dump(mode="json"),
        "boundary": selected.hold.boundary.model_dump(mode="json"),
        "bound_head": selected.hold.bound_head.model_dump(mode="json"),
        "dimension": selected.hold.exceeded_dimension,
        "actual": selected.hold.actual_value,
        "limit": selected.hold.limit,
        "evidence": selected.hold.evidence.model_dump(mode="json"),
        "operator": selected.hold.operator_recovery_owner,
    }
    inner = scheduler_hold_canonical_bytes(exact_inner)
    digest = hashlib.sha256(inner).hexdigest()
    assert selected.hold.hold == Present(
        head="scheduler-overflow-hold-v1:" + digest, fingerprint=digest
    )
    assert b'"evidence":{"kind":' in inner


@pytest.mark.parametrize(
    "mutation",
    [
        "fingerprint",
        "semantic",
        "relabel",
        "noncanonical",
        "noncanonical-base64",
        "inactive",
        "not-present",
    ],
)
def test_v1_integrity_and_compatibility_mutants_reject(mutation: str) -> None:
    member = v1_overflow_hold_member()
    raw = base64.b64decode(member.canonical_base64)
    if mutation == "fingerprint":
        forged = member.model_copy(update={"fingerprint": "0" * 64})
    elif mutation == "semantic":
        value = json.loads(raw)
        value["hold"]["head"] = "scheduler-overflow-hold-v1:" + "0" * 64
        forged = _with_raw(member, scheduler_hold_canonical_bytes(value))
    elif mutation == "relabel":
        forged = member.model_copy(update={"schema_id": OVERFLOW_HOLD_V2_SCHEMA})
    elif mutation == "noncanonical":
        forged = _with_raw(member, json.dumps(json.loads(raw), indent=1).encode())
    elif mutation == "noncanonical-base64":
        encoded = member.canonical_base64
        assert encoded.endswith("==")
        final = BASE64_ALPHABET.index(encoded[-3])
        alternate = encoded[:-3] + BASE64_ALPHABET[final ^ 1] + encoded[-2:]
        assert base64.b64decode(alternate, validate=True) == raw
        forged = member.model_copy(update={"canonical_base64": alternate})
    elif mutation == "inactive":
        value = json.loads(raw)
        value["state"] = {"kind": "RESOLVED_BY_OPERATOR", "resolution_decision": value["hold"]}
        forged = _with_raw(member, scheduler_hold_canonical_bytes(value))
    else:
        value = json.loads(raw)
        value["hold"] = {"kind": "ABSENT"}
        forged = _with_raw(member, scheduler_hold_canonical_bytes(value))
    with pytest.raises(OverflowHoldRecordIntegrityError):
        decode_selected_active_overflow_hold(forged)


def test_v2_body_projects_to_legacy_shape_and_finalization_uses_v2_references() -> None:
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
        forged = member.model_copy(update={"schema_id": OVERFLOW_HOLD_V1_SCHEMA})
    elif mutation == "noncanonical":
        forged = _with_raw(member, json.dumps(json.loads(raw), indent=1).encode())
    elif mutation == "wrong-native-bytes":
        value = json.loads(raw)
        value["operator_recovery_owner"] = "other-operator"
        forged = _with_raw(member, scheduler_hold_canonical_bytes(value))
    elif mutation == "wrong-command":
        value = json.loads(raw)
        value["primitive"]["command"]["subject_id"] = "wrong"
        forged = _with_raw(member, scheduler_hold_canonical_bytes(value))
    else:
        forged = member
    with pytest.raises(OverflowHoldRecordIntegrityError):
        decode_selected_active_overflow_hold(forged)


def test_legacy_active_hold_returns_typed_unsupported_without_fabricating_v2_provenance() -> None:
    selected = decode_selected_active_overflow_hold(v1_overflow_hold_member())
    result = finalize_selected_overflow_hold(selected)
    assert isinstance(result, UnsupportedLegacyOverflowHoldExecutionV2)
    assert result.code == "UNSUPPORTED"


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
