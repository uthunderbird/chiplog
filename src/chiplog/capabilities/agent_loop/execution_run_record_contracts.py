"""Closed physical member codec for representation-only executable Runs.

This module proves that one retained owner member is the exact native v2 or v3
Run it claims to carry.  It neither validates state transitions nor authorizes
publication.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from .contracts import Frozen
from .execution_run_versions import ExecutionRun

AGENT_LOOP_OWNER: Literal["agent_loop"] = "agent_loop"
EXECUTION_RUN_V2_SCHEMA: Literal["chiplog.agent-loop.execution-record.v2"] = (
    "chiplog.agent-loop.execution-record.v2"
)
EXECUTION_RUN_V3_SCHEMA: Literal["chiplog.agent-loop.execution-record.v3"] = (
    "chiplog.agent-loop.execution-record.v3"
)


class ExecutionRunRecordIntegrityError(ValueError):
    """A physical Run member disagrees with its exact native Run bytes."""


class ExecutionRunCanonicalMember(Frozen):
    """One fixed ``agent_loop``/``Run`` member with base64 wire bytes."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    owner: Literal["agent_loop"] = AGENT_LOOP_OWNER
    record_kind: Literal["Run"] = "Run"
    record_id: str = Field(min_length=1)
    schema_id: Literal[
        "chiplog.agent-loop.execution-record.v2", "chiplog.agent-loop.execution-record.v3"
    ]
    canonical_record_bytes: bytes = Field(min_length=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ExecutionRunRecordRow:
    owner: Literal["agent_loop"]
    record_kind: Literal["Run"]
    schema_id: Literal[
        "chiplog.agent-loop.execution-record.v2", "chiplog.agent-loop.execution-record.v3"
    ]


EXECUTION_RUN_RECORD_ROWS = (
    ExecutionRunRecordRow(AGENT_LOOP_OWNER, "Run", EXECUTION_RUN_V2_SCHEMA),
    ExecutionRunRecordRow(AGENT_LOOP_OWNER, "Run", EXECUTION_RUN_V3_SCHEMA),
)
_ROWS = {(row.owner, row.record_kind, row.schema_id): row for row in EXECUTION_RUN_RECORD_ROWS}
_EXECUTION_RUN_ADAPTER: TypeAdapter[ExecutionRun] = TypeAdapter(ExecutionRun)


@dataclass(frozen=True)
class DecodedExecutionRunMember:
    """The original member and its decoded Run, retained without byte rewriting."""

    member: ExecutionRunCanonicalMember
    run: ExecutionRun


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise ExecutionRunRecordIntegrityError(reason)


def _native_head(run: ExecutionRun) -> str:
    return "loop:" + run.model_copy(update={"head": "pending"}).digest()


def decode_execution_run_member(
    member: ExecutionRunCanonicalMember,
) -> DecodedExecutionRunMember:
    """Decode one exact closed v2/v3 Run member, preserving supplied bytes."""

    if (member.owner, member.record_kind, member.schema_id) not in _ROWS:
        raise ExecutionRunRecordIntegrityError("unregistered execution Run owner, kind, or schema")
    try:
        run: ExecutionRun = _EXECUTION_RUN_ADAPTER.validate_json(member.canonical_record_bytes)
    except ValidationError as error:
        raise ExecutionRunRecordIntegrityError("invalid execution Run bytes") from error
    _require(run.schema_id, member.schema_id, "execution Run schema differs from physical schema")
    _require(
        run.canonical_bytes(),
        member.canonical_record_bytes,
        "execution Run bytes are not canonical",
    )
    _require(
        hashlib.sha256(member.canonical_record_bytes).hexdigest(),
        member.fingerprint,
        "execution Run fingerprint differs from physical bytes",
    )
    _require(member.record_id, run.head, "execution Run physical ID differs from native head")
    _require(run.head, _native_head(run), "execution Run native head differs")
    return DecodedExecutionRunMember(member=member, run=run)


def validate_execution_run_result_member(
    decoded: DecodedExecutionRunMember, expected: ExecutionRun
) -> None:
    """Prove an owner result retains exactly the supplied typed Run reference."""

    redecoded = decode_execution_run_member(decoded.member)
    _require(decoded.run, redecoded.run, "decoded execution Run differs from retained member")
    _require(
        expected.schema_id,
        redecoded.member.schema_id,
        "expected execution Run schema differs",
    )
    _require(expected.head, _native_head(expected), "expected execution Run native head differs")
    _require(expected, redecoded.run, "expected execution Run value differs from retained member")
    _require(
        expected.head,
        redecoded.member.record_id,
        "expected execution Run physical ID differs",
    )
    _require(
        expected.canonical_bytes(),
        redecoded.member.canonical_record_bytes,
        "expected execution Run bytes differ from retained member",
    )
