"""Producer-derived fixtures for scheduler overflow-hold physical codecs."""

from __future__ import annotations

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    OverflowActive,
    OverflowResolved,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import OverflowHoldPrimitiveV2
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    SchedulerCanonicalMember,
    prepare_streamed_overflow,
)
from chiplog.capabilities.agent_loop.scheduler_overflow_hold_records import (
    ExecutableOverflowHoldRecordV2,
    executable_overflow_hold_member,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    scheduler_execution_command_reference,
)
from tests.support.scheduler_seed_producer import overflow_request


def v1_overflow_hold_member() -> SchedulerCanonicalMember:
    request = overflow_request()
    source = request.source.observation
    evidence = request.eligibility
    assert evidence.kind == "STREAMING_MANIFEST"
    command = DecideIntervalCommand(
        identity=request.identity,
        boundary=source.boundary,
        bound_head=source.configuration.bound,
        manifest=evidence,
        publication_fence=source.pre_root_fence,
    )
    candidate = prepare_streamed_overflow(
        command=command,
        current_bound=source.configuration.bound,
        evidence=evidence,
        member_count=11,
        manifest_bytes=101,
        operator_recovery_owner="operator",
    )
    return candidate.records[0]


def v2_overflow_hold_record(
    *, resolved: bool = False, command_suffix: str = ""
) -> ExecutableOverflowHoldRecordV2:
    request = overflow_request()
    command = request.identity.model_copy(
        update={"command_id": request.identity.command_id + command_suffix}
    )
    source = request.source.observation
    primitive = OverflowHoldPrimitiveV2(
        command=scheduler_execution_command_reference(command),
        boundary=source.boundary,
        bound_head=source.configuration.bound,
        exceeded_dimension="MEMBER_COUNT",
        actual_value=2,
        limit=1,
        evidence=request.eligibility,
    )
    return ExecutableOverflowHoldRecordV2(
        command=command,
        primitive=primitive,
        operator_recovery_owner="operator",
        state=(
            OverflowResolved(resolution_decision=Present(head="resolution", fingerprint="a" * 64))
            if resolved
            else OverflowActive()
        ),
    )


def v2_overflow_hold_member(*, resolved: bool = False) -> SchedulerCanonicalMember:
    return executable_overflow_hold_member(v2_overflow_hold_record(resolved=resolved))
