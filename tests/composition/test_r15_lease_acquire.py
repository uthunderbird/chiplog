"""Private issuer tests with a simulated trusted assembly port; no publication claim."""

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    LeaseTransitionCommand,
    SchedulerContextRef,
)
from chiplog.composition.r15_lease_acquire import (
    EXECUTION_SERVICE,
    MAX_LEASE_NS,
    LeaseAcquireIssuer,
    LeaseAcquireSource,
    LeaseExecutionPolicy,
    validate_historical_acquire,
)
from chiplog.composition.r15_tick_clock_v1 import EvaluationSchedulerClock
from chiplog.composition.r15_tick_contracts import TickSourceCut
from tests.capabilities.agent_loop.support import DIGEST, view
from tests.support.scheduler_mandate_lifecycle import selected_mandate


class Port:
    def __init__(self, clock: EvaluationSchedulerClock) -> None:
        original = selected_mandate()
        scope = original.mandate.scope.model_copy(
            update={
                "service_identity": EXECUTION_SERVICE,
                "horizon": original.mandate.scope.horizon.model_copy(
                    update={"expires_at_unix_ns": 2 * MAX_LEASE_NS}
                ),
            }
        )
        body = original.mandate.model_copy(update={"scope": scope})
        import hashlib

        raw = body.canonical_bytes()
        mandate = original.model_copy(
            update={
                "mandate": body,
                "canonical_mandate_bytes": raw,
                "mandate_head": Present(
                    head="mandate/head", fingerprint=hashlib.sha256(raw).hexdigest()
                ),
            }
        )
        self.source = LeaseAcquireSource(
            context=SchedulerContextRef(
                tenant_id=scope.tenant_id,
                service_identity=EXECUTION_SERVICE,
                session_id="execution-session",
                mandate_head="mandate/head",
                issuance_id="auth",
                issuance_fingerprint=DIGEST,
            ),
            policy=LeaseExecutionPolicy(
                adoption=Present(head="explicit-admin-adoption", fingerprint=DIGEST),
                clock_source=clock.source,
                max_lease_ns=MAX_LEASE_NS,
            ),
            mandate=mandate,
            revocation=Absent(),
            current=view(),
            registry=Present(head="registry", fingerprint=DIGEST),
            used_lease_ids=(),
            authentication_frames=(b"execution-auth-frame",),
            selected_root_frames=(b"selected-root-join",),
            cut=TickSourceCut(
                tenant_id=scope.tenant_id,
                database_id="database",
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

    def capture(self, peer: str, root_id: str) -> LeaseAcquireSource:
        if peer != "authenticated-execution-peer" or root_id != "root":
            raise LoopRejected("execution caller denied")
        return self.source


def test_default_denies_and_valid_private_issue_rechecks_and_replays_exact_bytes() -> None:
    clock = EvaluationSchedulerClock(100)
    with pytest.raises(LoopRejected, match="not mounted"):
        LeaseAcquireIssuer(clock).prepare("peer", "root", "command")
    port = Port(clock)
    issuer = LeaseAcquireIssuer(clock, port)
    evidence = issuer.prepare("authenticated-execution-peer", "root", "command")
    issuer.recheck(evidence)
    command = LeaseTransitionCommand.model_validate_json(evidence.request.command_bytes)
    assert command.proposed_generation == 1
    assert command.proposed_expiry == 100 + MAX_LEASE_NS
    assert command.proposed_holder_id == EXECUTION_SERVICE
    assert validate_historical_acquire(evidence.canonical_bytes()) == evidence
    # Historical validation does not consult the changed live clock.
    clock.unix_ns = command.proposed_expiry
    assert validate_historical_acquire(evidence.canonical_bytes()) == evidence
    with pytest.raises(LoopRejected, match="expired"):
        issuer.recheck(evidence)


@pytest.mark.parametrize("duration", [0, -1, MAX_LEASE_NS + 1, True])
def test_invalid_duration_denies(duration: int) -> None:
    clock = EvaluationSchedulerClock(100)
    with pytest.raises(LoopRejected, match="sixty"):
        LeaseAcquireIssuer(clock, Port(clock)).prepare(
            "authenticated-execution-peer", "root", "c", duration
        )


def test_distinct_identity_authentication_cut_and_private_retention() -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    issuer = LeaseAcquireIssuer(clock, port)
    with pytest.raises(LoopRejected, match="caller denied"):
        issuer.prepare("tick-peer", "root", "command")
    evidence = issuer.prepare("authenticated-execution-peer", "root", "command")
    with pytest.raises(LoopRejected, match="retained"):
        LeaseAcquireIssuer(clock, port).recheck(evidence)
    port.source = port.source.model_copy(
        update={"cut": port.source.cut.model_copy(update={"tenant_frontier": 4})}
    )
    with pytest.raises(LoopRejected, match="cut"):
        issuer.recheck(evidence)
    port.source = evidence.source.model_copy(
        update={
            "context": evidence.source.context.model_copy(
                update={"service_identity": "r15-hermetic-one-tick-v1"}
            )
        }
    )
    with pytest.raises(LoopRejected, match="identity"):
        issuer.prepare("authenticated-execution-peer", "root", "command")


@pytest.mark.parametrize("field", ["reading", "source", "request", "prepared"])
def test_historical_tampered_original_evidence_fails(field: str) -> None:
    clock = EvaluationSchedulerClock(100)
    evidence = LeaseAcquireIssuer(clock, Port(clock)).prepare(
        "authenticated-execution-peer", "root", "command"
    )
    changes = {
        "reading": evidence.reading.model_copy(update={"unix_ns": 101}),
        "source": evidence.source.model_copy(update={"used_lease_ids": ("another-id",)}),
        "request": evidence.request.model_copy(
            update={
                "snapshot": evidence.request.snapshot.model_copy(update={"submission_id": "rival"})
            }
        ),
        "prepared": evidence.prepared.model_copy(update={"fingerprint": "b" * 64}),
    }
    with pytest.raises(LoopRejected, match="differs"):
        validate_historical_acquire(
            evidence.model_copy(update={field: changes[field]}).canonical_bytes()
        )


def test_short_lease_horizon_clock_regression_and_replay_new_generation_reject() -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    issuer = LeaseAcquireIssuer(clock, port)
    evidence = issuer.prepare("authenticated-execution-peer", "root", "command", 1)
    clock.unix_ns = 99
    with pytest.raises(LoopRejected, match="regressed"):
        issuer.recheck(evidence)
    clock.unix_ns = 101
    with pytest.raises(LoopRejected, match="expired"):
        issuer.recheck(evidence)
    clock.unix_ns = 100
    port.source = port.source.model_copy(update={"current": view(1)})
    with pytest.raises(LoopRejected, match="genesis"):
        issuer.prepare("authenticated-execution-peer", "root", "new-command")


@pytest.mark.parametrize(
    "field,value", [("session_id", "hermetic-session"), ("mandate_head", "rival")]
)
def test_tick_session_and_other_selected_mandate_head_deny(field: str, value: str) -> None:
    clock = EvaluationSchedulerClock(100)
    port = Port(clock)
    port.source = port.source.model_copy(
        update={"context": port.source.context.model_copy(update={field: value})}
    )
    with pytest.raises(LoopRejected, match="identity"):
        LeaseAcquireIssuer(clock, port).prepare("authenticated-execution-peer", "root", "command")
