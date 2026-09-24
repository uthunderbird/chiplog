"""Structural decoders for retained broker recovery-proof source observations.

These decoders establish an exact source-body shape and identity only.  They do
not authenticate an issuer, prove a current CAS decision, or authorize a retry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import ConfigDict, Field, ValidationError

from .call_acceptance_contracts import CallSubjectHead
from .model_attempt_recovery_contracts import RegisteredModelNoExposure
from .readonly_execution_contracts import RegisteredReadOnlyProof
from .readonly_history_tool_contracts import ConversationHistoryQuery
from .recovery_contracts import Digest, Identity, RecoveryDTO, UInt64

BROKER_SOURCE_OWNER: Final[Literal["broker"]] = "broker"
READONLY_HISTORY_PROOF_READER: Final = "readonly-history-proof-v1"
MODEL_PRE_EMISSION_CAS_READER: Final = "model-pre-emission-cas-v1"
READONLY_HISTORY_PROOF_SCHEMA: Final[Literal["chiplog.source.readonly-history-proof.v1"]] = (
    "chiplog.source.readonly-history-proof.v1"
)
MODEL_PRE_EMISSION_CAS_SCHEMA: Final[Literal["chiplog.source.model-pre-emission-cas.v1"]] = (
    "chiplog.source.model-pre-emission-cas.v1"
)


class RecoverySourceIntegrityError(ValueError):
    """A retained source member differs from its exact consumer expectation."""


class RecoveryProofSourceMember(RecoveryDTO):
    """A broker-retained source observation, deliberately not an owner record."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    source_owner: Literal["broker"] = BROKER_SOURCE_OWNER
    reader_id: Identity
    source_kind: Identity
    schema_id: Identity
    logical_subject_id: Identity
    source_record_id: Identity
    canonical_source_bytes: bytes = Field(min_length=1)
    fingerprint: Digest


