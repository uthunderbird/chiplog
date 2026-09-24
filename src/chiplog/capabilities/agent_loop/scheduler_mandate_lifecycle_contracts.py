"""Inert owner-local preparation contracts for scheduled mandate lifecycle.

Construction records exact proposed and selected evidence.  It does not authenticate an
administrator, derive authority, perform CAS, or publish the described atomic batch.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from .call_acceptance_contracts import CallSubjectHead
from .recovery_contracts import Absent, Digest, Identity, Present, TrustedClockProofRef, UInt64
from .scheduler_execution_contracts import (
    ProposedScheduledMandateBudgetV1,
    ScheduledMandateRevocationV1,
    ScheduledSystemMandateScopeV2,
    ScheduledSystemMandateV2,
    SchedulerExecutionDTO,
    SelectedScheduledMandateBudgetV1,
    SelectedScheduledSystemMandateV2,
    mandate_is_fresh_at,
)


def _fingerprint(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class MandateAdministrativeSourceV1(SchedulerExecutionDTO):
    """Broker observations for an administrative act; their authenticity is external."""

    kind: Literal["MANDATE_ADMINISTRATIVE_SOURCE_V1"] = "MANDATE_ADMINISTRATIVE_SOURCE_V1"
    authenticated_admin_act: Present
    authority_registry: CallSubjectHead
    current_authority_head: CallSubjectHead
    issuance_policy: CallSubjectHead
    tenant_id: Identity
    database_id: Identity
    active_contour: CallSubjectHead
    trusted_clock: TrustedClockProofRef
    source_cut_fingerprint: Digest
    broker_session_id: Identity
    canonical_source_frames: tuple[bytes, ...] = Field(min_length=1)
    source_record_id: Identity
    canonical_source_bytes: bytes = Field(min_length=1)
    source_reference: Present
    semantic_scope: ScheduledSystemMandateScopeV2

    @model_validator(mode="after")
    def source_tenant_matches_semantic_scope(self) -> MandateAdministrativeSourceV1:
        if self.tenant_id != self.semantic_scope.tenant_id:
            raise ValueError("administrative source tenant differs from semantic scope")
        if _fingerprint(self.canonical_source_bytes) != self.source_reference.fingerprint:
            raise ValueError("administrative source fingerprint differs from source bytes")
        if self.source_record_id != self.source_reference.head:
            raise ValueError("administrative source record ID differs from source reference")
        return self


class ProposedScheduledSystemMandateV2(SchedulerExecutionDTO):
    """A mandate body plus its candidate physical identity, before selection."""

    proposed_record_id: Identity
    canonical_mandate_bytes: bytes = Field(min_length=1)
    mandate: ScheduledSystemMandateV2
    external_reference: Present

    @model_validator(mode="after")
    def bytes_and_reference_match_proposed_body(self) -> ProposedScheduledSystemMandateV2:
        if self.canonical_mandate_bytes != self.mandate.canonical_bytes():
            raise ValueError("proposed mandate bytes differ from canonical body")
        if _fingerprint(self.canonical_mandate_bytes) != self.external_reference.fingerprint:
            raise ValueError("proposed mandate reference fingerprint differs from canonical bytes")
        if self.proposed_record_id != self.external_reference.head:
            raise ValueError("proposed mandate ID differs from external reference")
        return self


class ProposedScheduledMandateRevocationV1(SchedulerExecutionDTO):
    proposed_record_id: Identity
    canonical_revocation_bytes: bytes = Field(min_length=1)
    revocation: ScheduledMandateRevocationV1
    external_reference: Present

    @model_validator(mode="after")
    def bytes_and_reference_match_proposed_body(self) -> ProposedScheduledMandateRevocationV1:
        if self.canonical_revocation_bytes != self.revocation.canonical_bytes():
            raise ValueError("proposed revocation bytes differ from canonical body")
        if _fingerprint(self.canonical_revocation_bytes) != self.external_reference.fingerprint:
            raise ValueError(
                "proposed revocation reference fingerprint differs from canonical bytes"
            )
        if self.proposed_record_id != self.external_reference.head:
            raise ValueError("proposed revocation ID differs from external reference")
        return self


def _require_selected_mandate_bytes(selected: SelectedScheduledSystemMandateV2) -> None:
    if selected.canonical_mandate_bytes != selected.mandate.canonical_bytes():
        raise ValueError("selected mandate bytes differ from canonical mandate body")
    if _fingerprint(selected.canonical_mandate_bytes) != selected.mandate_head.fingerprint:
        raise ValueError("selected mandate fingerprint differs from canonical mandate bytes")


def _require_selected_budget_bytes(selected: SelectedScheduledMandateBudgetV1) -> None:
    if selected.canonical_budget_bytes != selected.budget.canonical_bytes():
        raise ValueError("selected budget bytes differ from canonical budget body")
    if _fingerprint(selected.canonical_budget_bytes) != selected.budget_head.fingerprint:
        raise ValueError("selected budget fingerprint differs from canonical budget bytes")


class MandateGenesisAbsencesV1(SchedulerExecutionDTO):
    """The three distinct absent subjects required for a fresh mandate identity."""

    mandate_record: Absent
    budget_record: Absent
    revocation_record: Absent


class PreparedMandateIssuanceV1(SchedulerExecutionDTO):
    kind: Literal["PREPARED_MANDATE_ISSUANCE_V1"] = "PREPARED_MANDATE_ISSUANCE_V1"
    source_request_fingerprint: Digest
    mandate: ProposedScheduledSystemMandateV2
    budget_genesis: ProposedScheduledMandateBudgetV1

    @model_validator(mode="after")
    def is_a_zero_counter_absent_predecessor_genesis(self) -> PreparedMandateIssuanceV1:
        budget = self.budget_genesis.budget
        basis = budget.consumption_basis
        counters = (
            budget.cumulative_cycles,
            budget.cumulative_runs,
            budget.cumulative_consequential_calls,
        )
        deltas = (basis.delta_cycles, basis.delta_runs, basis.delta_consequential_calls)
        if (
            budget.mandate_id != self.mandate.mandate.scope.mandate_id
            or basis.mandate_id != budget.mandate_id
            or budget.generation != 0
            or not isinstance(budget.predecessor, Absent)
            or not isinstance(basis.selected_predecessor, Absent)
            or counters != (0, 0, 0)
            or deltas != (0, 0, 0)
        ):
            raise ValueError("issuance budget must be a zero-counter absent-predecessor genesis")
        if any(
            type(value) is not int
            for value in (
                budget.generation,
                budget.cumulative_cycles,
                budget.cumulative_runs,
                budget.cumulative_consequential_calls,
                basis.delta_cycles,
                basis.delta_runs,
                basis.delta_consequential_calls,
            )
        ):
            raise ValueError("issuance genesis counters must be actual integer zeroes")
        if self.budget_genesis.canonical_budget_bytes != budget.canonical_bytes():
            raise ValueError("proposed genesis bytes differ from canonical budget body")
        return self


class IssueScheduledMandateV1(SchedulerExecutionDTO):
    kind: Literal["ISSUE_SCHEDULED_MANDATE_V1"] = "ISSUE_SCHEDULED_MANDATE_V1"
    command_id: Identity
    mandate_id: Identity
    proposed_mandate: ProposedScheduledSystemMandateV2
    expected_absences: MandateGenesisAbsencesV1
    administrative_source: MandateAdministrativeSourceV1

    @model_validator(mode="after")
    def issue_binds_one_new_immutable_scope(self) -> IssueScheduledMandateV1:
        scope = self.proposed_mandate.mandate.scope
        if self.mandate_id != scope.mandate_id:
            raise ValueError("issue command and proposed mandate IDs differ")
        if self.administrative_source.semantic_scope != scope:
            raise ValueError("administrative source must retain the exact proposed semantic scope")
        return self


class RevokeScheduledMandateV1(SchedulerExecutionDTO):
    kind: Literal["REVOKE_SCHEDULED_MANDATE_V1"] = "REVOKE_SCHEDULED_MANDATE_V1"
    command_id: Identity
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget: SelectedScheduledMandateBudgetV1
    expected_revocation_absent: Absent
    administrative_source: MandateAdministrativeSourceV1
    reason: Identity
    reference: Identity
    expected_authority_head: CallSubjectHead

    @model_validator(mode="after")
    def revoke_preserves_selected_old_lineage(self) -> RevokeScheduledMandateV1:
        if self.selected_budget.budget.mandate_id != self.selected_mandate.mandate.scope.mandate_id:
            raise ValueError("revocation selected budget differs from selected mandate")
        if self.administrative_source.tenant_id != self.selected_mandate.mandate.scope.tenant_id:
            raise ValueError("revocation source tenant differs from selected mandate")
        if self.expected_authority_head != self.administrative_source.current_authority_head:
            raise ValueError("revocation expected authority head differs from source")
        _require_selected_mandate_bytes(self.selected_mandate)
        _require_selected_budget_bytes(self.selected_budget)
        return self


class PreparedMandateRevocationV1(SchedulerExecutionDTO):
    kind: Literal["PREPARED_MANDATE_REVOCATION_V1"] = "PREPARED_MANDATE_REVOCATION_V1"
    source_request_fingerprint: Digest
    selected_mandate: SelectedScheduledSystemMandateV2
    selected_budget: SelectedScheduledMandateBudgetV1
    terminal_revocation: ProposedScheduledMandateRevocationV1

    @model_validator(mode="after")
    def terminal_record_preserves_old_selected_heads(self) -> PreparedMandateRevocationV1:
        revocation = self.terminal_revocation.revocation
        if (
            self.selected_budget.budget.mandate_id != self.selected_mandate.mandate.scope.mandate_id
            or
            revocation.mandate_id != self.selected_mandate.mandate.scope.mandate_id
            or revocation.mandate_head != self.selected_mandate.mandate_head
            or not isinstance(revocation.predecessor, Absent)
        ):
            raise ValueError(
                "terminal revocation must retain selected mandate and absent predecessor"
            )
        _require_selected_mandate_bytes(self.selected_mandate)
        _require_selected_budget_bytes(self.selected_budget)
        return self


class SupersedeScheduledMandateV1(SchedulerExecutionDTO):
    kind: Literal["SUPERSEDE_SCHEDULED_MANDATE_V1"] = "SUPERSEDE_SCHEDULED_MANDATE_V1"
    command_id: Identity
    old_selected_mandate: SelectedScheduledSystemMandateV2
    old_selected_budget: SelectedScheduledMandateBudgetV1
    old_expected_revocation_absent: Absent
    terminal_revocation: ProposedScheduledMandateRevocationV1
    new_issue: IssueScheduledMandateV1
    administrative_source: MandateAdministrativeSourceV1
    expected_authority_head: CallSubjectHead

    @model_validator(mode="after")
    def supersession_closes_old_and_issues_distinct_new_lineage(
        self,
    ) -> SupersedeScheduledMandateV1:
        old_scope = self.old_selected_mandate.mandate.scope
        new_scope = self.new_issue.proposed_mandate.mandate.scope
        revocation = self.terminal_revocation.revocation
        if self.old_selected_budget.budget.mandate_id != old_scope.mandate_id:
            raise ValueError("supersession old budget differs from old mandate")
        _require_selected_mandate_bytes(self.old_selected_mandate)
        _require_selected_budget_bytes(self.old_selected_budget)
        closes_exact_old = (
            revocation.mandate_id == old_scope.mandate_id
            and revocation.mandate_head == self.old_selected_mandate.mandate_head
        )
        if not closes_exact_old or not isinstance(revocation.predecessor, Absent):
            raise ValueError("supersession revocation must close the exact old mandate")
        if self.new_issue.mandate_id == old_scope.mandate_id:
            raise ValueError("supersession must issue a distinct mandate ID")
        old_rest = old_scope.model_dump(exclude={"mandate_id", "bound_head", "issuance_generation"})
        new_rest = new_scope.model_dump(exclude={"mandate_id", "bound_head", "issuance_generation"})
        if old_rest != new_rest:
            raise ValueError("supersession may replace only ID, generation, and successor bound")
        if new_scope.issuance_generation <= old_scope.issuance_generation:
            raise ValueError("supersession mandate generation must advance")
        if self.administrative_source != self.new_issue.administrative_source:
            raise ValueError("supersession must use its exact new-issuance administrative source")
        if self.expected_authority_head != self.administrative_source.current_authority_head:
            raise ValueError("supersession expected authority head differs from source")
        return self


class PreparedMandateSupersessionV1(SchedulerExecutionDTO):
    kind: Literal["PREPARED_MANDATE_SUPERSESSION_V1"] = "PREPARED_MANDATE_SUPERSESSION_V1"
    source_request_fingerprint: Digest
    old_selected_mandate: SelectedScheduledSystemMandateV2
    old_selected_budget: SelectedScheduledMandateBudgetV1
    terminal_revocation: ProposedScheduledMandateRevocationV1
    new_issuance: PreparedMandateIssuanceV1

    @model_validator(mode="after")
    def result_keeps_old_and_new_lineages_separate(self) -> PreparedMandateSupersessionV1:
        old_id = self.old_selected_mandate.mandate.scope.mandate_id
        if (
            self.old_selected_budget.budget.mandate_id != old_id
            or self.terminal_revocation.revocation.mandate_id != old_id
            or self.terminal_revocation.revocation.mandate_head
            != self.old_selected_mandate.mandate_head
            or not isinstance(self.terminal_revocation.revocation.predecessor, Absent)
            or self.new_issuance.mandate.mandate.scope.mandate_id == old_id
        ):
            raise ValueError("supersession must retain old lineage and issue a distinct genesis")
        _require_selected_mandate_bytes(self.old_selected_mandate)
        _require_selected_budget_bytes(self.old_selected_budget)
        return self


class ScheduledMandateLifecycleRejectedV1(SchedulerExecutionDTO):
    kind: Literal["SCHEDULED_MANDATE_LIFECYCLE_REJECTED_V1"] = (
        "SCHEDULED_MANDATE_LIFECYCLE_REJECTED_V1"
    )
    command_id: Identity
    code: Literal["CONFLICT", "DENIED", "INTEGRITY_FAULT", "REVOKED", "EXPIRED", "STALE"]
    reason: Identity


IssueScheduledMandateResultV1 = Annotated[
    PreparedMandateIssuanceV1 | ScheduledMandateLifecycleRejectedV1, Field(discriminator="kind")
]
RevokeScheduledMandateResultV1 = Annotated[
    PreparedMandateRevocationV1 | ScheduledMandateLifecycleRejectedV1, Field(discriminator="kind")
]
SupersedeScheduledMandateResultV1 = Annotated[
    PreparedMandateSupersessionV1 | ScheduledMandateLifecycleRejectedV1,
    Field(discriminator="kind"),
]
ScheduledMandateLifecycleRequestV1 = Annotated[
    IssueScheduledMandateV1 | RevokeScheduledMandateV1 | SupersedeScheduledMandateV1,
    Field(discriminator="kind"),
]


def mandate_admission_is_fresh(
    mandate: SelectedScheduledSystemMandateV2, revocation: Absent, now_unix_ns: UInt64
) -> bool:
    """Pure admission predicate; broker still verifies clock and selected absence."""

    return isinstance(revocation, Absent) and mandate_is_fresh_at(
        mandate.mandate.scope.horizon, now_unix_ns
    )


class ScheduledMandateLifecyclePreparationPort(Protocol):
    async def issue_scheduled_mandate(
        self, request: IssueScheduledMandateV1
    ) -> IssueScheduledMandateResultV1: ...

    async def revoke_scheduled_mandate(
        self, request: RevokeScheduledMandateV1
    ) -> RevokeScheduledMandateResultV1: ...

    async def supersede_scheduled_mandate(
        self, request: SupersedeScheduledMandateV1
    ) -> SupersedeScheduledMandateResultV1: ...
