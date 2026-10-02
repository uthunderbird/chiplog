"""Canonical adoption preparation with simulated trusted sources, no publication claim."""

import hashlib

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerContextRef
from chiplog.composition.r15_lease_acquire import EXECUTION_SERVICE, MAX_LEASE_NS
from chiplog.composition.r15_lease_policy import (
    LeasePolicyAdminSource,
    LeasePolicyAdoptionIssuer,
    LeasePolicyAdoptionRecord,
    LeasePolicyAdoptionRequest,
    LeasePolicyDocument,
    policy_for_execution,
    validate_historical_lease_policy,
)
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock
from chiplog.composition.r15_tick_contracts import TickSourceCut
from tests.support.scheduler_mandate_lifecycle import selected_mandate

DIGEST = "a" * 64


class Port:
    def __init__(self, clock: EvaluationSchedulerClock) -> None:
        original = selected_mandate()
        scope = original.mandate.scope.model_copy(
            update={
                "tenant_id": "hermetic-tenant",
                "service_identity": EXECUTION_SERVICE,
                "horizon": original.mandate.scope.horizon.model_copy(
                    update={"expires_at_unix_ns": 2 * MAX_LEASE_NS}
                ),
            }
        )
        body = original.mandate.model_copy(update={"scope": scope})
        raw = body.canonical_bytes()
        self.mandate = original.model_copy(
            update={
                "mandate": body,
                "canonical_mandate_bytes": raw,
                "mandate_head": Present(
                    head="mandate/head", fingerprint=hashlib.sha256(raw).hexdigest()
                ),
            }
        )
        self.policy = LeasePolicyDocument(
            execution_session_id="execution-session",
            selected_mandate=self.mandate.mandate_head,
            clock_source=clock.source,
            max_lease_ns=MAX_LEASE_NS,
        )
        self.request = LeasePolicyAdoptionRequest(
            adoption_act_id="explicit-distinct-lease-adoption",
            policy_bytes=self.policy.canonical_bytes(),
        )
        self.source = LeasePolicyAdminSource(
            principal_id="hermetic-principal",
            admin_session_id="hermetic-session",
            administrative_policy=Present(head="distinct-admin-lease-policy", fingerprint=DIGEST),
            authority_head=Present(head="admin-authority", fingerprint=DIGEST),
            trust_observation_bytes=b"observed-admin-trust",
            trust_request_frame=b"admin-auth-request",
            trust_response_frame=b"admin-auth-response",
            request_fingerprint=hashlib.sha256(self.request.canonical_bytes()).hexdigest(),
            mandate=self.mandate,
            revocation=Absent(),
            cut=TickSourceCut(
                tenant_id="hermetic-tenant",
                database_id="db",
                database_path="/db",
                database_device=1,
                database_inode=2,
                tenant_frontier=3,
                materialization_commitment=DIGEST,
                independent_journal_head=DIGEST,
                deletion_fence_generation="epoch",
                deletion_fence_frontier=0,
            ),
        )

    def capture(self, peer: str, request: LeasePolicyAdoptionRequest) -> LeasePolicyAdminSource:
        if peer != "authenticated-admin" or request != self.request:
            raise LoopRejected("authenticated administrative adoption denied")
        return self.source

    def context(self) -> SchedulerContextRef:
        return SchedulerContextRef(
            tenant_id="hermetic-tenant",
            service_identity=EXECUTION_SERVICE,
            session_id="execution-session",
            mandate_head=self.mandate.mandate_head.head,
            issuance_id="execution-auth",
            issuance_fingerprint=DIGEST,
        )


def test_explicit_adoption_prepares_rechecks_and_projects_exact_selected_bytes() -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    issuer = LeasePolicyAdoptionIssuer(clock, port)
    prepared = issuer.prepare("authenticated-admin", port.request)
    issuer.recheck(prepared)
    record = validate_historical_lease_policy(prepared)
    assert record.request == port.request
    assert record.policy == port.policy
    reference = Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
    projection = policy_for_execution(prepared, reference, port.context(), port.mandate)
    assert projection.adoption == reference
    assert projection.max_lease_ns == MAX_LEASE_NS
    # Exact historical interpretation never obtains a fresh reading.
    clock.unix_ns = 2 * MAX_LEASE_NS
    assert validate_historical_lease_policy(prepared) == record
    assert policy_for_execution(prepared, reference, port.context(), port.mandate) == projection
    with pytest.raises(LoopRejected, match="expired"):
        issuer.recheck(prepared)


