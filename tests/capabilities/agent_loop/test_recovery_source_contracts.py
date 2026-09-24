"""Consumer contracts for exact retained recovery-proof source observations."""

import base64
import hashlib
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.model_attempt_recovery_contracts import (
    RegisteredModelNoExposure,
)
from chiplog.capabilities.agent_loop.readonly_execution_contracts import RegisteredReadOnlyProof
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import ConversationHistoryQuery
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_source_contracts import (
    BROKER_SOURCE_OWNER,
    MODEL_PRE_EMISSION_CAS_READER,
    MODEL_PRE_EMISSION_CAS_SCHEMA,
    READONLY_HISTORY_PROOF_READER,
    READONLY_HISTORY_PROOF_SCHEMA,
    RECOVERY_PROOF_SOURCE_ROWS,
    ModelPreEmissionCASSource,
    ReadOnlyHistoryProofSource,
    RecoveryProofSourceMember,
    RecoverySourceIntegrityError,
    decode_model_pre_emission_proof,
    decode_readonly_history_proof,
)
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes


def head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name,
        revision=Present(
            head="physical:" + name, fingerprint=hashlib.sha256(name.encode()).hexdigest()
        ),
    )


def member(
    *, reader_id: str, source_kind: str, schema_id: str, subject: str, raw: bytes
) -> RecoveryProofSourceMember:
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryProofSourceMember(
        source_owner=BROKER_SOURCE_OWNER,
        reader_id=reader_id,
        source_kind=source_kind,
        schema_id=schema_id,
        logical_subject_id=subject,
        source_record_id=schema_id + ":" + fingerprint,
        canonical_source_bytes=raw,
        fingerprint=fingerprint,
    )


def readonly_fixture() -> tuple[RecoveryProofSourceMember, RegisteredReadOnlyProof]:
    query = ConversationHistoryQuery(limit=3, after_cursor="cursor")
    query_bytes = query.canonical_bytes()
    query_identity = CallSubjectHead(
        subject_id="query",
        revision=Present(
            head="registered-query-head",
            fingerprint=hashlib.sha256(query_bytes).hexdigest(),
        ),
    )
    body = ReadOnlyHistoryProofSource(
        registry=head("registry"),
        tool_schema=head("tool-schema"),
        tool_policy=head("tool-policy"),
        implementation=head("implementation"),
        query_identity=query_identity,
        canonical_query_bytes=query_bytes,
        snapshot=head("snapshot"),
        snapshot_frontier=9,
        snapshot_contract=head("snapshot-contract"),
        tenant_id="tenant",
        database_id="database",
        principal_id="principal",
        contour_head="contour",
        registered_read_operation=head("operation"),
        authority_read_manifest=head("read-manifest"),
        immutable_snapshot_selection=head("snapshot-selection"),
    )
    source = member(
        reader_id=READONLY_HISTORY_PROOF_READER,
        source_kind="READONLY_HISTORY_PROOF",
        schema_id=READONLY_HISTORY_PROOF_SCHEMA,
        subject=query_identity.subject_id,
        raw=body.canonical_bytes(),
    )
    enclosing = RegisteredReadOnlyProof(
        registry=body.registry,
        tool_schema=body.tool_schema,
        tool_policy=body.tool_policy,
        implementation=body.implementation,
        no_mutation_proof=CallSubjectHead(
            subject_id=source.logical_subject_id,
            revision=Present(head=source.source_record_id, fingerprint=source.fingerprint),
        ),
        proof_schema=source.schema_id,
        canonical_proof_bytes=source.canonical_source_bytes,
        query_identity=body.query_identity,
        canonical_query_bytes=body.canonical_query_bytes,
        snapshot=body.snapshot,
        snapshot_frontier=body.snapshot_frontier,
        snapshot_contract=body.snapshot_contract,
    )
    return source, enclosing