class ReadOnlyHistoryProofSource(RecoveryDTO):
    """Hash-free body for a registered immutable conversation-history proof."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    schema_id: Literal["chiplog.source.readonly-history-proof.v1"] = READONLY_HISTORY_PROOF_SCHEMA
    registry: CallSubjectHead
    tool_schema: CallSubjectHead
    tool_policy: CallSubjectHead
    implementation: CallSubjectHead
    query_identity: CallSubjectHead
    canonical_query_bytes: bytes = Field(min_length=1)
    snapshot: CallSubjectHead
    snapshot_frontier: UInt64
    snapshot_contract: CallSubjectHead
    tenant_id: Identity
    database_id: Identity
    principal_id: Identity
    contour_head: Identity
    registered_read_operation: CallSubjectHead
    authority_read_manifest: CallSubjectHead
    immutable_snapshot_selection: CallSubjectHead


class ModelPreEmissionCASSource(RecoveryDTO):
    """Hash-free body for an observed selected pre-emission CAS proposal."""

    schema_id: Literal["chiplog.source.model-pre-emission-cas.v1"] = MODEL_PRE_EMISSION_CAS_SCHEMA
    registry: CallSubjectHead
    original_run: CallSubjectHead
    original_attempt: CallSubjectHead
    immutable_request: CallSubjectHead
    visibility_manifest: CallSubjectHead
    lineage_id: Identity
    selector_generation: UInt64
    provider_contract: Identity
    recipient: Identity
    observed_emission_head: CallSubjectHead
    tenant_id: Identity
    database_id: Identity
    authenticated_model_adapter: CallSubjectHead
    model_worker_session: CallSubjectHead
    emission_state: Literal["NOT_EMITTED"] = "NOT_EMITTED"
    permanently_fenced_generation: UInt64
    selected_pre_emission_cas_decision: CallSubjectHead


@dataclass(frozen=True)
class RecoveryProofSourceRow:
    reader_id: str
    source_owner: str
    source_kind: str
    schema_id: str
    body_type: type[RecoveryDTO]


RECOVERY_PROOF_SOURCE_ROWS = (
    RecoveryProofSourceRow(
        READONLY_HISTORY_PROOF_READER,
        BROKER_SOURCE_OWNER,
        "READONLY_HISTORY_PROOF",
        READONLY_HISTORY_PROOF_SCHEMA,
        ReadOnlyHistoryProofSource,
    ),
    RecoveryProofSourceRow(
        MODEL_PRE_EMISSION_CAS_READER,
        BROKER_SOURCE_OWNER,
        "MODEL_PRE_EMISSION_CAS",
        MODEL_PRE_EMISSION_CAS_SCHEMA,
        ModelPreEmissionCASSource,
    ),
)


def _row(member: RecoveryProofSourceMember) -> RecoveryProofSourceRow:
    for row in RECOVERY_PROOF_SOURCE_ROWS:
        if (
            member.reader_id,
            member.source_owner,
            member.source_kind,
            member.schema_id,
        ) == (row.reader_id, row.source_owner, row.source_kind, row.schema_id):
            return row
    raise RecoverySourceIntegrityError("unknown recovery proof source row")


def _decode(
    member: RecoveryProofSourceMember,
    expected: type[ReadOnlyHistoryProofSource] | type[ModelPreEmissionCASSource],
) -> ReadOnlyHistoryProofSource | ModelPreEmissionCASSource:
    row = _row(member)
    if row.body_type is not expected:
        raise RecoverySourceIntegrityError("source row is not accepted by this decoder")
    digest = hashlib.sha256(member.canonical_source_bytes).hexdigest()
    if member.fingerprint != digest:
        raise RecoverySourceIntegrityError("source fingerprint differs from canonical bytes")
    if member.source_record_id != f"{member.schema_id}:{digest}":
        raise RecoverySourceIntegrityError("source record identity differs from canonical bytes")
    try:
        body = expected.model_validate_json(member.canonical_source_bytes)
    except (TypeError, UnicodeDecodeError, ValidationError) as error:
        raise RecoverySourceIntegrityError("malformed source body") from error
    if body.canonical_bytes() != member.canonical_source_bytes:
        raise RecoverySourceIntegrityError("source body bytes are not canonical")
    if body.schema_id != member.schema_id:
        raise RecoverySourceIntegrityError("embedded source schema differs from envelope")
    if not isinstance(body, (ReadOnlyHistoryProofSource, ModelPreEmissionCASSource)):
        raise RecoverySourceIntegrityError("decoded an unsupported source body")
    return body


def _proof_head_matches(member: RecoveryProofSourceMember, proof: CallSubjectHead) -> bool:
    return (
        proof.subject_id == member.logical_subject_id
        and proof.revision.head == member.source_record_id
        and proof.revision.fingerprint == member.fingerprint
    )


def decode_readonly_history_proof(
    member: RecoveryProofSourceMember,
    enclosing: RegisteredReadOnlyProof,
    *,
    expected_tenant_id: Identity,
    expected_database_id: Identity,
    expected_principal_id: Identity,
    expected_contour_head: Identity,
) -> ReadOnlyHistoryProofSource:
    """Decode one exact read-only proof proposal against an external selected cut."""
    body = _decode(member, ReadOnlyHistoryProofSource)
    assert isinstance(body, ReadOnlyHistoryProofSource)
    if (
        member.logical_subject_id != enclosing.query_identity.subject_id
        or not _proof_head_matches(member, enclosing.no_mutation_proof)
        or enclosing.proof_schema != member.schema_id
        or enclosing.canonical_proof_bytes != member.canonical_source_bytes
    ):
        raise RecoverySourceIntegrityError("read-only enclosing proof differs from source member")
    if (
        body.tenant_id != expected_tenant_id
        or body.database_id != expected_database_id
        or body.principal_id != expected_principal_id
        or body.contour_head != expected_contour_head
    ):
        raise RecoverySourceIntegrityError("read-only source differs from selected context")
    if (
        body.registry != enclosing.registry
        or body.tool_schema != enclosing.tool_schema
        or body.tool_policy != enclosing.tool_policy
        or body.implementation != enclosing.implementation
        or body.query_identity != enclosing.query_identity
        or body.canonical_query_bytes != enclosing.canonical_query_bytes
        or body.snapshot != enclosing.snapshot
        or body.snapshot_frontier != enclosing.snapshot_frontier
        or body.snapshot_contract != enclosing.snapshot_contract
    ):
        raise RecoverySourceIntegrityError("read-only source differs from enclosing fields")
    if body.immutable_snapshot_selection == enclosing.no_mutation_proof:
        raise RecoverySourceIntegrityError("snapshot selection must not be the proof head")
    try:
        query = ConversationHistoryQuery.model_validate_json(body.canonical_query_bytes)
    except (TypeError, UnicodeDecodeError, ValidationError) as error:
        raise RecoverySourceIntegrityError("malformed conversation-history query") from error
    if (
        query.canonical_bytes() != body.canonical_query_bytes
        or hashlib.sha256(body.canonical_query_bytes).hexdigest()
        != body.query_identity.revision.fingerprint
    ):
        raise RecoverySourceIntegrityError("query bytes differ from query identity")
    return body


def decode_model_pre_emission_proof(
    member: RecoveryProofSourceMember,
    enclosing: RegisteredModelNoExposure,
    *,
    expected_tenant_id: Identity,
    expected_database_id: Identity,
) -> ModelPreEmissionCASSource:
    """Decode one exact pre-emission CAS proposal against an external selected cut."""
    body = _decode(member, ModelPreEmissionCASSource)
    assert isinstance(body, ModelPreEmissionCASSource)
    if (
        member.logical_subject_id != enclosing.original_attempt.subject_id
        or not _proof_head_matches(member, enclosing.proof)
        or enclosing.source_schema != member.schema_id
        or enclosing.canonical_source_bytes != member.canonical_source_bytes
    ):
        raise RecoverySourceIntegrityError("model enclosing proof differs from source member")
    if body.tenant_id != expected_tenant_id or body.database_id != expected_database_id:
        raise RecoverySourceIntegrityError("model source differs from selected context")
    if (
        body.registry != enclosing.registry
        or body.original_run != enclosing.original_run
        or body.original_attempt != enclosing.original_attempt
        or body.immutable_request != enclosing.immutable_request
        or body.visibility_manifest != enclosing.visibility_manifest
        or body.lineage_id != enclosing.lineage_id
        or body.selector_generation != enclosing.selector_generation
        or body.provider_contract != enclosing.provider_contract
        or body.recipient != enclosing.recipient
        or body.observed_emission_head != enclosing.observed_emission_head
    ):
        raise RecoverySourceIntegrityError("model source differs from enclosing fields")
    if body.permanently_fenced_generation != body.selector_generation:
        raise RecoverySourceIntegrityError("permanent generation fence differs from selector")
    if body.selected_pre_emission_cas_decision == enclosing.proof:
        raise RecoverySourceIntegrityError("selected CAS decision must not be the proof head")
    return body
