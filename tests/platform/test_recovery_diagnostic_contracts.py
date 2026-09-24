"""Pure contracts for broker-retained recovery diagnostic sources."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from importlib import import_module

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_fault_rule_contracts import (
    FaultRuleObservationRoleV1,
    FaultRuleRegistryV1,
    FaultRuleSourceKindV1,
    FaultRuleV1,
    SelectedFaultRuleRegistryV1,
)
from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    DirectDiagnosticProvenanceV1,
    FaultBrokerSourceRefV1,
    PreparedTerminalRecoveryFaultV1,
    PrepareTerminalRecoveryFaultV1,
    RecoveryFaultDiagnosticCutV1,
    RecoveryFaultInventoryV1,
    RecoveryFaultObservedMemberV1,
    RegisteredRecoveryFaultObserverAuthorityV1,
    TerminalRecoveryFaultCanonicalMemberV1,
    TerminalRecoveryFaultRecordV1,
    UndecodableRecoveryFaultFrontierV1,
    diagnostic_inventory_fingerprint,
)
from chiplog.platform._ingress_contracts import Head
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerRecoveryDiagnosticAuthenticationV1,
    ExactRecordHead,
    InvocationProofRef,
    JournalSelectedPublication,
    ObservedAbsence,
    OwnerCommandBytes,
    OwnerRecordBytes,
)
from chiplog.platform.recovery_diagnostic_contracts import (
    BrokerSelectedSourceDecisionV1,
    CaptureFaultSourceRequestV1,
    FaultCaptureRecordV1,
    FaultCaptureSubjectV1,
    FaultDiagnosticIssuanceRecordV1,
    FaultDiagnosticSubjectV1,
    FaultObservationRecordV1,
    FaultObserverReaderV1,
    FaultObserverRegistryV1,
    FreshFaultVerificationV1,
    HistoricalFaultVerificationV1,
    RetainedBrokerDiagnosticSourceV1,
    SelectedFaultDiagnosticIssuanceV1,
    SelectedFaultObservationV1,
    diagnostic_source_ref,
    fault_capture_inventory_fingerprint,
    fault_capture_inventory_preimage,
)
from chiplog.platform.runtime_surface_contracts import RuntimeSurfaceCut


def _digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _head(subject: str) -> CallSubjectHead:
    digest = _digest(subject)
    return CallSubjectHead(
        subject_id=subject, revision=Present(head="head:" + digest, fingerprint=digest)
    )


def _rules() -> SelectedFaultRuleRegistryV1:
    rule = FaultRuleV1(
        rule_id="rule",
        version="v1",
        classifier_id="classifier",
        classifier_version="v1",
        finding_code="CORRUPT_CANONICAL_BYTES",
        expected_relation="canonical-body",
        predicate_kind="CANONICAL_BODY",
        verifier_id="verifier",
        verifier_version="v1",
        ordered_observation_roles=(
            FaultRuleObservationRoleV1(
                role="BODY",
                min_count=1,
                max_count=1,
                allowed_owner_kinds=(
                    FaultRuleSourceKindV1(
                        owner="agent_loop", record_kind="Run", schema_id="run.v1"
                    ),
                ),
            ),
        ),
        completeness="POSITIVE_WITNESS_SUFFICIENT",
        witness_contract_schema="witness.v1",
        witness_contract_fingerprint="a" * 64,
    )
    registry = FaultRuleRegistryV1(
        registry_id="rules",
        version="v1",
        classifier_id="classifier",
        classifier_version="v1",
        disposition_version="v1",
        ordered_rules=(rule,),
    )
    digest = _digest(registry.canonical_bytes())
    reference = CallSubjectHead(
        subject_id="rules",
        revision=Present(head=registry.schema_id + ":" + digest, fingerprint=digest),
    )
    return SelectedFaultRuleRegistryV1(
        registry=registry,
        registry_reference=reference,
        canonical_registry_bytes=registry.canonical_bytes(),
        selected_decision=_head("rule-selection"),
    )


def _capture(
    *,
    ordinal: int = 0,
    observed: bytes = b"bad json",
    claimed: str = "b" * 64,
    reader_contract: str = "reader",
    reader_version: str = "v1",
) -> FaultCaptureRecordV1:
    return FaultCaptureRecordV1(
        subject=FaultCaptureSubjectV1(
            tenant_id="tenant",
            database_id="db",
            role="BODY",
            ordinal=ordinal,
            observed_owner="agent_loop",
            observed_record_kind="Run",
            declared_schema_id="run.v1",
            logical_subject_id="run",
            physical_record_id="physical",
            selected_source_decision=ExactRecordHead(
                owner="agent_loop",
                record_kind="Run",
                subject_id="run",
                record_id="physical",
                fingerprint="c" * 64,
            ),
            claimed_fingerprint=claimed,
            observed_bytes=observed,
            observed_bytes_fingerprint=_digest(observed),
            reader_contract=reader_contract,
            reader_version=reader_version,
            authority_snapshot_id="snapshot",
            authority_snapshot_fingerprint="d" * 64,
            observed_tenant_frontier=4,
            observed_materialization_commitment="e" * 64,
        ),
        capture_command_id="capture-command",
        originating_broker_epoch="epoch",
        originating_broker_session="session",
        runtime_generation="generation",
    )


def _retained(
    body: FaultCaptureRecordV1
    | FaultObserverRegistryV1
    | FaultObservationRecordV1
    | FaultDiagnosticIssuanceRecordV1,
) -> RetainedBrokerDiagnosticSourceV1:
    ref = diagnostic_source_ref(body)
    return RetainedBrokerDiagnosticSourceV1(
        reference=ref, canonical_source_bytes=body.canonical_bytes()
    )


def _registry() -> FaultObserverRegistryV1:
    return FaultObserverRegistryV1(
        registry_id="observers",
        version="v1",
        observer_id="observer",
        observer_contract_version="v1",
        allowed_reader_contracts=(FaultObserverReaderV1(reader_id="reader", version="v1"),),
        fault_rule_registry_reference=_rules().registry_reference,
        fault_rule_registry_selection=_rules().selected_decision,
        issuer_authority="issuer",
    )


def _observation() -> tuple[
    FaultObservationRecordV1, RetainedBrokerDiagnosticSourceV1, RetainedBrokerDiagnosticSourceV1
]:
    capture = _retained(_capture())
    registry = _retained(_registry())
    body = FaultObservationRecordV1(
        tenant_id="tenant",
        database_id="db",
        original_run=ExactRecordHead(
            owner="agent_loop",
            record_kind="Run",
            subject_id="run",
            record_id="run-head",
            fingerprint="f" * 64,
        ),
        ordered_capture_refs=(capture.reference,),
        ordered_capture_inventory_fingerprint=fault_capture_inventory_fingerprint((capture,)),
        observer_registry=registry.reference,
        fault_rule_registry_reference=_rules().registry_reference,
        fault_rule_registry_selection=_rules().selected_decision,
        authority_snapshot_id="snapshot",
        authority_snapshot_fingerprint="d" * 64,
        tenant_frontier=4,
        materialization_commitment="e" * 64,
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
    )
    return body, capture, registry


def _cut_and_provenance() -> tuple[bytes, bytes]:
    capture = FaultBrokerSourceRefV1(
        source_kind="FAULT_CAPTURE",
        schema_id="chiplog.broker.fault-capture.v1",
        source_id="capture",
        fingerprint="f" * 64,
    )
    observed = RecoveryFaultObservedMemberV1(
        role="BODY",
        ordinal=0,
        owner="agent_loop",
        record_kind="Run",
        declared_schema_id="run.v1",
        logical_subject_id="run",
        physical_record_id="physical",
        selected_decision=_head("selected"),
        claimed_fingerprint="a" * 64,
        observed_bytes=b"bad",
        observed_bytes_fingerprint=_digest(b"bad"),
        capture=capture,
    )
    inventory = RecoveryFaultInventoryV1(
        tenant_id="tenant",
        database_id="db",
        tenant_commit_sequence=4,
        materialization_commitment="b" * 64,
        current_run=_head("run"),
        suspension_baseline=_head("baseline"),
        suspension_pair=_head("pair"),
        frontier_registry=_head("frontier"),
        canonical_registry_bytes=b"registry",
        classifier=_head("classifier"),
        disposition_version="v1",
        fault_rule_registry=_head("rules"),
        fault_rule_registry_selection=_head("rule-selection"),
        ordered_observations=(observed,),
    )
    cut = RecoveryFaultDiagnosticCutV1(
        **inventory.model_dump(exclude={"schema_id"}),
        complete_inventory_fingerprint=diagnostic_inventory_fingerprint(inventory),
        frontier_observation=UndecodableRecoveryFaultFrontierV1(
            expected_schema_id="frontier.v1", raw_bytes=b"bad", capture=capture
        ),
        expected_fault=Absent(),
    )
    provenance = DirectDiagnosticProvenanceV1(
        trigger_capture=capture, reason="RECOVERY_INPUT_UNDECODABLE"
    )
    return cut.canonical_bytes(), provenance.canonical_bytes()


def test_capture_retains_empty_or_malformed_observed_bytes_and_not_claimed_hash() -> None:
    record = _capture(observed=b"", claimed="b" * 64)
    retained = _retained(record)
    assert record.subject.claimed_fingerprint != record.subject.observed_bytes_fingerprint
    assert retained.reference.source_kind == "FAULT_CAPTURE"
    assert b"source_id" not in retained.canonical_source_bytes


def test_capture_subject_rejects_invalid_observed_hash_and_request_has_no_payload_slot() -> None:
    subject = _capture().subject
    with pytest.raises(ValidationError, match="observed bytes fingerprint"):
        FaultCaptureSubjectV1.model_validate(
            {**subject.model_dump(), "observed_bytes_fingerprint": "0" * 64}
        )
    fields = CaptureFaultSourceRequestV1.model_fields
    assert "payload" not in fields and "observed_bytes" not in fields


def test_retained_source_rejects_wrong_kind_schema_hash_id_or_noncanonical_bytes() -> None:
    retained = _retained(_capture())
    for reference in (
        retained.reference.model_copy(update={"source_kind": "FAULT_ISSUANCE"}),
        retained.reference.model_copy(update={"schema_id": "wrong.v1"}),
        retained.reference.model_copy(update={"source_id": "wrong"}),
        retained.reference.model_copy(update={"fingerprint": "0" * 64}),
    ):
        with pytest.raises(ValidationError):
            RetainedBrokerDiagnosticSourceV1(
                reference=reference, canonical_source_bytes=retained.canonical_source_bytes
            )
    with pytest.raises(ValidationError):
        RetainedBrokerDiagnosticSourceV1(
            reference=retained.reference,
            canonical_source_bytes=retained.canonical_source_bytes + b" ",
        )


def test_selected_observation_requires_complete_exact_ordered_inventory() -> None:
    body, capture, registry = _observation()
    selected = SelectedFaultObservationV1(
        retained_observation=_retained(body),
        ordered_retained_captures=(capture,),
        selected_rules=_rules(),
        retained_observer_registry=registry,
    )
    assert selected.retained_observation.reference.source_kind == "FAULT_OBSERVATION"
    with pytest.raises(ValidationError):
        SelectedFaultObservationV1(
            retained_observation=selected.retained_observation,
            ordered_retained_captures=(),
            selected_rules=_rules(),
            retained_observer_registry=registry,
        )
    extra = _retained(_capture(ordinal=1))
    with pytest.raises(ValidationError, match="capture"):
        SelectedFaultObservationV1(
            retained_observation=selected.retained_observation,
            ordered_retained_captures=(capture, extra),
            selected_rules=_rules(),
            retained_observer_registry=registry,
        )


def test_capture_inventory_uses_versioned_full_descriptors_and_validates_each_capture() -> None:
    first = _retained(_capture(observed=b"left"))
    second = _retained(_capture(ordinal=1, observed=b"right"))
    preimage = fault_capture_inventory_preimage((first, second))
    decoded = json.loads(preimage)
    assert decoded == {
        "schema_id": "chiplog.broker.fault-capture-inventory.v1",
        "ordered_captures": [
            {
                "reference": first.reference.model_dump(mode="json"),
                "canonical_source_bytes": base64.b64encode(first.canonical_source_bytes).decode(),
            },
            {
                "reference": second.reference.model_dump(mode="json"),
                "canonical_source_bytes": base64.b64encode(second.canonical_source_bytes).decode(),
            },
        ],
    }
    assert fault_capture_inventory_fingerprint((first, second)) == _digest(preimage)
    assert preimage != fault_capture_inventory_preimage((second, first))
    with pytest.raises(ValueError, match="capture"):
        fault_capture_inventory_preimage((_retained(_registry()),))


def test_legacy_raw_concat_collision_is_not_the_descriptor_inventory_recipe() -> None:
    assert b"ab" + b"c" == b"a" + b"bc"
    first = _retained(_capture(observed=b"ab"))
    second = _retained(_capture(ordinal=1, observed=b"c"))
    third = _retained(_capture(observed=b"a"))
    fourth = _retained(_capture(ordinal=1, observed=b"bc"))
    assert fault_capture_inventory_preimage((first, second)) != fault_capture_inventory_preimage(
        (third, fourth)
    )


def test_selected_observation_rejects_unknown_capture_reader() -> None:
    original, _old_capture, registry = _observation()
    unknown_capture = _retained(_capture(reader_contract="unknown"))
    observation = FaultObservationRecordV1.model_validate(
        original.model_dump()
        | {
            "ordered_capture_refs": (unknown_capture.reference,),
            "ordered_capture_inventory_fingerprint": fault_capture_inventory_fingerprint(
                (unknown_capture,)
            ),
        }
    )
    with pytest.raises(ValidationError, match="unregistered capture reader"):
        SelectedFaultObservationV1(
            retained_observation=_retained(observation),
            ordered_retained_captures=(unknown_capture,),
            selected_rules=_rules(),
            retained_observer_registry=registry,
        )


def _selected_for_capture(capture_body: FaultCaptureRecordV1) -> SelectedFaultObservationV1:
    original, _old_capture, registry = _observation()
    capture = _retained(capture_body)
    observation = FaultObservationRecordV1.model_validate(
        original.model_dump()
        | {
            "ordered_capture_refs": (capture.reference,),
            "ordered_capture_inventory_fingerprint": fault_capture_inventory_fingerprint(
                (capture,)
            ),
        }
    )
    return SelectedFaultObservationV1(
        retained_observation=_retained(observation),
        ordered_retained_captures=(capture,),
        selected_rules=_rules(),
        retained_observer_registry=registry,
    )


@pytest.mark.parametrize(
    ("subject_updates", "record_updates", "message"),
    (
        ({"tenant_id": "other"}, {}, "tenant or database"),
        ({"database_id": "other"}, {}, "tenant or database"),
        ({"authority_snapshot_id": "other"}, {}, "authority snapshot"),
        ({"authority_snapshot_fingerprint": "a" * 64}, {}, "authority snapshot"),
        ({"observed_tenant_frontier": 5}, {}, "materialization cut"),
        ({"observed_materialization_commitment": "a" * 64}, {}, "materialization cut"),
        ({}, {"originating_broker_epoch": "other"}, "broker runtime"),
        ({}, {"originating_broker_session": "other"}, "broker runtime"),
        ({}, {"runtime_generation": "other"}, "broker runtime"),
    ),
)
def test_selected_observation_rejects_coherently_rehashed_capture_parent_drift(
    subject_updates: dict[str, object], record_updates: dict[str, object], message: str
) -> None:
    capture = _capture()
    rebuilt = FaultCaptureRecordV1.model_validate(
        capture.model_dump()
        | {
            "subject": capture.subject.model_dump() | subject_updates,
            **record_updates,
        }
    )
    with pytest.raises(ValidationError, match=message):
        _selected_for_capture(rebuilt)


def test_preissuance_proof_dag_excludes_future_cut_and_final_request_hash() -> None:
    body, _capture, registry = _observation()
    observation = _retained(body)
    cut, provenance = _cut_and_provenance()
    subject = FaultDiagnosticSubjectV1(
        operation="recovery.publish_terminal_fault",
        request_schema="chiplog.execution.prepare-terminal-recovery-fault.v1",
        tenant_id="tenant",
        database_id="db",
        command_id="command",
        original_run=body.original_run,
        diagnostic_cut_schema="chiplog.execution.recovery-fault-diagnostic-cut.v1",
        canonical_diagnostic_cut_bytes=cut,
        provenance_schema="chiplog.execution.fault-classification-provenance.v1",
        canonical_provenance_bytes=provenance,
        observer_registry=registry.reference,
        diagnostic_observation=observation.reference,
        source_capture_inventory_fingerprint=body.ordered_capture_inventory_fingerprint,
        fault_rule_registry_reference=_rules().registry_reference,
        fault_rule_registry_selection=_rules().selected_decision,
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
    )
    assert "final_request" not in FaultDiagnosticSubjectV1.model_fields
    issuance = FaultDiagnosticIssuanceRecordV1(
        subject=subject,
        observation_subject_fingerprint=_digest(subject.canonical_bytes()),
        issuance_id="issuance",
        issuer_authority="issuer",
        observer_contract_version="v1",
    )
    retained = _retained(issuance)
    invocation = InvocationProofRef(
        issuance_id="issuance",
        issuance_fingerprint=_digest(retained.canonical_source_bytes),
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
        operation_subject=_digest(subject.canonical_bytes()),
    )
    selected = SelectedFaultDiagnosticIssuanceV1(retained_issuance=retained, invocation=invocation)
    assert selected.invocation.operation_subject != "a" * 64
    assert selected.invocation.issuance_fingerprint != selected.invocation.operation_subject


def test_broker_ref_is_not_an_owner_record_head_and_broker_selection_is_explicit() -> None:
    ref = diagnostic_source_ref(_capture())
    with pytest.raises(ValidationError):
        ExactRecordHead.model_validate(ref.model_dump())
    decision = BrokerSelectedSourceDecisionV1(
        source=ref, selection_id="selection", selection_fingerprint="a" * 64
    )
    assert decision.source.source_owner == "broker"


def test_fresh_and_historical_input_shapes_are_deliberately_disjoint() -> None:
    from chiplog.platform.recovery_diagnostic_contracts import (
        FreshFaultVerificationV1,
        HistoricalFaultVerificationV1,
    )

    assert "current_fault" in FreshFaultVerificationV1.model_fields
    assert "current_fault" not in HistoricalFaultVerificationV1.model_fields
    assert "current_lookup_invocation" in HistoricalFaultVerificationV1.model_fields
    assert "current_lookup_invocation" not in FreshFaultVerificationV1.model_fields


def _owner_ref(reference: RetainedBrokerDiagnosticSourceV1) -> FaultBrokerSourceRefV1:
    return FaultBrokerSourceRefV1.model_validate(reference.reference.model_dump())


def _fresh(version: str = "v2") -> FreshFaultVerificationV1:
    terminal_test = import_module(
        "tests.capabilities.agent_loop.test_terminal_recovery_fault_contracts"
    )
    run = asyncio.run(terminal_test._suspended_run(version))
    run_head = ExactRecordHead(
        owner="agent_loop",
        record_kind="Run",
        subject_id=run.run_id,
        record_id=run.head,
        fingerprint=run.digest(),
    )
    run_ref = CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    base = terminal_test._record()
    run = run.model_copy(
        update={"suspension_baseline": base.diagnostic_cut.suspension_baseline, "head": "pending"}
    )
    run = run.model_copy(update={"head": "loop:" + run.digest()})
    run_head = ExactRecordHead(
        owner="agent_loop",
        record_kind="Run",
        subject_id=run.run_id,
        record_id=run.head,
        fingerprint=run.digest(),
    )
    run_ref = CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    inventory = RecoveryFaultInventoryV1(
        **(
            base.diagnostic_cut.model_dump(
                exclude={
                    "schema_id",
                    "complete_inventory_fingerprint",
                    "frontier_observation",
                    "expected_fault",
                }
            )
            | {"current_run": run_ref.model_dump()}
        )
    )
    cut = RecoveryFaultDiagnosticCutV1(
        **inventory.model_dump(exclude={"schema_id"}),
        complete_inventory_fingerprint=diagnostic_inventory_fingerprint(inventory),
        frontier_observation=base.diagnostic_cut.frontier_observation,
        expected_fault=base.diagnostic_cut.expected_fault,
    )
    observation_body, capture, registry = _observation()
    observation_body = FaultObservationRecordV1.model_validate(
        observation_body.model_dump() | {"original_run": run_head.model_dump()}
    )
    selected_observation = SelectedFaultObservationV1(
        retained_observation=_retained(observation_body),
        ordered_retained_captures=(capture,),
        selected_rules=_rules(),
        retained_observer_registry=registry,
    )
    authority = RegisteredRecoveryFaultObserverAuthorityV1(
        observer_registry=_owner_ref(registry),
        diagnostic_observation=_owner_ref(selected_observation.retained_observation),
        issuance=FaultBrokerSourceRefV1(
            source_kind="FAULT_ISSUANCE",
            schema_id="chiplog.broker.fault-diagnostic-issuance.v1",
            source_id="pending",
            fingerprint="a" * 64,
        ),
        observation_subject_fingerprint="a" * 64,
    )
    request = PrepareTerminalRecoveryFaultV1(
        command_id="command", run=run, cut=cut, provenance=base.provenance, authority=authority
    )
    subject = FaultDiagnosticSubjectV1(
        tenant_id=cut.tenant_id,
        database_id=cut.database_id,
        command_id=request.command_id,
        original_run=run_head,
        canonical_diagnostic_cut_bytes=cut.canonical_bytes(),
        canonical_provenance_bytes=request.provenance.canonical_bytes(),
        observer_registry=registry.reference,
        diagnostic_observation=selected_observation.retained_observation.reference,
        source_capture_inventory_fingerprint=observation_body.ordered_capture_inventory_fingerprint,
        fault_rule_registry_reference=_rules().registry_reference,
        fault_rule_registry_selection=_rules().selected_decision,
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
    )
    issuance = FaultDiagnosticIssuanceRecordV1(
        subject=subject,
        observation_subject_fingerprint=_digest(subject.canonical_bytes()),
        issuance_id="issuance",
        issuer_authority="issuer",
        observer_contract_version="v1",
    )
    retained_issuance = _retained(issuance)
    invocation = InvocationProofRef(
        issuance_id="issuance",
        issuance_fingerprint=_digest(retained_issuance.canonical_source_bytes),
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
        operation_subject=issuance.observation_subject_fingerprint,
    )
    selected_issuance = SelectedFaultDiagnosticIssuanceV1(
        retained_issuance=retained_issuance, invocation=invocation
    )
    authority = authority.model_copy(
        update={
            "issuance": _owner_ref(retained_issuance),
            "observation_subject_fingerprint": issuance.observation_subject_fingerprint,
        }
    )
    request = request.model_copy(update={"authority": authority})
    command_bytes = request.canonical_bytes()
    command = OwnerCommandBytes(
        owner="agent_loop",
        schema_id=request.schema_id,
        canonical_bytes=command_bytes,
        fingerprint=_digest(command_bytes),
    )
    fault = TerminalRecoveryFaultRecordV1(
        tenant_id=cut.tenant_id,
        database_id=cut.database_id,
        command_id=request.command_id,
        original_run=run_ref,
        suspension_baseline=cut.suspension_baseline,
        suspension_pair=cut.suspension_pair,
        source_request_fingerprint=command.fingerprint,
        diagnostic_cut=cut,
        provenance=request.provenance,
        authority=authority,
        disposition="TERMINAL_RECOVERY_FAULT",
        findings=base.findings,
        continuation="FORBIDDEN",
        predecessor_fault=Absent(),
    )
    raw = fault.canonical_bytes()
    fingerprint = _digest(raw)
    member = TerminalRecoveryFaultCanonicalMemberV1(
        record_id="chiplog.execution.terminal-recovery-fault.v1:" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )
    result = PreparedTerminalRecoveryFaultV1(
        source_request_fingerprint=command.fingerprint,
        member=member,
        complete_commitment=_digest(
            b"chiplog.execution.terminal-recovery-fault-result.v1\0"
            + command.fingerprint.encode()
            + b"\0"
            + member.canonical_bytes()
        ),
    )
    record = OwnerRecordBytes(
        owner="agent_loop",
        record_kind="TERMINAL_RECOVERY_FAULT",
        record_id=member.record_id,
        schema_id=member.schema_id,
        canonical_bytes=member.canonical_record_bytes,
        fingerprint=member.fingerprint,
    )
    authentication = BrokerRecoveryDiagnosticAuthenticationV1(
        invocation=invocation,
        observer_registry=registry.reference,
        diagnostic_observation=selected_observation.retained_observation.reference,
        issuance=retained_issuance.reference,
        original_run=run_head,
        diagnostic_cut_fingerprint=_digest(cut.canonical_bytes()),
        source_capture_inventory_fingerprint=observation_body.ordered_capture_inventory_fingerprint,
        observation_subject_fingerprint=issuance.observation_subject_fingerprint,
        final_request_fingerprint=command.fingerprint,
    )
    head = Head(identity="head", head="head", fingerprint="a" * 64)
    return FreshFaultVerificationV1(
        command=command,
        record=record,
        authentication=authentication,
        retained_owner_result_bytes=result.canonical_bytes(),
        retained_issuance=selected_issuance,
        retained_observation=selected_observation,
        current_authoritative_reads=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=4,
            expected_materialization_commitment="a" * 64,
            registry_head="registry",
            registry_fingerprint="a" * 64,
            ordered_heads=(),
            complete_manifest_fingerprint="a" * 64,
        ),
        current_runtime=RuntimeSurfaceCut(
            runtime_profile=head,
            broker_generation="generation",
            runtime_generation="generation",
            source_inventory_fingerprint="a" * 64,
            worker_registry=head,
            ingress_manifest=head,
        ),
        current_run=run_head,
        current_run_canonical_bytes=run.canonical_bytes(),
        current_fault=ObservedAbsence(
            owner="agent_loop", record_kind="TERMINAL_RECOVERY_FAULT", subject_id=run.run_id
        ),
        writer_transaction_id="tx",
    )


@pytest.mark.parametrize("version", ("v2", "v3"))
def test_fresh_fault_verification_binds_whole_native_owner_and_retained_graph(version: str) -> None:
    assert _fresh(version).current_run.subject_id


@pytest.mark.parametrize("field,value", (("record_id", "wrong"), ("fingerprint", "b" * 64)))
def test_fresh_fault_verification_rejects_coherent_current_run_mutants(
    field: str, value: str
) -> None:
    fresh = _fresh()
    with pytest.raises(ValidationError):
        FreshFaultVerificationV1.model_validate(
            fresh.model_dump() | {"current_run": fresh.current_run.model_dump() | {field: value}}
        )


def test_fresh_fault_verification_rejects_coherently_reheaded_non_suspended_run() -> None:
    fresh = _fresh()
    terminal_test = import_module(
        "tests.capabilities.agent_loop.test_terminal_recovery_fault_contracts"
    )
    active = asyncio.run(terminal_test._suspended_run("v2"))
    active = active.model_copy(update={"state": "ACTIVE", "head": "pending"})
    active = active.model_copy(update={"head": "loop:" + active.digest()})
    current = fresh.current_run.model_copy(
        update={"record_id": active.head, "fingerprint": active.digest()}
    )
    with pytest.raises(ValidationError, match="SUSPENDED"):
        FreshFaultVerificationV1.model_validate(
            fresh.model_dump()
            | {
                "current_run": current.model_dump(),
                "current_run_canonical_bytes": active.canonical_bytes(),
            }
        )


def _historical() -> HistoricalFaultVerificationV1:
    fresh = _fresh()
    selected = JournalSelectedPublication(
        kind="COMMITTED",
        tenant_id="tenant",
        command_id="command",
        decision_id="decision",
        decision_head="decision-head",
        decision_fingerprint="a" * 64,
        predecessor_commitment="a" * 64,
        resulting_commitment="b" * 64,
        tenant_commit_sequence=5,
        complete_records=(fresh.record,),
    )
    return HistoricalFaultVerificationV1(
        current_lookup_invocation=fresh.authentication.invocation,
        original_command=fresh.command,
        original_authentication=fresh.authentication,
        retained_issuance=fresh.retained_issuance,
        retained_observation=fresh.retained_observation,
        selected=selected,
        current_authoritative_reads=fresh.current_authoritative_reads,
    )


def test_historical_fault_verification_binds_original_request_auth_and_selected_record() -> None:
    assert _historical().selected.command_id == "command"


@pytest.mark.parametrize(
    ("path", "value"),
    (("selected.command_id", "other"), ("original_command.owner", "effects")),
)
def test_historical_fault_verification_rejects_wrong_command_or_owner(
    path: str, value: str
) -> None:
    historical = _historical()
    root, field = path.split(".")
    target = getattr(historical, root)
    changed = target.model_copy(update={field: value})
    with pytest.raises(ValidationError):
        HistoricalFaultVerificationV1.model_validate(
            historical.model_dump() | {root: changed.model_dump()}
        )
