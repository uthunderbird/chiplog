"""Explicit one-hold operator adoption; values alone grant no publication authority."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from chiplog.capabilities.agent_loop.recovery_contracts import Identity, Present
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    MissedOccurrencePolicyHead,
    ScheduleDefinitionHead,
    SchedulerEligibilityBoundary,
    SchedulerIntervalBoundHead,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import IntervalPreparationRequest
from chiplog.composition.r15_tick_contracts import TickDTO, TickSourceCut
from chiplog.platform._owner_publication_contracts import BrokerPublicationResult


class ResolutionPolicyDraft(TickDTO):
    adoption_act_id: Identity
    schedule_id: Identity


class ResolutionPolicy(TickDTO):
    schema_id: Literal["chiplog.scheduler.hermetic-resolution-policy.v1"] = (
        "chiplog.scheduler.hermetic-resolution-policy.v1"
    )
    operation: Literal["scheduler.resolve_interval"] = "scheduler.resolve_interval"
    scope: Literal["ONE_ACTIVE_HOLD_OPERATOR_RESOLUTION_NO_EXECUTION_NO_SEND"] = (
        "ONE_ACTIVE_HOLD_OPERATOR_RESOLUTION_NO_EXECUTION_NO_SEND"
    )
    tenant_id: Literal["hermetic-tenant"] = "hermetic-tenant"
    principal_id: Literal["hermetic-principal"] = "hermetic-principal"
    registered_ingress: Literal["hermetic-ingress"] = "hermetic-ingress"


class ResolutionPolicyPreview(TickDTO):
    schema_id: Literal["chiplog.scheduler.resolution-policy-adoption.v1"] = (
        "chiplog.scheduler.resolution-policy-adoption.v1"
    )
    adoption_act_id: Identity
    schedule: ScheduleDefinitionHead
    missed_policy: MissedOccurrencePolicyHead
    successor_bound: SchedulerIntervalBoundHead
    active_hold: Present
    held_boundary: SchedulerEligibilityBoundary
    policy: ResolutionPolicy


class ResolutionPolicyAdoption(TickDTO):
    adoption_act_id: Identity
    policy_bytes: bytes = Field(min_length=1)


class ResolutionIssuanceEvidence(TickDTO):
    schema_id: Literal["chiplog.scheduler.resolution-issuance.v1"] = (
        "chiplog.scheduler.resolution-issuance.v1"
    )
    adoption: ResolutionPolicyAdoption
    trust_preimages: bytes
    generation_preimages: bytes
    source_cut: TickSourceCut
    issuance_nonce: Identity
    preparation: IntervalPreparationRequest
    owner_request_frame: bytes
    owner_response_frame: bytes


class SchedulerResolutionPort(Protocol):
    async def preview_scheduler_resolution_policy(
        self, peer: str, draft: ResolutionPolicyDraft
    ) -> ResolutionPolicyPreview: ...

    async def adopt_scheduler_resolution(
        self, peer: str, adoption: ResolutionPolicyAdoption
    ) -> BrokerPublicationResult: ...
