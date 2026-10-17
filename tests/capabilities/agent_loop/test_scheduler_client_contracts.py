"""Public input/transport checks; no publication, authority or lease claim."""

import base64
import hashlib

import pytest
from pydantic import ValidationError
from tests.support.scheduler_execution import origin
from tests.support.scheduler_overflow_hold_records import v1_overflow_hold_member

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, SchedulerRootReference
from chiplog.capabilities.agent_loop.execution_client_transitions import create
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.agent_loop.recovery_contracts import IndividualSubject, Present
from chiplog.capabilities.agent_loop.scheduler_client_contracts import (
    NativeOrdinaryInterval,
    NativeOverflowInterval,
    NativeOverflowResolutionDraft,
    NativeScheduledExecution,
    NativeScheduleDraft,
    NativeSchedulerAdoption,
    NativeSchedulerCycle,
    NativeSchedulerCycleResult,
    NativeSchedulerLeaseRequest,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    ScheduleDefinitionHead,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerLineageView,
    SchedulerOverflowHold,
)


def schedule() -> ScheduleDefinitionHead:
    return ScheduleDefinitionHead(
        schedule_id="daily", schedule_revision="0", head="definition:0", fingerprint="a" * 64
    )


def test_consumer_retains_exact_adoption_bytes_including_non_utf8() -> None:
    adoption = NativeSchedulerAdoption(act_id="adopt:1", canonical_preview=b"\xff\x00invalid")
    assert NativeSchedulerAdoption.model_validate_json(adoption.model_dump_json()) == adoption
    with pytest.raises(ValidationError):
        NativeSchedulerAdoption(act_id="adopt:1", canonical_preview=b"")


def test_finite_configuration_expands_explicit_policy_and_bounds() -> None:
    draft = NativeScheduleDraft(
        act_id="adopt:1",
        schedule_id="daily",
        start_ns=0,
        period_ns=10,
        end_exclusive_ns=100,
        prompt="Review",
        budget=BudgetPolicy(),
        missed_policy="COALESCE",
        bound=SchedulerIntervalBound(
            max_member_count=10, max_manifest_bytes=10000, max_serialized_batch_bytes=100000
        ),
    )
    assert NativeScheduleDraft.model_validate_json(draft.canonical_bytes()) == draft
    for field, value in (("period_ns", 0), ("end_exclusive_ns", "NO_END")):
        with pytest.raises(ValidationError):
            NativeScheduleDraft.model_validate({**draft.model_dump(), field: value})


@pytest.mark.parametrize("field", ["cutoff", "clock", "holder_id", "authority"])
def test_cycle_does_not_accept_caller_authority_or_observation(field: str) -> None:
    request = NativeSchedulerCycle(act_id="delivery:1", schedule=schedule())
    assert NativeSchedulerCycle.model_validate_json(request.canonical_bytes()) == request
    with pytest.raises(ValidationError):
        NativeSchedulerCycle.model_validate({**request.model_dump(), field: "claimed"})


@pytest.mark.parametrize("disposition", ["COMMITTED", "REPLAY"])
def test_empty_interval_retains_selected_boundary_without_run(disposition: str) -> None:
    selected = Present(head="interval:0", fingerprint="b" * 64)
    result = NativeSchedulerCycleResult.model_validate(
        {
            "disposition": disposition,
            "publication_sequence": 1,
            "result": NativeOrdinaryInterval(
                interval=selected,
                boundary=selected,
                manifest=SchedulerEligibilityManifest(members=(), fingerprint="c" * 64),
                members=(),
            ),
        }
    )
    assert NativeSchedulerCycleResult.model_validate_json(result.canonical_bytes()) == result
    with pytest.raises(ValidationError):
        NativeSchedulerCycleResult.model_validate(
            {**result.model_dump(), "disposition": "EXACT_REPLAY"}
        )


def test_native_v3_carries_existing_scheduler_root_and_real_subject_bytes() -> None:
    subject = IndividualSubject(occurrence_id="occurrence:1")
    raw = subject.canonical_bytes()
    root = SchedulerRootReference(
        root_id="root:1",
        subject_canonical_base64=base64.b64encode(raw).decode(),
        subject_schema_version="chiplog.execution-lineage-subject.v1",
        root_fingerprint=hashlib.sha256(raw).hexdigest(),
        initial_run_id="run:1",
    )
    ordinary = create(
        version="v3",
        tenant="t",
        principal="p",
        run_id="run:1",
        prompt="Review",
        policy=BudgetPolicy(),
        origin=origin(),
        worker="worker:1",
    )
    run = ExecutionRunRecordV3.model_validate({**ordinary.model_dump(), "root_binding": root})
    member = NativeScheduledExecution(
        run_id=run.run_id, initial_run=Present(head=run.head, fingerprint=run.digest()), root=root
    )
    assert ExecutionRunRecordV3.model_validate_json(run.canonical_bytes()).root_binding == root
    assert NativeScheduledExecution.model_validate_json(member.canonical_bytes()) == member


def test_overflow_retains_full_hold_and_resolution_adopts_exact_bound() -> None:
    hold = SchedulerOverflowHold.model_validate_json(
        base64.b64decode(v1_overflow_hold_member().canonical_base64)
    )
    result = NativeSchedulerCycleResult(
        disposition="COMMITTED", publication_sequence=1, result=NativeOverflowInterval(hold=hold)
    )
    draft = NativeOverflowResolutionDraft(
        act_id="resolve:1",
        schedule=hold.boundary.schedule_definition_head,
        hold=hold.hold,
        expected_bound=hold.bound_head,
        sufficient_bound=hold.bound_head.bound.model_copy(update={"max_member_count": 100}),
    )
    adoption = NativeSchedulerAdoption(
        act_id=draft.act_id, canonical_preview=draft.canonical_bytes()
    )
    assert NativeSchedulerCycleResult.model_validate_json(result.canonical_bytes()) == result
    assert NativeOverflowResolutionDraft.model_validate_json(adoption.canonical_preview) == draft


def test_lease_request_exposes_expected_cut_but_no_holder_clock_or_proof() -> None:
    assert set(NativeSchedulerLeaseRequest.model_fields) == {"act_id", "transition", "expected"}
    assert NativeSchedulerLeaseRequest.model_fields["expected"].annotation is SchedulerLineageView