def model_fixture() -> tuple[RecoveryProofSourceMember, RegisteredModelNoExposure]:
    body = ModelPreEmissionCASSource(
        registry=head("registry"),
        original_run=head("run"),
        original_attempt=head("attempt"),
        immutable_request=head("request"),
        visibility_manifest=head("manifest"),
        lineage_id="lineage",
        selector_generation=4,
        provider_contract="provider-contract",
        recipient="recipient",
        observed_emission_head=head("emission-head"),
        tenant_id="tenant",
        database_id="database",
        authenticated_model_adapter=head("adapter"),
        model_worker_session=head("session"),
        permanently_fenced_generation=4,
        selected_pre_emission_cas_decision=head("selected-cas"),
    )
    source = member(
        reader_id=MODEL_PRE_EMISSION_CAS_READER,
        source_kind="MODEL_PRE_EMISSION_CAS",
        schema_id=MODEL_PRE_EMISSION_CAS_SCHEMA,
        subject=body.original_attempt.subject_id,
        raw=body.canonical_bytes(),
    )
    enclosing = RegisteredModelNoExposure(
        registry=body.registry,
        proof=CallSubjectHead(
            subject_id=source.logical_subject_id,
            revision=Present(head=source.source_record_id, fingerprint=source.fingerprint),
        ),
        original_run=body.original_run,
        original_attempt=body.original_attempt,
        lineage_id=body.lineage_id,
        selector_generation=body.selector_generation,
        immutable_request=body.immutable_request,
        visibility_manifest=body.visibility_manifest,
        provider_contract=body.provider_contract,
        recipient=body.recipient,
        observed_emission_head=body.observed_emission_head,
        source_schema=source.schema_id,
        canonical_source_bytes=source.canonical_source_bytes,
    )
    return source, enclosing


def changed_body(
    source: RecoveryProofSourceMember, field: str, value: object
) -> RecoveryProofSourceMember:
    wire = json.loads(source.canonical_source_bytes)
    wire[field] = value
    raw = json.dumps(wire, separators=(",", ":"), ensure_ascii=False).encode()
    return member(
        reader_id=source.reader_id,
        source_kind=source.source_kind,
        schema_id=source.schema_id,
        subject=source.logical_subject_id,
        raw=raw,
    )


def test_closed_rows_are_broker_source_envelopes_not_owner_records() -> None:
    assert {
        (row.reader_id, row.source_owner, row.source_kind, row.schema_id)
        for row in RECOVERY_PROOF_SOURCE_ROWS
    } == {
        (
            READONLY_HISTORY_PROOF_READER,
            "broker",
            "READONLY_HISTORY_PROOF",
            READONLY_HISTORY_PROOF_SCHEMA,
        ),
        (
            MODEL_PRE_EMISSION_CAS_READER,
            "broker",
            "MODEL_PRE_EMISSION_CAS",
            MODEL_PRE_EMISSION_CAS_SCHEMA,
        ),
    }
    source, _ = readonly_fixture()
    with pytest.raises(ValidationError):
        OwnerRecordBytes.model_validate(source.model_dump(mode="json"))


def test_readonly_decoder_binds_full_body_query_context_and_proof_head() -> None:
    source, enclosing = readonly_fixture()
    decoded = decode_readonly_history_proof(
        source,
        enclosing,
        expected_tenant_id="tenant",
        expected_database_id="database",
        expected_principal_id="principal",
        expected_contour_head="contour",
    )
    assert decoded.canonical_bytes() == source.canonical_source_bytes
    assert decoded.registered_read_operation == head("operation")
    assert "no_mutation_proof" not in json.loads(source.canonical_source_bytes)

    for field, value in (
        ("tenant_id", "other"),
        ("principal_id", "other"),
        ("snapshot_frontier", 10),
    ):
        with pytest.raises(RecoverySourceIntegrityError):
            decode_readonly_history_proof(
                changed_body(source, field, value),
                enclosing,
                expected_tenant_id="tenant",
                expected_database_id="database",
                expected_principal_id="principal",
                expected_contour_head="contour",
            )
    with pytest.raises(RecoverySourceIntegrityError):
        decode_readonly_history_proof(
            source,
            enclosing,
            expected_tenant_id="tenant",
            expected_database_id="database",
            expected_principal_id="principal",
            expected_contour_head="other",
        )


