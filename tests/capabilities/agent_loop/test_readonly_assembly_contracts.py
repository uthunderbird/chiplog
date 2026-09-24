"""Leaf tests for closed ordinary read-only assembly carrier shapes."""

import pytest
from tests.support.readonly_assembly import readonly_member, same_run_first_attempt_fixture

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.readonly_assembly_contracts import (
    ReadOnlyOutcomeAssemblyV1,
    SameRunReadOnlyAttemptAssemblyV1,
    SelectedReadOnlyCounterV1,
    SuccessorPendingReadOnlyAttemptAssemblyV1,
    validate_readonly_outcome,
    validate_same_run_readonly_attempt,
)
from chiplog.capabilities.agent_loop.readonly_execution_contracts import PreparedReadOnlyAttempt
from chiplog.capabilities.agent_loop.readonly_source_contracts import (
    readonly_physical_batch_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_record_contracts import RecoveryRecordIntegrityError


def _head(name: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=name,
        revision=Present(head=name + ":head", fingerprint="a" * 64),
    )


def test_closed_assembly_carriers_have_distinct_route_kinds() -> None:
    assert SameRunReadOnlyAttemptAssemblyV1.model_fields["kind"].default == (
        "SAME_RUN_READONLY_ATTEMPT_ASSEMBLY_V1"
    )
    assert SuccessorPendingReadOnlyAttemptAssemblyV1.model_fields["kind"].default == (
        "SUCCESSOR_PENDING_READONLY_ATTEMPT_ASSEMBLY_V1"
    )
    assert ReadOnlyOutcomeAssemblyV1.model_fields["kind"].default == "READONLY_OUTCOME_ASSEMBLY_V1"


def test_selected_counter_retains_an_opaque_selection_head() -> None:
    selected = SelectedReadOnlyCounterV1.model_validate(
        {
            "member": {
                "owner": "agent_loop",
                "record_kind": "READONLY_ATTEMPT_ACCEPTED",
                "schema_id": "chiplog.readonly.attempt-accepted.v1",
                "record_id": "chiplog.readonly.attempt-accepted.v1:" + "a" * 64,
                "canonical_record_bytes": b"{}",
                "fingerprint": "a" * 64,
            },
            "selected_decision": _head("opaque-selection"),
        }
    )
    assert selected.selected_decision.subject_id == "opaque-selection"


async def test_native_same_run_first_attempt_and_selected_outcome() -> None:
    fixture = await same_run_first_attempt_fixture()
    attempt = validate_same_run_readonly_attempt(fixture.attempt)
    outcome = validate_readonly_outcome(fixture.outcome)
    assert attempt.accepted is not None
    assert attempt.counter is not None
    assert outcome.outcome is not None
    assert outcome.evidence is not None


async def test_same_run_rejects_coherently_rebuilt_wrong_counter_predecessor() -> None:
    fixture = await same_run_first_attempt_fixture()
    assert isinstance(fixture.attempt.result, PreparedReadOnlyAttempt)
    counter = fixture.attempt.result.counter.model_copy(
        update={"predecessor": _head("foreign-counter")}
    )
    counter_member = readonly_member("READONLY_COUNTER", counter)
    members = (counter_member, fixture.attempt.members[1])
    result = fixture.attempt.result.model_copy(
        update={
            "counter": counter,
            "complete_batch_fingerprint": readonly_physical_batch_fingerprint(members),
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="predecessor"):
        validate_same_run_readonly_attempt(
            fixture.attempt.model_copy(update={"result": result, "members": members})
        )
