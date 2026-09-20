"""Lease candidate algebra with simulated issuance; no durable clock/CAS evidence."""

import pytest
from tests.capabilities.agent_loop.support import issue, view

from chiplog.capabilities.agent_loop.recovery_contracts import (
    IndividualSubject,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    GenesisLease,
    HeldLease,
)
from chiplog.capabilities.agent_loop.scheduler_leases import (
    UINT64_MAX,
    LeaseRuleViolation,
    evaluate_lease,
)


def test_acquire_derives_stable_candidate_without_mutating_genesis() -> None:
    current = view()
    command, issued = issue(current, "ACQUIRE")
    result = evaluate_lease(current, command, issued, submission_id="submission")
    assert result == evaluate_lease(current, command, issued, submission_id="submission")
    assert result.state.kind == "HELD" and result.state.binding.generation == 1
    assert current.lease.kind == "UNLEASED"
    assert current.lineage.current_run_id == "run"


@pytest.mark.parametrize("now,allowed", [(99, True), (100, False), (101, False)])
def test_renew_requires_live_exact_holder_and_later_bounded_expiry(now: int, allowed: bool) -> None:
    current = view(7)
    command, issued = issue(current, "RENEW", generation=7, now=now, lease_id="lease")
    if allowed:
        result = evaluate_lease(current, command, issued, submission_id="submission")
        assert result.state.kind == "HELD"
        assert result.state.binding.generation == 7
        assert result.state.binding.trusted_expiry == 120
    else:
        with pytest.raises(LeaseRuleViolation, match="renew holder"):
            evaluate_lease(current, command, issued, submission_id="submission")


@pytest.mark.parametrize("now,allowed", [(99, False), (100, True), (101, True)])
def test_takeover_requires_positive_expiry_and_increments_once(now: int, allowed: bool) -> None:
    current = view(7)
    command, issued = issue(current, "TAKEOVER", generation=8, now=now, holder="new-worker")
    if allowed:
        result = evaluate_lease(current, command, issued, submission_id="submission")
        assert result.state.kind == "HELD" and result.state.binding.generation == 8
        assert current.lineage.current_run_id == "run"
    else:
        with pytest.raises(LeaseRuleViolation, match="positive expiry"):
            evaluate_lease(current, command, issued, submission_id="submission")


def test_takeover_at_maximum_produces_exhaustion_without_wrap_or_run_allocation() -> None:
    current = view(UINT64_MAX)
    command, issued = issue(current, "TAKEOVER", generation=UINT64_MAX, now=100)
    result = evaluate_lease(current, command, issued, submission_id="submission")
    assert result.state.kind == "GENERATION_EXHAUSTED_HOLD"
    assert result.state.generation == UINT64_MAX
    assert result.state.preceding_held_lease == current.lease.binding  # type: ignore[union-attr]
    assert current.lineage == command.lineage
    reset, reset_proof = issue(current, "TAKEOVER", generation=0, now=100)
    with pytest.raises(LeaseRuleViolation, match="reset"):
        evaluate_lease(current, reset, reset_proof, submission_id="submission")


def test_missing_stale_forged_or_wrong_submission_proof_never_yields_candidate() -> None:
    current = view()
    command, issued = issue(current, "ACQUIRE")
    for observation, submission in ((None, "submission"), (issued, "another-cut")):
        with pytest.raises(LeaseRuleViolation, match="exact issued"):
            evaluate_lease(current, command, observation, submission_id=submission)
    for field, replacement in (
        ("command_id", "rival"),
        ("fence_fingerprint", "b" * 64),
        ("command_payload_fingerprint", "b" * 64),
    ):
        proof = command.clock_proof.model_copy(update={field: replacement})
        changed = command.model_copy(update={"clock_proof": proof})
        changed_issued = issued.model_copy(update={"proof": proof})
        with pytest.raises(LeaseRuleViolation, match="exact issued"):
            evaluate_lease(current, changed, changed_issued, submission_id="submission")


