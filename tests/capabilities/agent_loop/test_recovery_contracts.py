"""Public consumer checks; these do not certify broker authentication or recovery."""

import base64
from copy import deepcopy
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.contracts import SchedulerRootReference
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    CoalescedSubject,
    ExecutionLineageSubject,
    HeadMarker,
    IndividualSubject,
    NotApplicable,
    RecoveryDTO,
    WorkerCommitApplicability,
)

DIGEST = "a" * 64


def _variants() -> list[dict[str, Any]]:
    present = {"kind": "PRESENT", "head": "head", "fingerprint": DIGEST}
    lineage = {
        "root_id": "lineage",
        "subject": {"kind": "INDIVIDUAL", "occurrence_id": "occurrence"},
        "root_fingerprint": DIGEST,
        "lineage_head": "lineage-head",
        "initial_run_id": "run0",
        "current_run_id": "run1",
        "schedule_id": "schedule",
        "schedule_revision": "schedule-revision",
        "policy_revision": "policy-revision",
    }
    epoch = {
        "selector_id": "selector",
        "selector_head": "selector-head",
        "selector_version": 1,
        "current_epoch_id": "epoch",
        "current_epoch_head": "epoch-head",
    }
    lease = {
        "lease_head": "lease-head",
        "holder_id": "holder",
        "holder_session_id": "session",
        "lease_id": "lease",
        "generation": 1,
        "trusted_expiry": 100,
        "clock_contract_version": "clock.v1",
    }
    proof = {
        "proof_id": "proof",
        "proof_fingerprint": DIGEST,
        "proof_version": "proof.v1",
        "clock_contract_version": "clock.v1",
        "fence_fingerprint": DIGEST,
        "command_id": "command",
        "command_payload_fingerprint": DIGEST,
        "submission_id": "submission",
    }
    work = {
        "work_id": "work",
        "work_subject_head": "work-head",
        "work_subject_fingerprint": DIGEST,
        "terminal_manifest_head": "terminal-manifest",
        "terminal_manifest_member_fingerprint": DIGEST,
        "original_obligation": {
            "original_run_id": "run0",
            "original_call_id": "call0",
            "obligation_id": "obligation",
            "obligation_stream_id": "original-stream",
            "obligation_head": "obligation-head",
            "closure_predicate_id": "closure",
            "closure_predicate_version": "closure.v1",
            "resolver_id": "resolver",
            "resolver_version": "resolver.v1",
            "reducer_id": "reducer",
            "reducer_version": "reducer.v1",
            "evidence_stream_id": "evidence",
            "evidence_head": present,
        },
    }
    exhaustion = {
        "hold_head": "hold",
        "exhausted_command_id": "takeover",
        "lease": {**lease, "generation": 2**64 - 1},
        "authority_epoch": "authority-epoch",
    }
    authority = {
        "proof_id": "operator-proof",
        "proof_fingerprint": DIGEST,
        "authority_head": "operator-authority",
        "command_id": "rollover-command",
        "command_payload_fingerprint": DIGEST,
        "predecessor_rollover": {"kind": "PRESENT", "decision": present, "edge": present},
    }
    return [
        {
            "kind": "NON_SCHEDULER_NOT_APPLICABLE",
            **{
                key: {"kind": "NOT_APPLICABLE"}
                for key in ("lineage", "physical_root", "lease", "clock_proof")
            },
            "run_id": "run",
            "run_head": "run-head",
            "worker_session_id": "worker",
            "runtime_generation": "runtime-generation",
        },
        {
            "kind": "EXECUTION_ROOT_LIVE_LEASE",
            "run_head": "run-head",
            "lineage": lineage,
            "physical_root": epoch,
            "lease": lease,
            "clock_proof": proof,
        },
        {
            "kind": "PRE_ROOT_SCHEDULER_DECISION",
            "command_id": "interval",
            "disposition": {
                "kind": "FIRST_PUBLICATION",
                "decision": {"kind": "ABSENT"},
                "expected_canonical_absence_manifest": "absence-manifest",
            },
            "scheduler_authority_head": "scheduler-authority",
            "broker_generation": "broker-generation",
            "runtime_generation": "runtime-generation",
        },
        {
            "kind": "POST_TERMINAL_RECOVERY_WORK",
            "subject": work,
            "work_epoch": epoch,
            "lease": lease,
            "clock_proof": proof,
            "work_command_id": "resolve",
        },
        {
            "kind": "PHYSICAL_ROOT_ROLLOVER",
            "lineage": lineage,
            "physical_root": epoch,
            "exhaustion": exhaustion,
            "authority": authority,
        },
        {
            "kind": "WORK_EPOCH_ROLLOVER",
            "subject": work,
            "work_epoch": epoch,
            "exhaustion": exhaustion,
            "authority": authority,
        },
    ]


