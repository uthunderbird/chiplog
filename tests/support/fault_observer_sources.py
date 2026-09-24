"""Coherent retained broker sources for terminal-fault observer tests."""

from __future__ import annotations

import hashlib
from typing import Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_fault_rule_contracts import (
    FaultRuleObservationRoleV1,
    FaultRuleRegistryV1,
    FaultRuleSourceKindV1,
    FaultRuleV1,
    SelectedFaultRuleRegistryV1,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    frontier_registry_content_fingerprint,
    frontier_registry_reference,
)
from chiplog.capabilities.agent_loop.terminal_fault_observer_sources import (
    FaultBrokerSourceRefV1 as OwnerRef,
)
from chiplog.capabilities.agent_loop.terminal_fault_observer_sources import (
    FaultObserverRetainedInputsV1,
    FaultRetainedBrokerSourceV1,
)
from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    DirectDiagnosticProvenanceV1,
    FaultBrokerSourceRefV1,
    PrepareTerminalRecoveryFaultV1,
    RecoveryFaultDiagnosticCutV1,
    RecoveryFaultInventoryV1,
    RecoveryFaultObservedMemberV1,
    RegisteredRecoveryFaultObserverAuthorityV1,
    UndecodableRecoveryFaultFrontierV1,
    diagnostic_inventory_fingerprint,
)
from chiplog.platform._owner_publication_contracts import ExactRecordHead
from chiplog.platform.recovery_diagnostic_contracts import (
    FaultCaptureRecordV1,
    FaultCaptureSubjectV1,
    FaultDiagnosticIssuanceRecordV1,
    FaultDiagnosticSubjectV1,
    FaultObservationRecordV1,
    FaultObserverReaderV1,
    FaultObserverRegistryV1,
    RetainedBrokerDiagnosticSourceV1,
    diagnostic_source_ref,
    fault_capture_inventory_fingerprint,
)
from chiplog.platform.recovery_diagnostic_refs import BrokerDiagnosticSourceRefV1
from tests.support.execution_fan_out import fixture as execution_fan_out_fixture
from tests.support.execution_versions import execution_run_v3


def _digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _head(subject: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=subject,
        revision=Present(head="head:" + subject, fingerprint=_digest(subject)),
    )


def _selected_rules() -> SelectedFaultRuleRegistryV1:
    rule = FaultRuleV1(
        rule_id="recovery-cut-corruption",
        version="v1",
        classifier_id="classifier",
        classifier_version="head:classifier",
        finding_code="CORRUPT_CANONICAL_BYTES",
        expected_relation="canonical-body",
        predicate_kind="CANONICAL_BODY",
        verifier_id="verifier",
        verifier_version="v1",
        ordered_observation_roles=(
            FaultRuleObservationRoleV1(
                role="RECOVERY_CUT",
                min_count=1,
                max_count=1,
                allowed_owner_kinds=(
                    FaultRuleSourceKindV1(
                        owner="agent_loop",
                        record_kind="RECOVERY_CUT",
                        schema_id="chiplog.execution.recovery-cut.v1",
                    ),
                ),
            ),
        ),
        completeness="POSITIVE_WITNESS_SUFFICIENT",
        witness_contract_schema="witness.v1",
        witness_contract_fingerprint="a" * 64,
    )
    registry = FaultRuleRegistryV1(
        registry_id="fault-rules",
        version="v1",
        classifier_id=rule.classifier_id,
        classifier_version=rule.classifier_version,
        disposition_version="v1",
        ordered_rules=(rule,),
    )
    raw = registry.canonical_bytes()
    digest = _digest(raw)
    return SelectedFaultRuleRegistryV1(
        registry=registry,
        registry_reference=CallSubjectHead(
            subject_id=registry.registry_id,
            revision=Present(head=registry.schema_id + ":" + digest, fingerprint=digest),
        ),
        canonical_registry_bytes=raw,
        selected_decision=_head("fault-rules-selection"),
    )


def _owner_ref(ref: BrokerDiagnosticSourceRefV1) -> OwnerRef:
    return OwnerRef.model_validate(ref.model_dump(mode="json"))


def _retained(
    body: FaultCaptureRecordV1
    | FaultObserverRegistryV1
    | FaultObservationRecordV1
    | FaultDiagnosticIssuanceRecordV1,
) -> RetainedBrokerDiagnosticSourceV1:
    reference = diagnostic_source_ref(body)
    return RetainedBrokerDiagnosticSourceV1(
        reference=reference, canonical_source_bytes=body.canonical_bytes()
    )


