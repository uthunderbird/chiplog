"""Closed retained source exchange for one successor Run revision ancestry."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import Field, StrictInt, ValidationError

from .call_acceptance_contracts import CallSubjectHead
from .execution_run_record_contracts import (
    DecodedExecutionRunMember,
    ExecutionRunCanonicalMember,
    ExecutionRunRecordIntegrityError,
    decode_execution_run_member,
)
from .recovery_contracts import Digest, Identity, Present, RecoveryDTO


class SelectedSuccessorRunSourceV1(RecoveryDTO):
    member: ExecutionRunCanonicalMember
    selected_decision: CallSubjectHead
    selected_commit_sequence: StrictInt = Field(gt=0, le=2**64 - 1)


class SuccessorAncestryCutV1(RecoveryDTO):
    tenant_id: Identity
    database_id: Identity
    tenant_commit_sequence: StrictInt = Field(ge=0, le=2**64 - 1)
    materialization_commitment: Digest
    independent_journal_head: CallSubjectHead


class ReadSuccessorRunAncestryV1(RecoveryDTO):
    kind: Literal["READ_SUCCESSOR_RUN_ANCESTRY_V1"] = "READ_SUCCESSOR_RUN_ANCESTRY_V1"
    schema_id: Literal["chiplog.execution.read-successor-run-ancestry.v1"] = (
        "chiplog.execution.read-successor-run-ancestry.v1"
    )
    command_id: Identity
    cut: SuccessorAncestryCutV1
    prior_epoch_observation: CallSubjectHead
    prior_lineage_advance: CallSubjectHead
    start_run: CallSubjectHead
    end_run: CallSubjectHead


class ReadSuccessorRunAncestryResultV1(RecoveryDTO):
    kind: Literal["SUCCESSOR_RUN_ANCESTRY_V1"] = "SUCCESSOR_RUN_ANCESTRY_V1"
    schema_id: Literal["chiplog.execution.successor-run-ancestry.v1"] = (
        "chiplog.execution.successor-run-ancestry.v1"
    )
    source_request_fingerprint: Digest
    cut: SuccessorAncestryCutV1
    prior_epoch_observation: CallSubjectHead
    prior_lineage_advance: CallSubjectHead
    prior_successor_selected_decision: CallSubjectHead
    prior_successor_commit_sequence: StrictInt = Field(gt=0, le=2**64 - 1)
    ordered_runs: tuple[SelectedSuccessorRunSourceV1, ...] = Field(min_length=1)


class SuccessorAncestryReadFailureV1(RecoveryDTO):
    kind: Literal["SUCCESSOR_RUN_ANCESTRY_READ_FAILURE_V1"] = (
        "SUCCESSOR_RUN_ANCESTRY_READ_FAILURE_V1"
    )
    source_request_fingerprint: Digest
    code: Literal["STALE_CUT", "NOT_SELECTED", "NOT_ANCESTOR", "INTEGRITY_FAULT"]
    reason: Identity


SuccessorRunAncestryReadResultV1 = Annotated[
    ReadSuccessorRunAncestryResultV1 | SuccessorAncestryReadFailureV1,
    Field(discriminator="kind"),
]


class SuccessorRunAncestryReader(Protocol):
    async def read_successor_run_ancestry(
        self, request: ReadSuccessorRunAncestryV1
    ) -> SuccessorRunAncestryReadResultV1: ...


class SuccessorAncestryIntegrityError(ValueError):
    pass


def successor_ancestry_request_fingerprint(request: ReadSuccessorRunAncestryV1) -> str:
    return hashlib.sha256(
        b"chiplog.execution.read-successor-run-ancestry.v1\x00" + request.canonical_bytes()
    ).hexdigest()


def validate_successor_run_ancestry_exchange(
    request: ReadSuccessorRunAncestryV1,
    result: SuccessorRunAncestryReadResultV1,
) -> tuple[DecodedExecutionRunMember, ...]:
    """Validate representation joins; broker reader authentication remains external."""
    if not isinstance(result, ReadSuccessorRunAncestryResultV1):
        raise SuccessorAncestryIntegrityError("ancestry reader did not return success")
    try:
        request = ReadSuccessorRunAncestryV1.model_validate_json(request.canonical_bytes())
        result = ReadSuccessorRunAncestryResultV1.model_validate_json(result.canonical_bytes())
    except ValidationError as error:
        raise SuccessorAncestryIntegrityError("invalid retained ancestry exchange") from error
    if result.source_request_fingerprint != successor_ancestry_request_fingerprint(request):
        raise SuccessorAncestryIntegrityError("ancestry request fingerprint differs")
    if (
        result.cut != request.cut
        or result.prior_epoch_observation != request.prior_epoch_observation
    ):
        raise SuccessorAncestryIntegrityError("ancestry retained source context differs")
    if result.prior_lineage_advance != request.prior_lineage_advance:
        raise SuccessorAncestryIntegrityError("ancestry prior lineage differs")
    try:
        decoded = tuple(decode_execution_run_member(value.member) for value in result.ordered_runs)
    except ExecutionRunRecordIntegrityError as error:
        raise SuccessorAncestryIntegrityError("invalid ancestry Run member") from error
    first, last = decoded[0], decoded[-1]
    first_ref = CallSubjectHead(
        subject_id=first.run.run_id,
        revision=Present(head=first.member.record_id, fingerprint=first.member.fingerprint),
    )
    last_ref = CallSubjectHead(
        subject_id=last.run.run_id,
        revision=Present(head=last.member.record_id, fingerprint=last.member.fingerprint),
    )
    if first_ref != request.start_run or last_ref != request.end_run:
        raise SuccessorAncestryIntegrityError("ancestry endpoints differ")
    if first.run.state != "CREATED" or last.run.state != "SUSPENDED":
        raise SuccessorAncestryIntegrityError("ancestry endpoint states differ")
    base = first.run
    if base.tenant != request.cut.tenant_id:
        raise SuccessorAncestryIntegrityError("ancestry Run tenant differs from cut")
    if (
        result.ordered_runs[0].selected_decision != result.prior_successor_selected_decision
        or result.ordered_runs[0].selected_commit_sequence != result.prior_successor_commit_sequence
    ):
        raise SuccessorAncestryIntegrityError("ancestry first selection differs")
    heads: set[str] = set()
    previous_sequence = 0
    previous = None
    for source, value in zip(result.ordered_runs, decoded, strict=True):
        run = value.run
        if value.member.record_id in heads:
            raise SuccessorAncestryIntegrityError("ancestry duplicate physical Run")
        heads.add(value.member.record_id)
        if (
            source.selected_commit_sequence <= previous_sequence
            or source.selected_commit_sequence > request.cut.tenant_commit_sequence
        ):
            raise SuccessorAncestryIntegrityError("ancestry selection sequence differs")
        previous_sequence = source.selected_commit_sequence
        if (
            run.run_id,
            run.tenant,
            run.principal,
            run.schema_id,
            run.root_binding,
        ) != (base.run_id, base.tenant, base.principal, base.schema_id, base.root_binding):
            raise SuccessorAncestryIntegrityError("ancestry Run identity differs")
        if previous is not None and run.predecessor != previous.run.head:
            raise SuccessorAncestryIntegrityError("ancestry Run predecessor differs")
        previous = value
    return decoded