@pytest.mark.parametrize("value", _variants())
def test_public_consumer_can_roundtrip_each_applicability(value: dict[str, Any]) -> None:
    adapter: TypeAdapter[WorkerCommitApplicability] = TypeAdapter(WorkerCommitApplicability)
    parsed = adapter.validate_python(value)
    assert adapter.validate_json(parsed.canonical_bytes()) == parsed
    assert parsed.canonical_bytes().startswith(b'{"kind":')
    with pytest.raises(ValidationError):
        adapter.validate_python({**value, "unregistered_field": "extra"})


@pytest.mark.parametrize("marker", [Absent(), NotApplicable()])
def test_absence_and_not_applicable_cannot_smuggle_a_head(marker: RecoveryDTO) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(HeadMarker).validate_python({**marker.model_dump(), "head": "unexpected"})
    assert Absent().canonical_bytes() != NotApplicable().canonical_bytes()


def test_disjoint_tagged_subject_domains_and_different_identity_bytes() -> None:
    individual = IndividualSubject(occurrence_id="same-untrusted-payload")
    coalesced = CoalescedSubject(aggregate_id="same-untrusted-payload", manifest_fingerprint=DIGEST)
    assert individual.canonical_bytes() != coalesced.canonical_bytes()
    with pytest.raises(ValidationError):
        TypeAdapter(ExecutionLineageSubject).validate_python(
            {
                **individual.model_dump(),
                "identity_domain": coalesced.identity_domain,
            }
        )


def test_non_scheduler_cannot_borrow_live_lease_and_work_cannot_borrow_physical_epoch() -> None:
    variants = _variants()
    adapter: TypeAdapter[WorkerCommitApplicability] = TypeAdapter(WorkerCommitApplicability)
    with pytest.raises(ValidationError):
        adapter.validate_python({**variants[0], "lease": variants[1]["lease"]})
    with pytest.raises(ValidationError):
        adapter.validate_python({**variants[3], "physical_root": variants[1]["physical_root"]})


@pytest.mark.parametrize("generation", [-1, 2**64, True, "1"])
def test_lease_uint64_is_strict_and_never_wraps(generation: object) -> None:
    value = deepcopy(_variants()[1])
    value["lease"]["generation"] = generation
    with pytest.raises(ValidationError):
        TypeAdapter(WorkerCommitApplicability).validate_python(value)


def test_rollover_requires_both_predecessor_decision_and_edge() -> None:
    value = deepcopy(_variants()[4])
    del value["authority"]["predecessor_rollover"]["edge"]
    with pytest.raises(ValidationError):
        TypeAdapter(WorkerCommitApplicability).validate_python(value)


def test_post_terminal_resolver_version_is_not_reducer_version() -> None:
    value = deepcopy(_variants()[3])
    del value["subject"]["original_obligation"]["resolver_version"]
    with pytest.raises(ValidationError):
        TypeAdapter(WorkerCommitApplicability).validate_python(value)


@pytest.mark.parametrize("kind", ["UNKNOWN", "LIVE", "NOT_APPLICABLE"])
def test_unknown_or_aliased_applicability_rejects(kind: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(WorkerCommitApplicability).validate_python({**_variants()[0], "kind": kind})


def test_scheduler_run_reference_retains_exact_original_tagged_subject_bytes() -> None:
    subject = CoalescedSubject(aggregate_id="aggregate", manifest_fingerprint=DIGEST)
    ref = SchedulerRootReference(
        root_id="lineage",
        subject_canonical_base64=base64.b64encode(subject.canonical_bytes()).decode(),
        subject_schema_version="chiplog.execution-lineage-subject.v1",
        root_fingerprint=DIGEST,
        initial_run_id="initial-run",
    )
    assert (
        base64.b64decode(ref.subject_canonical_base64, validate=True) == subject.canonical_bytes()
    )
    assert SchedulerRootReference.model_validate_json(ref.canonical_bytes()) == ref
    for mutation in (
        {"subject_canonical_base64": "not base64!"},
        {"subject_canonical_base64": "Zh=="},  # Nonzero unused bits aliases b"f".
        {"subject_schema_version": "unknown"},
        {"physical_epoch": "cannot-rebind-stable-root"},
    ):
        with pytest.raises(ValidationError):
            SchedulerRootReference.model_validate({**ref.model_dump(), **mutation})