def _owner_retained(item: RetainedBrokerDiagnosticSourceV1) -> FaultRetainedBrokerSourceV1:
    return FaultRetainedBrokerSourceV1(
        reference=_owner_ref(item.reference), canonical_source_bytes=item.canonical_source_bytes
    )


async def _suspended_run(version: Literal["v2", "v3"]) -> ExecutionRun:
    original = (
        execution_run_v3() if version == "v3" else (await execution_fan_out_fixture()).captured_run
    )
    pending = original.model_copy(
        update={
            "state": "SUSPENDED",
            "head": "pending",
            "suspension_baseline": _head("suspension-baseline"),
        }
    )
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


async def full_fixture(
    version: Literal["v2", "v3"] = "v3",
) -> tuple[
    PrepareTerminalRecoveryFaultV1,
    FaultObserverRetainedInputsV1,
    SelectedFaultRuleRegistryV1,
]:
    """Return one complete observer request and its exact retained broker bodies."""
    run = await _suspended_run(version)
    assert run.suspension_baseline is not None
    run_head = CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )
    selected_rules = _selected_rules()
    rule_ref = selected_rules.registry_reference
    rule_selection = selected_rules.selected_decision
    registry_rows = (
        RecoveryRegistryRow(
            family="RUN",
            ordinal=0,
            subject_extractor_id="run-subject",
            cardinality_rule="one",
            terminal_conflict_rule="none",
            serialization_rule="recovery-v1",
            canonicalization_version="recovery-v1",
        ),
    )
    frontier_registry = RecoveryFrontierRegistry(
        registry_id="frontier-registry",
        version="v1",
        ordered_rows=registry_rows,
        fingerprint=frontier_registry_content_fingerprint("frontier-registry", "v1", registry_rows),
    )
    frontier_reference = frontier_registry_reference(frontier_registry)
    selected = ExactRecordHead(
        owner="agent_loop",
        record_kind="RECOVERY_CUT",
        subject_id="cut",
        record_id="cut-body",
        fingerprint="a" * 64,
    )
    capture = _retained(
        FaultCaptureRecordV1(
            subject=FaultCaptureSubjectV1(
                tenant_id=run.tenant,
                database_id="database",
                role="RECOVERY_CUT",
                ordinal=0,
                observed_owner="agent_loop",
                observed_record_kind="RECOVERY_CUT",
                declared_schema_id="chiplog.execution.recovery-cut.v1",
                logical_subject_id="cut",
                physical_record_id="cut-body",
                selected_source_decision=selected,
                claimed_fingerprint="b" * 64,
                observed_bytes=b"{not a recovery cut",
                observed_bytes_fingerprint=_digest(b"{not a recovery cut"),
                reader_contract="reader",
                reader_version="v1",
                authority_snapshot_id="snapshot",
                authority_snapshot_fingerprint="c" * 64,
                observed_tenant_frontier=4,
                observed_materialization_commitment="d" * 64,
            ),
            capture_command_id="capture-command",
            originating_broker_epoch="epoch",
            originating_broker_session="session",
            runtime_generation="generation",
        )
    )
    registry = _retained(
        FaultObserverRegistryV1(
            registry_id="observer-registry",
            version="v1",
            observer_id="observer",
            observer_contract_version="v1",
            allowed_reader_contracts=(FaultObserverReaderV1(reader_id="reader", version="v1"),),
            fault_rule_registry_reference=rule_ref,
            fault_rule_registry_selection=rule_selection,
            issuer_authority="issuer",
        )
    )
    observation = _retained(
        FaultObservationRecordV1(
            tenant_id=run.tenant,
            database_id="database",
            original_run=ExactRecordHead(
                owner="agent_loop",
                record_kind="Run",
                subject_id=run_head.subject_id,
                record_id=run_head.revision.head,
                fingerprint=run_head.revision.fingerprint,
            ),
            ordered_capture_refs=(capture.reference,),
            ordered_capture_inventory_fingerprint=fault_capture_inventory_fingerprint((capture,)),
            observer_registry=registry.reference,
            fault_rule_registry_reference=rule_ref,
            fault_rule_registry_selection=rule_selection,
            authority_snapshot_id="snapshot",
            authority_snapshot_fingerprint="c" * 64,
            tenant_frontier=4,
            materialization_commitment="d" * 64,
            broker_epoch="epoch",
            broker_session="session",
            runtime_generation="generation",
        )
    )
    capture_ref = FaultBrokerSourceRefV1.model_validate(capture.reference.model_dump())
    observed = RecoveryFaultObservedMemberV1(
        role="RECOVERY_CUT",
        ordinal=0,
        owner="agent_loop",
        record_kind="RECOVERY_CUT",
        declared_schema_id="chiplog.execution.recovery-cut.v1",
        logical_subject_id="cut",
        physical_record_id="cut-body",
        selected_decision=CallSubjectHead(
            subject_id="cut",
            revision=Present(head="cut-body", fingerprint="a" * 64),
        ),
        claimed_fingerprint="b" * 64,
        observed_bytes=b"{not a recovery cut",
        observed_bytes_fingerprint=_digest(b"{not a recovery cut"),
        capture=capture_ref,
    )
    inventory = RecoveryFaultInventoryV1(
        tenant_id=run.tenant,
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment="d" * 64,
        current_run=run_head,
        suspension_baseline=run.suspension_baseline,
        suspension_pair=_head("suspension-pair"),
        frontier_registry=frontier_reference,
        canonical_registry_bytes=frontier_registry.canonical_bytes(),
        classifier=_head("classifier"),
        disposition_version="v1",
        fault_rule_registry=rule_ref,
        fault_rule_registry_selection=rule_selection,
        ordered_observations=(observed,),
    )
    cut = RecoveryFaultDiagnosticCutV1(
        tenant_id=inventory.tenant_id,
        database_id=inventory.database_id,
        tenant_commit_sequence=inventory.tenant_commit_sequence,
        materialization_commitment=inventory.materialization_commitment,
        current_run=inventory.current_run,
        suspension_baseline=inventory.suspension_baseline,
        suspension_pair=inventory.suspension_pair,
        frontier_registry=inventory.frontier_registry,
        canonical_registry_bytes=inventory.canonical_registry_bytes,
        classifier=inventory.classifier,
        disposition_version=inventory.disposition_version,
        fault_rule_registry=inventory.fault_rule_registry,
        fault_rule_registry_selection=inventory.fault_rule_registry_selection,
        ordered_observations=inventory.ordered_observations,
        complete_inventory_fingerprint=diagnostic_inventory_fingerprint(inventory),
        frontier_observation=UndecodableRecoveryFaultFrontierV1(
            expected_schema_id="chiplog.execution.recovery-cut.v1",
            raw_bytes=observed.observed_bytes,
            capture=capture_ref,
        ),
        expected_fault=Absent(),
    )
    provenance = DirectDiagnosticProvenanceV1(
        trigger_capture=capture_ref, reason="RECOVERY_INPUT_UNDECODABLE"
    )
    issuance_subject = FaultDiagnosticSubjectV1(
        tenant_id=run.tenant,
        database_id="database",
        command_id="command",
        original_run=ExactRecordHead(
            owner="agent_loop",
            record_kind="Run",
            subject_id=run_head.subject_id,
            record_id=run_head.revision.head,
            fingerprint=run_head.revision.fingerprint,
        ),
        canonical_diagnostic_cut_bytes=cut.canonical_bytes(),
        canonical_provenance_bytes=provenance.canonical_bytes(),
        observer_registry=registry.reference,
        diagnostic_observation=observation.reference,
        source_capture_inventory_fingerprint=fault_capture_inventory_fingerprint((capture,)),
        fault_rule_registry_reference=rule_ref,
        fault_rule_registry_selection=rule_selection,
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
    )
    issuance = _retained(
        FaultDiagnosticIssuanceRecordV1(
            subject=issuance_subject,
            observation_subject_fingerprint=_digest(issuance_subject.canonical_bytes()),
            issuance_id="issuance",
            issuer_authority="issuer",
            observer_contract_version="v1",
        )
    )
    request = PrepareTerminalRecoveryFaultV1(
        command_id="command",
        run=run,
        cut=cut,
        provenance=provenance,
        authority=RegisteredRecoveryFaultObserverAuthorityV1(
            observer_registry=FaultBrokerSourceRefV1.model_validate(
                registry.reference.model_dump()
            ),
            diagnostic_observation=FaultBrokerSourceRefV1.model_validate(
                observation.reference.model_dump()
            ),
            issuance=FaultBrokerSourceRefV1.model_validate(issuance.reference.model_dump()),
            observation_subject_fingerprint=_digest(issuance_subject.canonical_bytes()),
        ),
    )
    retained = FaultObserverRetainedInputsV1(
        observer_registry=_owner_retained(registry),
        observation=_owner_retained(observation),
        issuance=_owner_retained(issuance),
        ordered_captures=(_owner_retained(capture),),
    )
    return request, retained, selected_rules


async def fixture(
    version: Literal["v2", "v3"] = "v3",
) -> tuple[PrepareTerminalRecoveryFaultV1, FaultObserverRetainedInputsV1]:
    """Return the two retained observer inputs used by O/B1 callers."""
    request, retained, _selected_rules = await full_fixture(version)
    return request, retained