def test_default_and_unauthed_administrative_source_deny() -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    with pytest.raises(LoopRejected, match="not mounted"):
        LeasePolicyAdoptionIssuer(clock).prepare("authenticated-admin", port.request)
    with pytest.raises(LoopRejected, match="denied"):
        LeasePolicyAdoptionIssuer(clock, port).prepare("execution-peer", port.request)


@pytest.mark.parametrize(
    "changes",
    [
        {"principal_id": "rival"},
        {"admin_session_id": "execution-session"},
        {"request_fingerprint": "b" * 64},
    ],
)
def test_wrong_admin_and_request_source_joins_deny(changes: dict[str, str]) -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    port.source = port.source.model_copy(update=changes)
    with pytest.raises(LoopRejected, match="join"):
        LeasePolicyAdoptionIssuer(clock, port).prepare("authenticated-admin", port.request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", "rival-session"),
        ("service_identity", "r15-hermetic-one-tick-v1"),
        ("mandate_head", "rival-mandate"),
    ],
)
def test_selected_adoption_projection_binds_execution_caller(field: str, value: str) -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    prepared = LeasePolicyAdoptionIssuer(clock, port).prepare("authenticated-admin", port.request)
    reference = Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
    with pytest.raises(LoopRejected, match="applicability"):
        policy_for_execution(
            prepared, reference, port.context().model_copy(update={field: value}), port.mandate
        )
    with pytest.raises(LoopRejected, match="adoption"):
        policy_for_execution(
            prepared, Present(head="asserted", fingerprint=DIGEST), port.context(), port.mandate
        )


def test_source_cut_retention_clock_and_original_record_tampering_deny() -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    issuer = LeasePolicyAdoptionIssuer(clock, port)
    prepared = issuer.prepare("authenticated-admin", port.request)
    with pytest.raises(LoopRejected, match="retained"):
        LeasePolicyAdoptionIssuer(clock, port).recheck(prepared)
    port.source = port.source.model_copy(
        update={"cut": port.source.cut.model_copy(update={"tenant_frontier": 4})}
    )
    with pytest.raises(LoopRejected, match="cut"):
        issuer.recheck(prepared)
    record = LeasePolicyAdoptionRecord.model_validate_json(prepared.canonical_record_bytes)
    changed = record.model_copy(
        update={"reading": record.reading.model_copy(update={"unix_ns": 101})}
    )
    with pytest.raises(LoopRejected, match="bytes differ"):
        validate_historical_lease_policy(
            prepared.model_copy(update={"canonical_record_bytes": changed.canonical_bytes()})
        )
    with pytest.raises(LoopRejected, match="identity"):
        validate_historical_lease_policy(prepared.model_copy(update={"record_id": "forged"}))
    port.source = record.source
    clock.unix_ns = 99
    with pytest.raises(LoopRejected, match="regressed"):
        issuer.recheck(prepared)


def test_closed_policy_rejects_sixty_second_overflow_tick_session_and_noncanonical_bytes() -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    with pytest.raises(ValidationError):
        LeasePolicyDocument.model_validate_json(
            port.policy.model_copy(update={"max_lease_ns": MAX_LEASE_NS + 1}).canonical_bytes()
        )
    request = port.request.model_copy(
        update={
            "policy_bytes": port.policy.model_copy(
                update={"execution_session_id": "hermetic-session"}
            ).canonical_bytes()
        }
    )
    with pytest.raises(LoopRejected, match="distinct"):
        LeasePolicyAdoptionIssuer(clock, port).prepare("authenticated-admin", request)
    with pytest.raises(LoopRejected, match="canonical"):
        LeasePolicyAdoptionIssuer(clock, port).prepare(
            "authenticated-admin",
            port.request.model_copy(update={"policy_bytes": b" " + port.request.policy_bytes}),
        )