def test_every_changed_lineage_selector_epoch_or_lease_makes_snapshot_stale() -> None:
    current = view(7)
    command, issued = issue(current, "RENEW", generation=7, lease_id="lease")
    changes: list[dict[str, object]] = [{"lease": GenesisLease(lease_head="new")}]
    for name in type(current.lineage).model_fields:
        replacement: object = (
            IndividualSubject(occurrence_id="other") if name == "subject" else "new"
        )
        changes.append({"lineage": current.lineage.model_copy(update={name: replacement})})
    for name in type(current.physical_root).model_fields:
        replacement = 1 if name == "selector_version" else "new"
        changes.append(
            {
                "physical_root": current.physical_root.model_copy(
                    update={name: replacement},
                )
            }
        )
    assert current.lease.kind == "HELD"
    for name in type(current.lease.binding).model_fields:
        old = getattr(current.lease.binding, name)
        replacement = old + 1 if isinstance(old, int) else "new"
        changes.append(
            {
                "lease": HeldLease(
                    binding=current.lease.binding.model_copy(
                        update={name: replacement},
                    )
                )
            }
        )
    for change in changes:
        with pytest.raises(LeaseRuleViolation, match="changed"):
            evaluate_lease(
                current.model_copy(update=change),
                command,
                issued,
                submission_id="submission",
            )


def test_wrong_holder_reused_id_generation_and_expiry_reject_after_exact_proof_match() -> None:
    current = view(7)
    for overrides in (
        {"generation": 8},
        {"holder": "rival"},
        {"session": "rival-session"},
        {"lease_id": "different"},
        {"expiry": 100},
        {"expiry": 151},
    ):
        parameters = {"generation": 7, "lease_id": "lease", **overrides}
        command, issued = issue(current, "RENEW", **parameters)  # type: ignore[arg-type]
        with pytest.raises(LeaseRuleViolation):
            evaluate_lease(current, command, issued, submission_id="submission")
    command, issued = issue(current, "TAKEOVER", generation=8, now=100, lease_id="lease")
    with pytest.raises(LeaseRuleViolation, match="not fresh"):
        evaluate_lease(current, command, issued, submission_id="submission")


def test_rival_renew_candidates_make_loser_stale_in_both_simulated_orders() -> None:
    current = view(7)
    pairs = tuple(
        issue(
            current,
            "RENEW",
            generation=7,
            lease_id="lease",
            command_id=f"command-{n}",
            expiry=120 + n,
        )
        for n in (0, 1)
    )
    for first, second in ((pairs[0], pairs[1]), (pairs[1], pairs[0])):
        winner = evaluate_lease(current, first[0], first[1], submission_id="submission")
        selected = current.model_copy(update={"lease": winner.state})
        with pytest.raises(LeaseRuleViolation, match="changed"):
            evaluate_lease(selected, second[0], second[1], submission_id="submission")


def test_penultimate_generation_reaches_maximum_then_expiry_requires_exhaustion() -> None:
    current = view(UINT64_MAX - 1)
    command, issued = issue(current, "TAKEOVER", generation=UINT64_MAX, now=100)
    result = evaluate_lease(current, command, issued, submission_id="submission")
    assert result.state.kind == "HELD" and result.state.binding.generation == UINT64_MAX
    assert result.state.binding.lease_id == "fresh-lease"


def test_authenticated_session_and_clock_contract_mismatches_are_independently_rejected() -> None:
    current = view(7)
    command, issued = issue(current, "RENEW", generation=7, lease_id="lease")
    with pytest.raises(LeaseRuleViolation, match="not authenticated"):
        evaluate_lease(
            current,
            command,
            issued.model_copy(
                update={
                    "holder_session_id": "another-authenticated-session",
                }
            ),
            submission_id="submission",
        )
    wrong_clock = command.clock_proof.model_copy(update={"clock_contract_version": "clock-v2"})
    with pytest.raises(LeaseRuleViolation, match="clock contract differs"):
        evaluate_lease(
            current,
            command.model_copy(update={"clock_proof": wrong_clock}),
            issued.model_copy(update={"proof": wrong_clock}),
            submission_id="submission",
        )
