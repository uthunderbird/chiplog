"""Pure structural support for worker applicability fences.

This module derives obligations from a submitted fence and compares explicitly
supplied, already-decoded captures.  It neither reads registered sources nor
establishes their provenance, freshness, proof issuance, clock trust, or writer
admission.  The writer boundary must map these roles to registered readers and
recheck them at its transaction cut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Never

from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    PhysicalRootRolloverFence,
    PostTerminalWorkFence,
    PreRootDecisionFence,
    RecoveryDTO,
    SchedulerExecutionFence,
    WorkEpochRolloverFence,
    WorkerCommitApplicability,
)


class ApplicabilityShapeError(ValueError):
    """The supplied structural capture does not match a fence obligation."""


@dataclass(frozen=True)
class SourceRequirement:
    """One field-level obligation derived from a fence, never a source registration."""

    role: str
    source_identity: str
    expected_head: str
    expected_binding_bytes: bytes


@dataclass(frozen=True)
class CapturedSource:
    """Caller-supplied decoded correspondence data, not an authority artifact."""

    role: str
    source_identity: str
    head: str
    binding_bytes: bytes
    raw_bytes: bytes
    raw_fingerprint: str


def _canonical(value: RecoveryDTO | dict[str, str]) -> bytes:
    if isinstance(value, RecoveryDTO):
        return value.canonical_bytes()
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _requirement(
    role: str, head: str, binding: RecoveryDTO | dict[str, str]
) -> SourceRequirement:
    return SourceRequirement(
        role=role,
        source_identity=f"chiplog.worker-applicability.{role}.v1",
        expected_head=head,
        expected_binding_bytes=_canonical(binding),
    )


def interpret_worker_applicability(
    fence: WorkerCommitApplicability,
) -> tuple[SourceRequirement, ...]:
    """Derive the finite source-role obligations for one of the six fence variants."""

    if isinstance(fence, NonSchedulerFence):
        return (
            _requirement(
                "original-run",
                fence.run_head,
                {"run_id": fence.run_id, "run_head": fence.run_head},
            ),
            _requirement(
                "worker-runtime",
                fence.runtime_generation,
                {
                    "runtime_generation": fence.runtime_generation,
                    "worker_session_id": fence.worker_session_id,
                },
            ),
        )
    if isinstance(fence, SchedulerExecutionFence):
        return (
            _requirement("original-run", fence.run_head, {"run_head": fence.run_head}),
            _requirement("scheduler-lineage", fence.lineage.lineage_head, fence.lineage),
            _requirement("physical-root", fence.physical_root.selector_head, fence.physical_root),
            _requirement("live-lease", fence.lease.lease_head, fence.lease),
            _requirement("clock-proof", fence.clock_proof.proof_id, fence.clock_proof),
        )
    if isinstance(fence, PreRootDecisionFence):
        decision_head = getattr(fence.disposition.decision, "head", "ABSENT")
        return (
            _requirement(
                "scheduler-authority",
                fence.scheduler_authority_head,
                {
                    "command_id": fence.command_id,
                    "scheduler_authority_head": fence.scheduler_authority_head,
                },
            ),
            _requirement(
                "runtime",
                fence.runtime_generation,
                {
                    "broker_generation": fence.broker_generation,
                    "runtime_generation": fence.runtime_generation,
                },
            ),
            _requirement("decision", decision_head, fence.disposition),
        )
    if isinstance(fence, PostTerminalWorkFence):
        return (
            _requirement("work-subject", fence.subject.work_subject_head, fence.subject),
            _requirement("work-epoch", fence.work_epoch.selector_head, fence.work_epoch),
            _requirement("live-lease", fence.lease.lease_head, fence.lease),
            _requirement("clock-proof", fence.clock_proof.proof_id, fence.clock_proof),
        )
    if isinstance(fence, PhysicalRootRolloverFence):
        return (
            _requirement("scheduler-lineage", fence.lineage.lineage_head, fence.lineage),
            _requirement("physical-root", fence.physical_root.selector_head, fence.physical_root),
            _requirement("exhaustion", fence.exhaustion.hold_head, fence.exhaustion),
            _requirement("rollover-authority", fence.authority.authority_head, fence.authority),
        )
    if isinstance(fence, WorkEpochRolloverFence):
        return (
            _requirement("work-subject", fence.subject.work_subject_head, fence.subject),
            _requirement("work-epoch", fence.work_epoch.selector_head, fence.work_epoch),
            _requirement("exhaustion", fence.exhaustion.hold_head, fence.exhaustion),
            _requirement("rollover-authority", fence.authority.authority_head, fence.authority),
        )
    return _unsupported(fence)


def _unsupported(fence: object) -> Never:
    raise ApplicabilityShapeError(f"unsupported worker applicability: {type(fence).__name__}")


def matches_captured_cut(
    requirements: tuple[SourceRequirement, ...], captured: tuple[CapturedSource, ...]
) -> None:
    """Reject non-identical caller-supplied structural captures.

    ``raw_fingerprint`` checks transport integrity only.  It does not prove that
    ``raw_bytes`` were read from an authoritative source or at a current cut.
    """

    expected = {item.role: item for item in requirements}
    if len(expected) != len(requirements):
        raise ApplicabilityShapeError("duplicate required source role")
    observed = {item.role: item for item in captured}
    if len(observed) != len(captured):
        raise ApplicabilityShapeError("duplicate captured source role")
    if set(observed) != set(expected):
        raise ApplicabilityShapeError("captured source roles differ from requirements")
    for role, requirement in expected.items():
        source = observed[role]
        if sha256(source.raw_bytes).hexdigest() != source.raw_fingerprint:
            raise ApplicabilityShapeError(f"captured {role} raw bytes fingerprint differs")
        if source.source_identity != requirement.source_identity:
            raise ApplicabilityShapeError(f"captured {role} source identity differs")
        if source.head != requirement.expected_head:
            raise ApplicabilityShapeError(f"captured {role} head differs")
        if source.binding_bytes != requirement.expected_binding_bytes:
            raise ApplicabilityShapeError(f"captured {role} binding differs")
