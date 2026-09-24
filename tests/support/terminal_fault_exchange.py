"""Builders for complete terminal-recovery-fault exchange regression tests."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    RecoverySourceRecord,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_fault_rule_contracts import (
    SelectedFaultRuleRegistryV1,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    FrozenRunBindings,
    RecoveryFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    decode_frontier_registry,
)
from chiplog.capabilities.agent_loop.terminal_recovery_fault_contracts import (
    TERMINAL_RECOVERY_FAULT_PHYSICAL_SCHEMA,
    PreparedTerminalRecoveryFaultV1,
    PrepareTerminalRecoveryFaultV1,
    RecoveryFaultDiagnosticCutV1,
    RecoveryFaultFindingV1,
    TerminalRecoveryFaultCanonicalMemberV1,
    TerminalRecoveryFaultRecordV1,
    WorkerFaultPublicationAuthorityV1,
    diagnostic_inventory_fingerprint,
    recovery_fault_inventory,
)


def _head(subject: str) -> CallSubjectHead:
    digest = hashlib.sha256(subject.encode()).hexdigest()
    return CallSubjectHead(
        subject_id=subject, revision=Present(head="head:" + subject, fingerprint=digest)
    )


def canonical_recovery_cut(request: PrepareTerminalRecoveryFaultV1) -> ExecutionRecoveryCut:
    """Build a syntactically complete recovery cut joined to a diagnostic cut."""
    diagnostic = request.cut
    return ExecutionRecoveryCut(
        tenant_id=diagnostic.tenant_id,
        database_id=diagnostic.database_id,
        tenant_commit_sequence=diagnostic.tenant_commit_sequence,
        materialization_commitment=diagnostic.materialization_commitment,
        current_run=diagnostic.current_run,
        complete_ordered_run_lineage=(request.run,),
        complete_ordered_responses=(),
        frontier=RecoveryFrontier(
            tenant_id=diagnostic.tenant_id,
            run_id=request.run.run_id,
            tenant_commit_sequence=diagnostic.tenant_commit_sequence,
            registry=decode_frontier_registry(
                RECOVERY_FRONTIER_REGISTRY_SCHEMA,
                diagnostic.canonical_registry_bytes,
                expected_reference=diagnostic.frontier_registry,
            ),
            ordered_members=(
                FrontierMember(
                    family="RUN",
                    subject=request.run.run_id,
                    branch="SUSPENDED",
                    ordered_heads=(request.cut.current_run.revision,),
                    fingerprint="a" * 64,
                ),
            ),
            ordered_calls=(),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="b" * 64,
        ),
        current_bindings=FrozenRunBindings(
            objective="recover",
            requested_work="recover",
            prompt_artifact=_head("prompt").revision,
            ordered_tool_specs=(_head("tool").revision,),
            generated_schema=_head("schema").revision,
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=_head("scope").revision,
            authority_mandate_heads=(),
            policy=_head("policy").revision,
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=_head("source"),
                schema_id="source.v1",
                canonical_record_bytes=b"source",
                selected_decision=_head("decision"),
                physical_record=_head("physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="c" * 64,
    )


def refreshed_request(
    request: PrepareTerminalRecoveryFaultV1,
    cut: RecoveryFaultDiagnosticCutV1,
    *,
    worker: bool = False,
) -> PrepareTerminalRecoveryFaultV1:
    """Recompute the diagnostic inventory hash after changing its retained view."""
    refreshed_cut = RecoveryFaultDiagnosticCutV1.model_validate(
        cut.model_dump()
        | {
            "complete_inventory_fingerprint": diagnostic_inventory_fingerprint(
                recovery_fault_inventory(cut)
            )
        }
    )
    authority = request.authority
    if worker:
        authority = WorkerFaultPublicationAuthorityV1(
            fence=NonSchedulerFence(
                lineage=NotApplicable(),
                physical_root=NotApplicable(),
                lease=NotApplicable(),
                clock_proof=NotApplicable(),
                run_id=request.run.run_id,
                run_head=request.run.head,
                worker_session_id="worker",
                runtime_generation="generation",
            ),
            invocation=_head("worker-invocation"),
        )
    return PrepareTerminalRecoveryFaultV1.model_validate(
        request.model_dump()
        | {"cut": refreshed_cut.model_dump(), "authority": authority.model_dump()}
    )


def prepared_result(
    request: PrepareTerminalRecoveryFaultV1,
    finding: RecoveryFaultFindingV1,
) -> PreparedTerminalRecoveryFaultV1:
    """Seal one rehashed record and physical member for a changed request."""
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
        findings=(finding,),
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
    commitment = hashlib.sha256(
        b"chiplog.execution.terminal-recovery-fault-result.v1\0"
        + record.source_request_fingerprint.encode()
        + b"\0"
        + member.canonical_bytes()
    ).hexdigest()
    return PreparedTerminalRecoveryFaultV1(
        source_request_fingerprint=record.source_request_fingerprint,
        member=member,
        complete_commitment=commitment,
    )


def registered_finding(selected_rules: SelectedFaultRuleRegistryV1) -> RecoveryFaultFindingV1:
    """Return the one fixture finding with its invariant bound to the selected rule."""
    rule = selected_rules.registry.ordered_rules[0]
    fingerprint = hashlib.sha256(rule.canonical_bytes()).hexdigest()
    return RecoveryFaultFindingV1(
        code=rule.finding_code,
        invariant=CallSubjectHead(
            subject_id=rule.rule_id,
            revision=Present(
                head=rule.schema_id + ":" + fingerprint,
                fingerprint=fingerprint,
            ),
        ),
        affected_observation_ordinals=(0,),
        expected_relation=rule.expected_relation,
        detail="retained recovery-cut bytes",
    )
