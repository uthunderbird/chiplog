"""Builders for mandate lifecycle contract consumers."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ProposedScheduledMandateBudgetV1,
    ScheduledMandateBudgetV1,
    ScheduledMandateConsumptionBasisV1,
    ScheduledSystemMandateV2,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_mandate_lifecycle_contracts import (
    IssueScheduledMandateV1,
    MandateAdministrativeSourceV1,
    MandateGenesisAbsencesV1,
    PreparedMandateIssuanceV1,
    ProposedScheduledSystemMandateV2,
)
from tests.support.scheduler_execution import DIGEST, call_head, clock, mandate


def reference(record_id: str, payload: bytes) -> Present:
    return Present(head=record_id, fingerprint=hashlib.sha256(payload).hexdigest())


def selected_mandate(
    generation: int = 0, mandate_id: str = "mandate", max_cycles: int | None = None
) -> SelectedScheduledSystemMandateV2:
    original = mandate().mandate
    horizon = original.scope.horizon
    if max_cycles is not None:
        horizon = horizon.model_copy(update={"max_cycles": max_cycles})
    scope = original.scope.model_copy(
        update={"mandate_id": mandate_id, "issuance_generation": generation, "horizon": horizon}
    )
    body = ScheduledSystemMandateV2(scope=scope)
    raw = body.canonical_bytes()
    return SelectedScheduledSystemMandateV2(
        mandate_head=reference(mandate_id + "/head", raw),
        canonical_mandate_bytes=raw,
        mandate=body,
        issuance_observation=call_head("issuance"),
    )


def admin_source(selected: SelectedScheduledSystemMandateV2) -> MandateAdministrativeSourceV1:
    raw = b"authenticated-admin-source"
    return MandateAdministrativeSourceV1(
        authenticated_admin_act=reference("admin/act", b"act"),
        authority_registry=call_head("authority-registry"),
        current_authority_head=call_head("authority"),
        issuance_policy=call_head("issuance-policy"),
        tenant_id=selected.mandate.scope.tenant_id,
        database_id="database",
        active_contour=call_head("active-contour"),
        trusted_clock=clock(),
        source_cut_fingerprint=DIGEST,
        broker_session_id="broker-session",
        canonical_source_frames=(b"frame",),
        source_record_id="admin/source",
        canonical_source_bytes=raw,
        source_reference=reference("admin/source", raw),
        semantic_scope=selected.mandate.scope,
    )


def proposed_mandate(
    selected: SelectedScheduledSystemMandateV2,
) -> ProposedScheduledSystemMandateV2:
    raw = selected.mandate.canonical_bytes()
    return ProposedScheduledSystemMandateV2(
        proposed_record_id=selected.mandate.scope.mandate_id + "/record",
        canonical_mandate_bytes=raw,
        mandate=selected.mandate,
        external_reference=reference(selected.mandate.scope.mandate_id + "/record", raw),
    )


def proposed_genesis(
    selected: SelectedScheduledSystemMandateV2,
) -> ProposedScheduledMandateBudgetV1:
    basis = ScheduledMandateConsumptionBasisV1(
        mandate_id=selected.mandate.scope.mandate_id,
        selected_predecessor=Absent(),
        primitive_command=call_head("issuance-genesis"),
        primitive_fingerprint=DIGEST,
        delta_cycles=0,
        delta_runs=0,
        delta_consequential_calls=0,
        accounting_policy_version="scheduler-accounting.v1",
    )
    body = ScheduledMandateBudgetV1(
        mandate_id=selected.mandate.scope.mandate_id,
        generation=0,
        predecessor=Absent(),
        cumulative_cycles=0,
        cumulative_runs=0,
        cumulative_consequential_calls=0,
        consumption_basis=basis,
    )
    raw = body.canonical_bytes()
    record_id = selected.mandate.scope.mandate_id + "/budget"
    return ProposedScheduledMandateBudgetV1(
        proposed_record_id=record_id, canonical_budget_bytes=raw, budget=body
    )


def issue(selected: SelectedScheduledSystemMandateV2 | None = None) -> IssueScheduledMandateV1:
    selected = selected or selected_mandate()
    proposed = proposed_mandate(selected)
    return IssueScheduledMandateV1(
        command_id="issue-command",
        mandate_id=selected.mandate.scope.mandate_id,
        proposed_mandate=proposed,
        expected_absences=MandateGenesisAbsencesV1(
            mandate_record=Absent(), budget_record=Absent(), revocation_record=Absent()
        ),
        administrative_source=admin_source(selected),
    )


def prepared_issue(
    selected: SelectedScheduledSystemMandateV2 | None = None,
) -> PreparedMandateIssuanceV1:
    selected = selected or selected_mandate()
    return PreparedMandateIssuanceV1(
        source_request_fingerprint=DIGEST,
        mandate=proposed_mandate(selected),
        budget_genesis=proposed_genesis(selected),
    )


def selected_budget(selected: SelectedScheduledSystemMandateV2) -> SelectedScheduledMandateBudgetV1:
    proposal = proposed_genesis(selected)
    return SelectedScheduledMandateBudgetV1(
        budget_head=reference("selected-budget/head", proposal.canonical_budget_bytes),
        canonical_budget_bytes=proposal.canonical_budget_bytes,
        budget=proposal.budget,
    )
