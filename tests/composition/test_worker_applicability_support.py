from __future__ import annotations

from hashlib import sha256
from typing import Any, cast

import pytest
from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.recovery_contracts import WorkerCommitApplicability

DIGEST = "a" * 64
_ADAPTER: TypeAdapter[WorkerCommitApplicability] = TypeAdapter(WorkerCommitApplicability)


def _fences() -> tuple[WorkerCommitApplicability, ...]:
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
    values: tuple[dict[str, Any], ...] = (
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
    )
    return tuple(_ADAPTER.validate_python(value) for value in values)


@pytest.fixture(params=_fences(), ids=lambda fence: fence.kind)
def fence(request: pytest.FixtureRequest) -> WorkerCommitApplicability:
    return cast(WorkerCommitApplicability, request.param)


def _captured(requirements: tuple[Any, ...]) -> tuple[Any, ...]:
    from chiplog.composition.r14_worker_applicability_support import CapturedSource

    return tuple(
        CapturedSource(
            role=requirement.role,
            source_identity=requirement.source_identity,
            head=requirement.expected_head,
            binding_bytes=requirement.expected_binding_bytes,
            raw_bytes=b"captured/" + requirement.role.encode(),
            raw_fingerprint=sha256(b"captured/" + requirement.role.encode()).hexdigest(),
        )
        for requirement in requirements
    )


def test_interpreter_emits_exact_six_variant_source_roles(fence: WorkerCommitApplicability) -> None:
    from chiplog.composition.r14_worker_applicability_support import interpret_worker_applicability

    expected = {
        "NON_SCHEDULER_NOT_APPLICABLE": ("original-run", "worker-runtime"),
        "EXECUTION_ROOT_LIVE_LEASE": (
            "original-run",
            "scheduler-lineage",
            "physical-root",
            "live-lease",
            "clock-proof",
        ),
        "PRE_ROOT_SCHEDULER_DECISION": ("scheduler-authority", "runtime", "decision"),
        "POST_TERMINAL_RECOVERY_WORK": ("work-subject", "work-epoch", "live-lease", "clock-proof"),
        "PHYSICAL_ROOT_ROLLOVER": (
            "scheduler-lineage",
            "physical-root",
            "exhaustion",
            "rollover-authority",
        ),
        "WORK_EPOCH_ROLLOVER": ("work-subject", "work-epoch", "exhaustion", "rollover-authority"),
    }

    actual = tuple(item.role for item in interpret_worker_applicability(fence))
    assert actual == expected[fence.kind]


def test_captured_cut_rejects_missing_extra_duplicate_and_corrupted_bytes(
    fence: WorkerCommitApplicability,
) -> None:
    from chiplog.composition.r14_worker_applicability_support import (
        ApplicabilityShapeError,
        CapturedSource,
        interpret_worker_applicability,
        matches_captured_cut,
    )

    requirements = interpret_worker_applicability(fence)
    captured = _captured(requirements)
    matches_captured_cut(requirements, captured)
    extra = CapturedSource(
        role="unexpected",
        source_identity="unexpected",
        head="unexpected",
        binding_bytes=b"unexpected",
        raw_bytes=b"unexpected",
        raw_fingerprint=sha256(b"unexpected").hexdigest(),
    )
    for mutation in (captured[:-1], (*captured, captured[0]), (*captured, extra)):
        with pytest.raises(ApplicabilityShapeError):
            matches_captured_cut(requirements, mutation)
    damaged = captured[0].__class__(
        **{**captured[0].__dict__, "raw_bytes": b"substituted"}
    )
    with pytest.raises(ApplicabilityShapeError, match="fingerprint"):
        matches_captured_cut(requirements, (damaged, *captured[1:]))


def test_each_variant_rejects_stale_head_and_source_identity(
    fence: WorkerCommitApplicability,
) -> None:
    from chiplog.composition.r14_worker_applicability_support import (
        ApplicabilityShapeError,
        interpret_worker_applicability,
        matches_captured_cut,
    )

    requirements = interpret_worker_applicability(fence)
    captured = _captured(requirements)
    for field, value in (("head", "stale-head"), ("source_identity", "substituted-source")):
        mutated = captured[0].__class__(**{**captured[0].__dict__, field: value})
        with pytest.raises(ApplicabilityShapeError):
            matches_captured_cut(requirements, (mutated, *captured[1:]))


def test_variant_bindings_reject_worker_lease_epoch_clock_and_proof_mutations(
    fence: WorkerCommitApplicability,
) -> None:
    from chiplog.composition.r14_worker_applicability_support import (
        ApplicabilityShapeError,
        interpret_worker_applicability,
        matches_captured_cut,
    )

    requirements = interpret_worker_applicability(fence)
    captured = _captured(requirements)
    for index, item in enumerate(captured):
        mutated = item.__class__(**{**item.__dict__, "binding_bytes": b"mutated-binding"})
        with pytest.raises(ApplicabilityShapeError):
            matches_captured_cut(requirements, (*captured[:index], mutated, *captured[index + 1 :]))


def test_unknown_variant_fails_closed() -> None:
    from chiplog.composition.r14_worker_applicability_support import (
        ApplicabilityShapeError,
        interpret_worker_applicability,
    )

    unknown = object()
    with pytest.raises(ApplicabilityShapeError, match="unsupported"):
        interpret_worker_applicability(unknown)  # type: ignore[arg-type]
