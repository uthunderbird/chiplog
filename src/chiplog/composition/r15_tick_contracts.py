"""Inert one-delivery tick contracts; no construction grants publication authority.

The broker retains TickIssuanceEvidence in WorkerAuthentication.applicability_bytes.
Its independent selected journal, not these caller-constructible values, supplies
historical provenance. Fresh issuance remains private to the runtime.
"""

from typing import Literal, Protocol

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Digest,
    Identity,
    RecoveryDTO,
    UInt64,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    MissedOccurrencePolicyHead,
    ScheduleDefinitionHead,
    SchedulerIntervalBoundHead,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import IntervalPreparationRequest
from chiplog.platform._owner_publication_contracts import BrokerPublicationResult


class TickDTO(RecoveryDTO):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class TickClockSource(TickDTO):
    source_id: Identity
    contract_version: Literal["chiplog.scheduler.coordinate-clock.v1"]
    coordinate_codec: Literal["chiplog.scheduler.unix-ns.v1"]
    deployment_profile: Identity
    implementation_fingerprint: Digest


class TickClockReading(TickDTO):
    """Read together at the registered composition leaf, never supplied by tick callers."""

    unix_ns: UInt64
    monotonic_ns: UInt64


class TickClockPort(Protocol):
    """Composition-owned source; identical injection seam for production and eval.

    Unavailable/backward readings deny fresh publication. Runtime identity checks
    and source registration are required; a matching description alone is no proof.
    """

    @property
    def source(self) -> TickClockSource: ...

    def observe(self) -> TickClockReading: ...


class TickPolicyDraft(TickDTO):
    adoption_act_id: Identity
    schedule_id: Identity
    delivery_id: Identity


class TickPolicy(TickDTO):
    schema_id: Literal["chiplog.scheduler.hermetic-tick-policy.v1"]
    operation: Literal["scheduler.decide_interval"]
    scope: Literal["ONE_DELIVERY_PRE_ROOT_MATERIALIZATION_NO_EXECUTION_NO_SEND"]
    tenant_id: Identity
    principal_id: Identity
    service_identity: Identity
    registered_ingress: Literal["hermetic-ingress"]
    clock_source: TickClockSource


class TickPolicyPreview(TickDTO):
    """Exact policy preview; carries no observed now and grants no authority."""

    schema_id: Literal["chiplog.scheduler.tick-policy-adoption.v1"]
    adoption_act_id: Identity
    delivery_id: Identity
    schedule: ScheduleDefinitionHead
    missed_policy: MissedOccurrencePolicyHead
    bound: SchedulerIntervalBoundHead
    policy: TickPolicy


class TickPolicyAdoption(TickDTO):
    """Exact untrusted preview bytes; acceptance authorizes only their one delivery."""

    adoption_act_id: Identity
    policy_bytes: bytes = Field(min_length=1)


class SchedulerTickPort(Protocol):
    """Public consumer boundary, never an issuer/clock/owner-preparation API.

    Invalid scope or malformed bytes raise LoopRejected. Publication races return
    BrokerPublicationResult refusals; durable corruption retains typed integrity
    errors. Replay authenticates today's caller and uses original selected bytes.
    """

    async def preview_scheduler_tick_policy(
        self, peer: str, draft: TickPolicyDraft
    ) -> TickPolicyPreview: ...

    async def adopt_scheduler_tick(
        self, peer: str, adoption: TickPolicyAdoption
    ) -> BrokerPublicationResult: ...


class TickClockObservation(TickDTO):
    source: TickClockSource
    observation_id: Identity
    broker_epoch: Identity
    broker_session: Identity
    reading: TickClockReading
    valid_until_monotonic_ns: UInt64


class TickSourceCut(TickDTO):
    tenant_id: Identity
    database_id: Identity
    database_path: Identity
    database_device: int = Field(ge=0)
    database_inode: int = Field(ge=0)
    tenant_frontier: UInt64
    materialization_commitment: Digest
    independent_journal_head: Digest
    deletion_fence_generation: Identity
    deletion_fence_frontier: UInt64


class TickIssuanceEvidence(TickDTO):
    """Journal-retained original provenance, not a public fresh authority token.

    Byte fields preserve actual frames/preimages losslessly. Historical verifier
    must parse each under its closed registered schema and compare cross-links,
    clock/cutoff, context/hash domains and complete original owner output before
    read/replay/pending materialization. Shape validation is intentionally not that
    verifier. Fresh writer additionally checks private issuance and current cut.
    """

    schema_id: Literal["chiplog.scheduler.tick-issuance.v1"]
    adoption: TickPolicyAdoption
    registered_policy: TickPolicy
    trust_request_frame: bytes = Field(min_length=1)
    trust_response_frame: bytes = Field(min_length=1)
    trust_observation_bytes: bytes = Field(min_length=1)
    generation_preimages: bytes = Field(min_length=1)
    source_cut: TickSourceCut
    clock: TickClockObservation
    issuance_nonce: Identity
    preparation: IntervalPreparationRequest
    owner_request_frame: bytes = Field(min_length=1)
    owner_response_frame: bytes = Field(min_length=1)