def test_readonly_decoder_rejects_noncanonical_query_and_envelope_mutants() -> None:
    source, enclosing = readonly_fixture()
    wire = json.loads(source.canonical_source_bytes)
    wire["canonical_query_bytes"] = base64.b64encode(
        b'{"after_cursor":"cursor", "limit":3}'
    ).decode()
    query_mutant = member(
        reader_id=source.reader_id,
        source_kind=source.source_kind,
        schema_id=source.schema_id,
        subject=source.logical_subject_id,
        raw=json.dumps(wire, separators=(",", ":"), ensure_ascii=False).encode(),
    )
    query_enclosing = enclosing.model_copy(
        update={
            "no_mutation_proof": CallSubjectHead(
                subject_id=query_mutant.logical_subject_id,
                revision=Present(
                    head=query_mutant.source_record_id,
                    fingerprint=query_mutant.fingerprint,
                ),
            ),
            "canonical_proof_bytes": query_mutant.canonical_source_bytes,
            "canonical_query_bytes": base64.b64decode(wire["canonical_query_bytes"]),
        }
    )
    with pytest.raises(RecoverySourceIntegrityError):
        decode_readonly_history_proof(
            query_mutant,
            query_enclosing,
            expected_tenant_id="tenant",
            expected_database_id="database",
            expected_principal_id="principal",
            expected_contour_head="contour",
        )
    for update in (
        {"reader_id": "unknown-v1"},
        {"source_owner": "agent_loop"},
        {"source_kind": "UNKNOWN"},
        {"schema_id": "chiplog.source.readonly-history-proof.v2"},
        {"source_record_id": "wrong"},
        {"fingerprint": "0" * 64},
        {"canonical_source_bytes": b'{"schema_id":"chiplog.source.readonly-history-proof.v1"}'},
    ):
        with pytest.raises(RecoverySourceIntegrityError):
            decode_readonly_history_proof(
                source.model_copy(update=update),
                enclosing,
                expected_tenant_id="tenant",
                expected_database_id="database",
                expected_principal_id="principal",
                expected_contour_head="contour",
            )
    payload = json.loads(source.canonical_source_bytes)
    noncanonical = json.dumps(payload, indent=1).encode()
    noncanonical_member = member(
        reader_id=source.reader_id,
        source_kind=source.source_kind,
        schema_id=source.schema_id,
        subject=source.logical_subject_id,
        raw=noncanonical,
    )
    with pytest.raises(RecoverySourceIntegrityError):
        decode_readonly_history_proof(
            noncanonical_member,
            enclosing,
            expected_tenant_id="tenant",
            expected_database_id="database",
            expected_principal_id="principal",
            expected_contour_head="contour",
        )


def test_model_decoder_binds_generation_selected_cas_and_independent_proof() -> None:
    source, enclosing = model_fixture()
    decoded = decode_model_pre_emission_proof(
        source, enclosing, expected_tenant_id="tenant", expected_database_id="database"
    )
    assert decoded.selected_pre_emission_cas_decision == head("selected-cas")
    assert decoded.selected_pre_emission_cas_decision != enclosing.proof
    assert "proof" not in json.loads(source.canonical_source_bytes)

    for field, value in (
        ("selector_generation", 5),
        ("permanently_fenced_generation", 3),
        ("visibility_manifest", head("other").model_dump(mode="json")),
        ("emission_state", "EMITTED"),
    ):
        with pytest.raises(RecoverySourceIntegrityError):
            decode_model_pre_emission_proof(
                changed_body(source, field, value),
                enclosing,
                expected_tenant_id="tenant",
                expected_database_id="database",
            )
    with pytest.raises(RecoverySourceIntegrityError):
        decode_model_pre_emission_proof(
            source, enclosing, expected_tenant_id="other", expected_database_id="database"
        )
