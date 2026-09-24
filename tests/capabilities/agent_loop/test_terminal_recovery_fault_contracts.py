"""Phase-C representation contracts for one terminal recovery fault record."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

import pytest
from pydantic import ValidationError
from tests.support.execution_fan_out import fixture as execution_fixture
from tests.support.execution_versions import execution_run_v3
from tests.support.fault_observer_sources import full_fixture as observer_full_fixture

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    IndividualSubject,
    LeaseBinding,
    NonSchedulerFence,
    NotApplicable,
    PhysicalRootBinding,
    Present,
    SchedulerExecutionFence,
    TrustedClockProofRef,
)
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
from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    AGENT_LOOP_OWNER,
    TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA,
    DirectDiagnosticProvenanceV1,
    FaultBrokerSourceRefV1,
    FaultRecoveryRecordIntegrityError,
    PreparedTerminalRecoveryFaultV1,
    PrepareTerminalRecoveryFaultV1,
    RecoveryFaultDiagnosticCutV1,
    RecoveryFaultFindingV1,
    RecoveryFaultInventoryV1,
    RecoveryFaultObservedMemberV1,
    RegisteredRecoveryFaultObserverAuthorityV1,
    TerminalRecoveryFaultCanonicalMemberV1,
    TerminalRecoveryFaultRecordV1,
    UndecodableRecoveryFaultFrontierV1,
    WorkerFaultPublicationAuthorityV1,
    decode_terminal_recovery_fault_member,
    diagnostic_inventory_fingerprint,
    recovery_fault_inventory,
    terminal_recovery_fault_ref,
    validate_terminal_recovery_fault_exchange,
)


def _head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name,
        revision=Present(
            head="physical:" + name,
            fingerprint=hashlib.sha256(name.encode()).hexdigest(),
        ),
    )


async def _suspended_run(version: str) -> ExecutionRun:
    original = execution_run_v3() if version == "v3" else (await execution_fixture()).captured_run
    pending = original.model_copy(update={"state": "SUSPENDED", "head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


def _observation(raw: bytes = b"{invalid") -> RecoveryFaultObservedMemberV1:
    return RecoveryFaultObservedMemberV1(
        role="SUSPENSION_PAIR",
        ordinal=0,
        owner="agent_loop",
        record_kind="SUSPENSION_PAIR",
        declared_schema_id="chiplog.execution.suspension-pair.v2",
        logical_subject_id="run",
        physical_record_id="pair-record",
        selected_decision=_head("selected-decision"),
        claimed_fingerprint="0" * 64,
        observed_bytes=raw,
        observed_bytes_fingerprint=hashlib.sha256(raw).hexdigest(),
        capture=FaultBrokerSourceRefV1(
            source_kind="FAULT_CAPTURE",
            schema_id="chiplog.broker.fault-capture.v1",
            source_id="capture",
            fingerprint=hashlib.sha256(b"capture").hexdigest(),
        ),
    )


def _record() -> TerminalRecoveryFaultRecordV1:
    observed = _observation()
    rows = (
        RecoveryRegistryRow(
            family="RUN",
            ordinal=0,
            subject_extractor_id="run",
            cardinality_rule="one",
            terminal_conflict_rule="reject",
            serialization_rule="canonical",
            canonicalization_version="1",
        ),
    )
    return _record_from(observed, rows)


def _prepared_result(
    request: PrepareTerminalRecoveryFaultV1,
    findings: tuple[RecoveryFaultFindingV1, ...],
) -> PreparedTerminalRecoveryFaultV1:
    record = TerminalRecoveryFaultRecordV1(
        tenant_id=request.cut.tenant_id,
        database_id=request.cut.database_id,
        command_id=request.command_id,
        original_run=request.cut.current_run,
        suspension_baseline=request.cut.suspension_baseline,
        suspension_pair=request.cut.suspension_pair,
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        diagnostic_cut=request.cut,
        provenance=request.provenance,
        authority=request.authority,
        disposition="TERMINAL_RECOVERY_FAULT",
        findings=findings,
        continuation="FORBIDDEN",
        predecessor_fault=Absent(),
    )
    body = record.canonical_bytes()
    fingerprint = hashlib.sha256(body).hexdigest()
    member = TerminalRecoveryFaultCanonicalMemberV1(
        record_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA + ":" + fingerprint,
        canonical_record_bytes=body,
        fingerprint=fingerprint,
    )
    return PreparedTerminalRecoveryFaultV1(
        source_request_fingerprint=record.source_request_fingerprint,
        member=member,
        complete_commitment=hashlib.sha256(
            b"chiplog.execution.terminal-recovery-fault-result.v1\0"
            + record.source_request_fingerprint.encode()
            + b"\0"
            + member.canonical_bytes()
        ).hexdigest(),
    )


def _worker_fence(run: ExecutionRun, kind: str) -> NonSchedulerFence | SchedulerExecutionFence:
    if kind == "non_scheduler":
        return NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id="worker",
            runtime_generation="generation",
        )
    return SchedulerExecutionFence(
        run_head=run.head,
        lineage=ExecutionLineageBinding(
            root_id="root",
            subject=IndividualSubject(occurrence_id="occurrence"),
            root_fingerprint="1" * 64,
            lineage_head="lineage-head",
            initial_run_id="initial-run",
            current_run_id=run.run_id,
            schedule_id="schedule",
            schedule_revision="schedule-v1",
            policy_revision="policy-v1",
        ),
        physical_root=PhysicalRootBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=1,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        lease=LeaseBinding(
            lease_head="lease-head",
            holder_id="worker",
            holder_session_id="worker-session",
            lease_id="lease",
            generation=1,
            trusted_expiry=2,
            clock_contract_version="clock-v1",
        ),
        clock_proof=TrustedClockProofRef(
            proof_id="proof",
            proof_fingerprint="2" * 64,
            proof_version="proof-v1",
            clock_contract_version="clock-v1",
            fence_fingerprint="3" * 64,
            command_id="command",
            command_payload_fingerprint="4" * 64,
            submission_id="submission",
        ),
    )


async def _worker_exchange(
    version: str, fence_kind: str
) -> tuple[
    PrepareTerminalRecoveryFaultV1,
    PreparedTerminalRecoveryFaultV1,
    SelectedFaultRuleRegistryV1,
]:
    record = _record()
    original = await _suspended_run(version)
    pending = original.model_copy(
        update={"suspension_baseline": record.diagnostic_cut.suspension_baseline, "head": "pending"}
    )
    run = pending.model_copy(update={"head": "loop:" + pending.digest()})
    run_ref = CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )
    rule = FaultRuleV1(
        rule_id="rule",
        version="1",
        classifier_id=record.diagnostic_cut.classifier.subject_id,
        classifier_version=record.diagnostic_cut.classifier.revision.head,
        finding_code="CORRUPT_CANONICAL_BYTES",
        expected_relation="canonical-body",
        predicate_kind="CANONICAL_BODY",
        verifier_id="verifier",
        verifier_version="1",
        ordered_observation_roles=(
            FaultRuleObservationRoleV1(
                role="SUSPENSION_PAIR",
                min_count=1,
                max_count=1,
                allowed_owner_kinds=(
                    FaultRuleSourceKindV1(
                        owner="agent_loop",
                        record_kind="SUSPENSION_PAIR",
                        schema_id="chiplog.execution.suspension-pair.v2",
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
        version="1",
        classifier_id=rule.classifier_id,
        classifier_version=rule.classifier_version,
        disposition_version=record.diagnostic_cut.disposition_version,
        ordered_rules=(rule,),
    )
    registry_raw = registry.canonical_bytes()
    registry_digest = hashlib.sha256(registry_raw).hexdigest()
    registry_ref = CallSubjectHead(
        subject_id=registry.registry_id,
        revision=Present(
            head=registry.schema_id + ":" + registry_digest,
            fingerprint=registry_digest,
        ),
    )
    selected_decision = _head("fault-rules-selected")
    selected_rules = SelectedFaultRuleRegistryV1(
        registry=registry,
        registry_reference=registry_ref,
        canonical_registry_bytes=registry_raw,
        selected_decision=selected_decision,
    )
    unsealed_cut = record.diagnostic_cut.model_copy(
        update={
            "current_run": run_ref,
            "fault_rule_registry": registry_ref,
            "fault_rule_registry_selection": selected_decision,
        }
    )
    cut = RecoveryFaultDiagnosticCutV1.model_validate(
        unsealed_cut.model_dump()
        | {
            "complete_inventory_fingerprint": diagnostic_inventory_fingerprint(
                recovery_fault_inventory(unsealed_cut)
            )
        }
    )
    finding = record.findings[0].model_copy(
        update={
            "invariant": CallSubjectHead(
                subject_id=rule.rule_id,
                revision=Present(
                    head=rule.schema_id + ":" + hashlib.sha256(rule.canonical_bytes()).hexdigest(),
                    fingerprint=hashlib.sha256(rule.canonical_bytes()).hexdigest(),
                ),
            )
        }
    )
    request = PrepareTerminalRecoveryFaultV1(
        command_id=record.command_id,
        run=run,
        cut=cut,
        provenance=record.provenance,
        authority=WorkerFaultPublicationAuthorityV1(
            fence=_worker_fence(run, fence_kind), invocation=_head("worker-invocation")
        ),
    )
    return request, _prepared_result(request, (finding,)), selected_rules


def _record_from(
    observed: RecoveryFaultObservedMemberV1, rows: tuple[RecoveryRegistryRow, ...]
) -> TerminalRecoveryFaultRecordV1:
    registry = RecoveryFrontierRegistry(
        registry_id="frontier-registry",
        version="1",
        fingerprint=frontier_registry_content_fingerprint("frontier-registry", "1", rows),
        ordered_rows=rows,
    )
    inventory_fields: dict[str, Any] = dict(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=1,
        materialization_commitment="c" * 64,
        current_run=_head("run"),
        suspension_baseline=_head("baseline"),
        suspension_pair=_head("pair"),
        frontier_registry=frontier_registry_reference(registry),
        canonical_registry_bytes=registry.canonical_bytes(),
        classifier=_head("classifier"),
        disposition_version="1",
        fault_rule_registry=_head("fault-rules"),
        fault_rule_registry_selection=_head("fault-rules-selected"),
        ordered_observations=(observed,),
    )
    inventory = RecoveryFaultInventoryV1(**inventory_fields)
    cut = RecoveryFaultDiagnosticCutV1(
        **inventory_fields,
        complete_inventory_fingerprint=diagnostic_inventory_fingerprint(inventory),
        frontier_observation=UndecodableRecoveryFaultFrontierV1(
            expected_schema_id="chiplog.execution.recovery-cut.v1",
            raw_bytes=b"{invalid",
            capture=observed.capture,
        ),
        expected_fault=Absent(),
    )
    return TerminalRecoveryFaultRecordV1(
        tenant_id="tenant",
        database_id="database",
        command_id="command",
        original_run=_head("run"),
        suspension_baseline=_head("baseline"),
        suspension_pair=_head("pair"),
        source_request_fingerprint="a" * 64,
        diagnostic_cut=cut,
        provenance=DirectDiagnosticProvenanceV1(
            trigger_capture=observed.capture,
            reason="RECOVERY_INPUT_UNDECODABLE",
        ),
        authority=RegisteredRecoveryFaultObserverAuthorityV1(
            observer_registry=FaultBrokerSourceRefV1(
                source_kind="FAULT_OBSERVER_REGISTRY",
                schema_id="chiplog.broker.fault-observer-registry.v1",
                source_id="observer-registry",
                fingerprint="d" * 64,
            ),
            diagnostic_observation=FaultBrokerSourceRefV1(
                source_kind="FAULT_OBSERVATION",
                schema_id="chiplog.broker.fault-observation.v1",
                source_id="observation",
                fingerprint="e" * 64,
            ),
            issuance=FaultBrokerSourceRefV1(
                source_kind="FAULT_ISSUANCE",
                schema_id="chiplog.broker.fault-diagnostic-issuance.v1",
                source_id="issuance",
                fingerprint="f" * 64,
            ),
            observation_subject_fingerprint="0" * 64,
        ),
        disposition="TERMINAL_RECOVERY_FAULT",
        findings=(
            RecoveryFaultFindingV1(
                code="CORRUPT_CANONICAL_BYTES",
                invariant=_head("rule"),
                affected_observation_ordinals=(0,),
                expected_relation="canonical-body",
                detail="retained malformed bytes",
            ),
        ),
        continuation="FORBIDDEN",
        predecessor_fault=Absent(),
    )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_exchange_requires_exact_selected_rule_registry_and_registered_finding(
    version: Literal["v2", "v3"],
) -> None:
    request, observer_sources, selected_rules = await observer_full_fixture(version=version)
    rule = selected_rules.registry.ordered_rules[0]
    rule_fingerprint = hashlib.sha256(rule.canonical_bytes()).hexdigest()
    finding = RecoveryFaultFindingV1(
        code=rule.finding_code,
        invariant=CallSubjectHead(
            subject_id=rule.rule_id,
            revision=Present(
                head=rule.schema_id + ":" + rule_fingerprint,
                fingerprint=rule_fingerprint,
            ),
        ),
        affected_observation_ordinals=(0,),
        expected_relation=rule.expected_relation,
        detail="retained malformed bytes",
    )
    result = _prepared_result(request, (finding,))

    validate_terminal_recovery_fault_exchange(
        request, result, selected_rules, observer_sources=observer_sources
    )
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="fault rule registry selection"):
        validate_terminal_recovery_fault_exchange(
            request,
            result,
            selected_rules.model_copy(update={"selected_decision": _head("substituted")}),
            observer_sources=observer_sources,
        )
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="absent observation slot"):
        validate_terminal_recovery_fault_exchange(
            request,
            _prepared_result(
                request, (finding.model_copy(update={"affected_observation_ordinals": (1,)}),)
            ),
            selected_rules,
            observer_sources=observer_sources,
        )
    return

    run = await _suspended_run(version)
    record = _record()
    pending = run.model_copy(
        update={"suspension_baseline": record.diagnostic_cut.suspension_baseline, "head": "pending"}
    )
    run = pending.model_copy(update={"head": "loop:" + pending.digest()})
    run_ref = CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=run.digest()),
    )
    rule = FaultRuleV1(
        rule_id="rule",
        version="1",
        classifier_id=record.diagnostic_cut.classifier.subject_id,
        classifier_version=record.diagnostic_cut.classifier.revision.head,
        finding_code="CORRUPT_CANONICAL_BYTES",
        expected_relation="canonical-body",
        predicate_kind="CANONICAL_BODY",
        verifier_id="verifier",
        verifier_version="1",
        ordered_observation_roles=(
            FaultRuleObservationRoleV1(
                role="SUSPENSION_PAIR",
                min_count=1,
                max_count=1,
                allowed_owner_kinds=(
                    FaultRuleSourceKindV1(
                        owner="agent_loop",
                        record_kind="SUSPENSION_PAIR",
                        schema_id="chiplog.execution.suspension-pair.v2",
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
        version="1",
        classifier_id=rule.classifier_id,
        classifier_version=rule.classifier_version,
        disposition_version=record.diagnostic_cut.disposition_version,
        ordered_rules=(rule,),
    )
    registry_raw = registry.canonical_bytes()
    registry_digest = hashlib.sha256(registry_raw).hexdigest()
    registry_ref = CallSubjectHead(
        subject_id=registry.registry_id,
        revision=Present(
            head=registry.schema_id + ":" + registry_digest,
            fingerprint=registry_digest,
        ),
    )
    selected_decision = _head("fault-rules-selected")
    selected_rules = SelectedFaultRuleRegistryV1(
        registry=registry,
        registry_reference=registry_ref,
        canonical_registry_bytes=registry_raw,
        selected_decision=selected_decision,
    )
    cut_wire = record.diagnostic_cut.model_dump() | {
        "current_run": run_ref.model_dump(),
        "fault_rule_registry": registry_ref.model_dump(),
        "fault_rule_registry_selection": selected_decision.model_dump(),
    }
    inventory = recovery_fault_inventory(
        record.diagnostic_cut.model_copy(
            update={
                "current_run": run_ref,
                "fault_rule_registry": registry_ref,
                "fault_rule_registry_selection": selected_decision,
            }
        )
    )
    cut = RecoveryFaultDiagnosticCutV1.model_validate(
        cut_wire | {"complete_inventory_fingerprint": diagnostic_inventory_fingerprint(inventory)}
    )
    finding = record.findings[0].model_copy(
        update={
            "invariant": CallSubjectHead(
                subject_id=rule.rule_id,
                revision=Present(
                    head=rule.schema_id + ":" + hashlib.sha256(rule.canonical_bytes()).hexdigest(),
                    fingerprint=hashlib.sha256(rule.canonical_bytes()).hexdigest(),
                ),
            )
        }
    )
    request = PrepareTerminalRecoveryFaultV1(
        command_id=record.command_id,
        run=run,
        cut=cut,
        provenance=record.provenance,
        authority=record.authority,
    )
    fault = TerminalRecoveryFaultRecordV1(
        tenant_id=cut.tenant_id,
        database_id=cut.database_id,
        command_id=request.command_id,
        original_run=run_ref,
        suspension_baseline=cut.suspension_baseline,
        suspension_pair=cut.suspension_pair,
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        diagnostic_cut=cut,
        provenance=request.provenance,
        authority=request.authority,
        disposition="TERMINAL_RECOVERY_FAULT",
        findings=(finding,),
        continuation="FORBIDDEN",
        predecessor_fault=Absent(),
    )
    raw = fault.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    member = TerminalRecoveryFaultCanonicalMemberV1(
        record_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )
    result = PreparedTerminalRecoveryFaultV1(
        source_request_fingerprint=fault.source_request_fingerprint,
        member=member,
        complete_commitment=hashlib.sha256(
            b"chiplog.execution.terminal-recovery-fault-result.v1\0"
            + fault.source_request_fingerprint.encode()
            + b"\0"
            + member.canonical_bytes()
        ).hexdigest(),
    )

    validate_terminal_recovery_fault_exchange(request, result, selected_rules)

    def rebuilt(
        mutated_request: PrepareTerminalRecoveryFaultV1,
        candidate_findings: tuple[RecoveryFaultFindingV1, ...] = (finding,),
    ) -> PreparedTerminalRecoveryFaultV1:
        candidate = TerminalRecoveryFaultRecordV1(
            tenant_id=mutated_request.cut.tenant_id,
            database_id=mutated_request.cut.database_id,
            command_id=mutated_request.command_id,
            original_run=mutated_request.cut.current_run,
            suspension_baseline=mutated_request.cut.suspension_baseline,
            suspension_pair=mutated_request.cut.suspension_pair,
            source_request_fingerprint=hashlib.sha256(
                mutated_request.canonical_bytes()
            ).hexdigest(),
            diagnostic_cut=mutated_request.cut,
            provenance=mutated_request.provenance,
            authority=mutated_request.authority,
            disposition="TERMINAL_RECOVERY_FAULT",
            findings=candidate_findings,
            continuation="FORBIDDEN",
            predecessor_fault=Absent(),
        )
        body = candidate.canonical_bytes()
        digest = hashlib.sha256(body).hexdigest()
        carrier = TerminalRecoveryFaultCanonicalMemberV1(
            record_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA + ":" + digest,
            canonical_record_bytes=body,
            fingerprint=digest,
        )
        request_fingerprint = candidate.source_request_fingerprint
        return PreparedTerminalRecoveryFaultV1(
            source_request_fingerprint=request_fingerprint,
            member=carrier,
            complete_commitment=hashlib.sha256(
                b"chiplog.execution.terminal-recovery-fault-result.v1\0"
                + request_fingerprint.encode()
                + b"\0"
                + carrier.canonical_bytes()
            ).hexdigest(),
        )

    def refreshed(mutated: PrepareTerminalRecoveryFaultV1) -> PrepareTerminalRecoveryFaultV1:
        wire = mutated.cut.model_dump()
        fingerprint = diagnostic_inventory_fingerprint(recovery_fault_inventory(mutated.cut))
        return PrepareTerminalRecoveryFaultV1.model_validate(
            mutated.model_dump() | {"cut": wire | {"complete_inventory_fingerprint": fingerprint}}
        )

    def bad_ref(ref: FaultBrokerSourceRefV1) -> FaultBrokerSourceRefV1:
        return ref.model_copy(update={"schema_id": "wrong.v1"})

    authority = request.authority
    assert isinstance(authority, RegisteredRecoveryFaultObserverAuthorityV1)
    for field in ("observer_registry", "diagnostic_observation", "issuance"):
        changed_authority = authority.model_copy(update={field: bad_ref(getattr(authority, field))})
        changed = request.model_copy(update={"authority": changed_authority})
        with pytest.raises(FaultRecoveryRecordIntegrityError, match="observer authority source"):
            validate_terminal_recovery_fault_exchange(changed, rebuilt(changed), selected_rules)
    changed_capture = request.cut.ordered_observations[0].model_copy(
        update={"capture": bad_ref(request.cut.ordered_observations[0].capture)}
    )
    changed_cut = request.cut.model_copy(update={"ordered_observations": (changed_capture,)})
    changed = refreshed(request.model_copy(update={"cut": changed_cut}))
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="observed capture"):
        validate_terminal_recovery_fault_exchange(changed, rebuilt(changed), selected_rules)

    second = request.cut.ordered_observations[0].model_copy(update={"ordinal": 1})
    two_cut = request.cut.model_copy(
        update={"ordered_observations": (request.cut.ordered_observations[0], second)}
    )
    two_request = refreshed(request.model_copy(update={"cut": two_cut}))
    two_finding = finding.model_copy(update={"affected_observation_ordinals": (0, 1)})
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="registered role count"):
        validate_terminal_recovery_fault_exchange(
            two_request, rebuilt(two_request, (two_finding,)), selected_rules
        )

    extra = second.model_copy(update={"role": "UNDECLARED"})
    extra_cut = request.cut.model_copy(
        update={"ordered_observations": (request.cut.ordered_observations[0], extra)}
    )
    extra_request = refreshed(request.model_copy(update={"cut": extra_cut}))
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="undeclared role"):
        validate_terminal_recovery_fault_exchange(
            extra_request, rebuilt(extra_request, (two_finding,)), selected_rules
        )

    foreign = request.cut.ordered_observations[0].model_copy(update={"owner": "foreign"})
    foreign_cut = request.cut.model_copy(update={"ordered_observations": (foreign,)})
    foreign_request = refreshed(request.model_copy(update={"cut": foreign_cut}))
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="registered source class"):
        validate_terminal_recovery_fault_exchange(
            foreign_request, rebuilt(foreign_request), selected_rules
        )

    rehashed_fault = fault.model_copy(update={"source_request_fingerprint": "0" * 64})
    rehashed_raw = rehashed_fault.canonical_bytes()
    rehashed_member = TerminalRecoveryFaultCanonicalMemberV1(
        record_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA
        + ":"
        + hashlib.sha256(rehashed_raw).hexdigest(),
        canonical_record_bytes=rehashed_raw,
        fingerprint=hashlib.sha256(rehashed_raw).hexdigest(),
    )
    rehashed_result = result.model_copy(
        update={
            "member": rehashed_member,
            "complete_commitment": hashlib.sha256(
                b"chiplog.execution.terminal-recovery-fault-result.v1\0"
                + result.source_request_fingerprint.encode()
                + b"\0"
                + rehashed_member.canonical_bytes()
            ).hexdigest(),
        }
    )
    with pytest.raises(FaultRecoveryRecordIntegrityError):
        validate_terminal_recovery_fault_exchange(request, rehashed_result, selected_rules)

    with pytest.raises(FaultRecoveryRecordIntegrityError):
        validate_terminal_recovery_fault_exchange(
            request,
            result,
            selected_rules.model_copy(update={"selected_decision": _head("substituted")}),
        )

    with pytest.raises(ValidationError):
        PrepareTerminalRecoveryFaultV1.model_validate(
            request.model_dump() | {"cut": request.cut.model_dump() | {"tenant_id": "other"}}
        )
    with pytest.raises(ValidationError):
        PrepareTerminalRecoveryFaultV1.model_validate(
            request.model_dump()
            | {
                "cut": request.cut.model_dump()
                | {"suspension_baseline": _head("other").model_dump()}
            }
        )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_native_v2_and_v3_suspended_runs_are_retained_without_rewriting(version: str) -> None:
    run = await _suspended_run(version)

    assert run.state == "SUSPENDED"
    assert run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest()


@pytest.mark.parametrize("version", ("v2", "v3"))
@pytest.mark.parametrize("fence_kind", ("non_scheduler", "scheduler"))
async def test_worker_authority_requires_fence_to_bind_selected_native_run(
    version: str, fence_kind: str
) -> None:
    request, result, selected_rules = await _worker_exchange(version, fence_kind)
    inventory_fingerprint = diagnostic_inventory_fingerprint(recovery_fault_inventory(request.cut))

    assert inventory_fingerprint == request.cut.complete_inventory_fingerprint
    validate_terminal_recovery_fault_exchange(request, result, selected_rules)
    findings = decode_terminal_recovery_fault_member(result.member).record.findings

    authority = request.authority
    assert isinstance(authority, WorkerFaultPublicationAuthorityV1)
    fence = authority.fence
    mutations: tuple[NonSchedulerFence | SchedulerExecutionFence, ...]
    if isinstance(fence, NonSchedulerFence):
        mutations = (
            fence.model_copy(update={"run_id": "wrong-run"}),
            fence.model_copy(update={"run_head": "wrong-head"}),
        )
    else:
        mutations = (
            fence.model_copy(
                update={"lineage": fence.lineage.model_copy(update={"current_run_id": "wrong-run"})}
            ),
            fence.model_copy(update={"run_head": "wrong-head"}),
        )

    for changed_fence in mutations:
        changed = request.model_copy(
            update={"authority": authority.model_copy(update={"fence": changed_fence})}
        )
        assert changed.cut == request.cut
        assert (
            diagnostic_inventory_fingerprint(recovery_fault_inventory(changed.cut))
            == inventory_fingerprint
        )
        with pytest.raises(
            FaultRecoveryRecordIntegrityError,
            match="worker fence differs from selected native Run",
        ):
            validate_terminal_recovery_fault_exchange(
                changed, _prepared_result(changed, findings), selected_rules
            )

    def refreshed(changed_cut: RecoveryFaultDiagnosticCutV1) -> PrepareTerminalRecoveryFaultV1:
        cut = RecoveryFaultDiagnosticCutV1.model_validate(
            changed_cut.model_dump()
            | {
                "complete_inventory_fingerprint": diagnostic_inventory_fingerprint(
                    recovery_fault_inventory(changed_cut)
                )
            }
        )
        return request.model_copy(update={"cut": cut})

    observed = request.cut.ordered_observations[0]
    for changed_observation, match in (
        (observed.model_copy(update={"role": "UNDECLARED"}), "undeclared role"),
        (observed.model_copy(update={"owner": "foreign"}), "registered source class"),
    ):
        changed = refreshed(
            request.cut.model_copy(update={"ordered_observations": (changed_observation,)})
        )
        with pytest.raises(FaultRecoveryRecordIntegrityError, match=match):
            validate_terminal_recovery_fault_exchange(
                changed, _prepared_result(changed, findings), selected_rules
            )
    second = observed.model_copy(update={"ordinal": 1})
    changed = refreshed(request.cut.model_copy(update={"ordered_observations": (observed, second)}))
    count_finding = findings[0].model_copy(update={"affected_observation_ordinals": (0, 1)})
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="registered role count"):
        validate_terminal_recovery_fault_exchange(
            changed, _prepared_result(changed, (count_finding,)), selected_rules
        )

    bad_record = decode_terminal_recovery_fault_member(result.member).record.model_copy(
        update={"source_request_fingerprint": "0" * 64}
    )
    bad_body = bad_record.canonical_bytes()
    bad_member = TerminalRecoveryFaultCanonicalMemberV1(
        record_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA
        + ":"
        + hashlib.sha256(bad_body).hexdigest(),
        canonical_record_bytes=bad_body,
        fingerprint=hashlib.sha256(bad_body).hexdigest(),
    )
    bad_result = result.model_copy(
        update={
            "member": bad_member,
            "complete_commitment": hashlib.sha256(
                b"chiplog.execution.terminal-recovery-fault-result.v1\0"
                + result.source_request_fingerprint.encode()
                + b"\0"
                + bad_member.canonical_bytes()
            ).hexdigest(),
        }
    )
    with pytest.raises(FaultRecoveryRecordIntegrityError, match="fault record does not retain"):
        validate_terminal_recovery_fault_exchange(request, bad_result, selected_rules)


def test_observed_invalid_or_empty_bytes_keep_claimed_and_actual_hashes_separate() -> None:
    for raw in (b"", b"{invalid", b'{"z":1,"a":2}'):
        observed = _observation(raw)
        assert observed.observed_bytes == raw
        assert observed.claimed_fingerprint == "0" * 64
        assert observed.observed_bytes_fingerprint == hashlib.sha256(raw).hexdigest()

    with pytest.raises(ValidationError):
        RecoveryFaultObservedMemberV1.model_validate(
            _observation(b"payload").model_dump() | {"observed_bytes_fingerprint": "f" * 64}
        )


def test_closed_fault_member_decodes_only_exact_content_identity_and_adapts_to_source() -> None:
    record = _record()
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    member = TerminalRecoveryFaultCanonicalMemberV1(
        record_id=TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )

    decoded = decode_terminal_recovery_fault_member(member)
    assert decoded.record == record
    source = terminal_recovery_fault_ref(decoded, _head("selected-fault"))
    assert source.owner == AGENT_LOOP_OWNER
    assert source.canonical_record_bytes == raw
    assert source.physical_record.revision.head == member.record_id

    with pytest.raises(FaultRecoveryRecordIntegrityError):
        decode_terminal_recovery_fault_member(member.model_copy(update={"record_id": "forged"}))
    with pytest.raises(FaultRecoveryRecordIntegrityError):
        decode_terminal_recovery_fault_member(
            member.model_copy(
                update={
                    "canonical_record_bytes": raw + b" ",
                    "fingerprint": hashlib.sha256(raw + b" ").hexdigest(),
                }
            )
        )
