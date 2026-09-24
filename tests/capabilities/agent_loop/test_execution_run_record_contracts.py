"""Representation tests for closed physical v2/v3 Run members."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError
from tests.support.execution_fan_out import fixture as execution_fixture
from tests.support.execution_versions import execution_run_v3

from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    EXECUTION_RUN_V2_SCHEMA,
    EXECUTION_RUN_V3_SCHEMA,
    DecodedExecutionRunMember,
    ExecutionRunCanonicalMember,
    ExecutionRunRecordIntegrityError,
    decode_execution_run_member,
    validate_execution_run_result_member,
)
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun

_STATES = (
    "CREATED",
    "ACTIVE",
    "SUCCEEDED",
    "SUSPENDED",
    "SUPERSEDED",
    "ABORTED",
    "CANCELLED",
)


def _native(run: ExecutionRun, state: str) -> ExecutionRun:
    pending = run.model_copy(update={"state": state, "head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _member(run: ExecutionRun) -> ExecutionRunCanonicalMember:
    raw = run.canonical_bytes()
    return ExecutionRunCanonicalMember(
        record_id=run.head,
        schema_id=run.schema_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


async def _run(version: str, state: str) -> ExecutionRun:
    run = execution_run_v3() if version == "v3" else (await execution_fixture()).captured_run
    return _native(run, state)


@pytest.mark.parametrize("version", ("v2", "v3"))
@pytest.mark.parametrize("state", _STATES)
async def test_decodes_each_native_run_version_and_state(version: str, state: str) -> None:
    run = await _run(version, state)
    member = _member(run)

    decoded = decode_execution_run_member(member)

    assert decoded.member is member
    assert decoded.run == run
    assert decoded.member.canonical_record_bytes == run.canonical_bytes()
    assert decoded.member.schema_id == (
        EXECUTION_RUN_V3_SCHEMA if version == "v3" else EXECUTION_RUN_V2_SCHEMA
    )
    validate_execution_run_result_member(decoded, run)


async def test_representation_can_retain_independent_v2_and_v3_run_members() -> None:
    """A mixed-version pair is representable here without asserting a transition."""

    predecessor = await _run("v2", "SUPERSEDED")
    successor = await _run("v3", "ACTIVE")
    pair = (
        decode_execution_run_member(_member(predecessor)),
        decode_execution_run_member(_member(successor)),
    )

    assert pair[0].run.schema_id == EXECUTION_RUN_V2_SCHEMA
    assert pair[1].run.schema_id == EXECUTION_RUN_V3_SCHEMA
    validate_execution_run_result_member(pair[0], predecessor)
    validate_execution_run_result_member(pair[1], successor)


async def test_decoder_rejects_owner_kind_schema_hash_head_and_byte_mutations() -> None:
    run = await _run("v2", "ACTIVE")
    member = _member(run)

    mutations = (
        member.model_copy(update={"owner": "rival"}),
        member.model_copy(update={"record_kind": "RivalRun"}),
        member.model_copy(update={"schema_id": "chiplog.rival.v1"}),
        member.model_copy(update={"fingerprint": "0" * 64}),
        member.model_copy(
            update={
                "canonical_record_bytes": member.canonical_record_bytes + b" ",
                "fingerprint": hashlib.sha256(member.canonical_record_bytes + b" ").hexdigest(),
            }
        ),
    )
    for mutant in mutations:
        with pytest.raises(ExecutionRunRecordIntegrityError):
            decode_execution_run_member(mutant)

    invalid_head = run.model_copy(update={"head": "loop:wrong"})
    with pytest.raises(ExecutionRunRecordIntegrityError):
        decode_execution_run_member(_member(invalid_head))


async def test_owner_result_join_rejects_rehashed_different_run_body() -> None:
    expected = await _run("v3", "ACTIVE")
    substituted = _native(
        expected.model_copy(update={"event": "DifferentRepresentation"}), "ACTIVE"
    )
    decoded = decode_execution_run_member(_member(substituted))

    with pytest.raises(ExecutionRunRecordIntegrityError):
        validate_execution_run_result_member(decoded, expected)


async def test_owner_result_join_rejects_decoded_carrier_run_substitution() -> None:
    run = await _run("v2", "SUSPENDED")
    decoded = decode_execution_run_member(_member(run))
    altered = _native(run.model_copy(update={"event": "AlteredCarrier"}), "SUSPENDED")

    with pytest.raises(ExecutionRunRecordIntegrityError):
        validate_execution_run_result_member(
            DecodedExecutionRunMember(member=decoded.member, run=altered), run
        )


def test_member_is_strict_frozen_and_serializes_binary_field_as_base64() -> None:
    with pytest.raises(ValidationError):
        ExecutionRunCanonicalMember.model_validate(
            {
                "record_id": "run",
                "schema_id": EXECUTION_RUN_V2_SCHEMA,
                "canonical_record_bytes": b"bytes",
                "fingerprint": "a" * 64,
                "unexpected": "field",
            }
        )

    member = ExecutionRunCanonicalMember(
        record_id="run",
        schema_id=EXECUTION_RUN_V2_SCHEMA,
        canonical_record_bytes=b"\xff\x00",
        fingerprint="a" * 64,
    )
    assert member.model_dump(mode="json")["canonical_record_bytes"] == "_wA="
