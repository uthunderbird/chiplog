"""Actual executable parser/owner preparation; no authenticated writer claim."""

import base64

import pytest
from tests.support.execution_fan_out import bind_run, fixture, ref

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallPreparationRejected,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
)
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
    ExecutionCapturedFanOutRequest,
)
from chiplog.capabilities.agent_loop.execution_fan_out_preparation import (
    prepare_execution_captured_fan_out,
)


def accepted(value: ExecutionCapturedFanOutRequest) -> ExecutionCapturedFanOutProposal:
    result = prepare_execution_captured_fan_out(value)
    assert isinstance(result, ExecutionCapturedFanOutProposal), result
    return result


def rejected(value: ExecutionCapturedFanOutRequest) -> CallPreparationRejected:
    result = prepare_execution_captured_fan_out(value)
    assert isinstance(result, CallPreparationRejected), result
    return result


@pytest.mark.parametrize("complete", [False, True])
async def test_owner_seals_exact_capture_without_terminalizing_any_call(complete: bool) -> None:
    value = await fixture(complete)
    result = accepted(value)
    assert accepted(value) == result
    run, original = result.sealed_run, value.captured_run
    assert run.predecessor == original.head
    assert run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest()
    assert run.state == "ACTIVE" and run.delivery_acceptance is None
    turn = run.turns[-1]
    assert turn.state == ("RESPONSE_AVAILABLE" if complete else "ACCEPTED")
    assert turn.attempts[-1].state == ("RESPONSE_CAPTURED" if complete else "TERMINAL_ACCEPTED")
    if complete:
        assert turn.attempts[-1] == original.turns[-1].attempts[-1]
        assert run.event == "ModelCompletionPrepared"
    assert turn.attempts[-1].response_base64 == value.request.canonical_response_base64
    assert turn.response_seal == result.fan_out.complete_ordered_record_manifest[0]
    assert turn.initialized_calls == result.fan_out.response_seal.complete_ordered_initialized
    assert len(result.fan_out.initialized_records) == (0 if complete else 3)
    assert original.turns[-1].initialized_calls is None
    assert original.turns[-1].attempts[-1].state == "RESPONSE_CAPTURED"
    if not complete:
        row = result.fan_out.initialized_records[-1]
        assert row.call.classification == "CONSEQUENTIAL"
        call = ConsequentialToolCall.model_validate_json(
            base64.b64decode(row.call.canonical_call_base64)
        )
        assert call.arguments.payload == b"\xff\x00exact"
        assert call.arguments.bundle_members == ("b", "a")
        assert row.original_call_id == "call:" + row.call.original.digest()
        assert "result" not in row.model_dump() and "proposal_id" not in row.model_dump()


@pytest.mark.parametrize("mutation", ["omit", "extra", "reorder", "duplicate", "bytes", "ordinal"])
async def test_entire_ordered_capture_must_match(mutation: str) -> None:
    value = await fixture()
    calls = value.request.ordered_calls
    if mutation == "omit":
        calls = calls[:-1]
    elif mutation == "extra":
        calls = (*calls, calls[-1])
    elif mutation == "reorder":
        calls = tuple(reversed(calls))
    elif mutation == "duplicate":
        calls = (calls[0], calls[0], calls[2])
    elif mutation == "bytes":
        calls = (*calls[:-1], calls[-1].model_copy(update={"canonical_call_base64": "e30="}))
    else:
        calls = (
            *calls[:-1],
            calls[-1].model_copy(
                update={"original": calls[-1].original.model_copy(update={"ordinal": 0})}
            ),
        )
    rejected(
        value.model_copy(
            update={"request": value.request.model_copy(update={"ordered_calls": calls})}
        )
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "reorder", "classification", "schema"]
)
async def test_registry_cannot_reclassify_or_omit_registered_tools(mutation: str) -> None:
    value = await fixture()
    entries = value.tool_registry.entries
    if mutation == "missing":
        entries = entries[:-1]
    elif mutation == "duplicate":
        entries = (*entries, entries[-1])
    elif mutation == "reorder":
        entries = tuple(reversed(entries))
    elif mutation == "classification":
        entries = (
            *entries[:-1],
            entries[-1].model_copy(update={"classification": "PROPOSAL_ONLY"}),
        )
    else:
        entries = (*entries[:-1], entries[-1].model_copy(update={"schema_id": "unknown"}))
    registry = value.tool_registry.model_copy(update={"entries": entries})
    rejected(
        value.model_copy(
            update={
                "tool_registry": registry,
                "tool_registry_head": ref(registry.registry_id, registry),
            }
        )
    )


@pytest.mark.parametrize("mutation", ["worker", "receipt", "generation", "selector", "seal", "raw"])
async def test_stale_or_resealed_capture_is_rejected(mutation: str) -> None:
    value = await fixture()
    run = value.captured_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    if mutation == "selector":
        turn = turn.model_copy(update={"selector": 1})
    elif mutation == "seal":
        turn = turn.model_copy(update={"initialized_calls": ()})
    else:
        changes: dict[str, dict[str, object]] = {
            "worker": {"worker_session": "another-worker"},
            "receipt": {"receipt": None},
            "generation": {"generation": 1},
            "raw": {"response_base64": "e30="},
        }
        turn = turn.model_copy(update={"attempts": (attempt.model_copy(update=changes[mutation]),)})
    rejected(bind_run(value, run.model_copy(update={"turns": (turn,)})))


async def test_call_and_manifest_bounds_include_exact_boundary() -> None:
    value = await fixture()
    result = accepted(value)
    manifest = result.fan_out.complete_ordered_record_manifest
    size = len(b"[" + b",".join(item.canonical_bytes() for item in manifest) + b"]")
    for bound in (
        value.request.bound.model_copy(update={"max_call_count": 3, "max_manifest_bytes": size}),
        value.request.bound.model_copy(update={"max_call_count": 2}),
        value.request.bound.model_copy(update={"max_manifest_bytes": size - 1}),
        value.request.bound.model_copy(update={"max_serialized_batch_bytes": 1}),
    ):
        candidate = value.model_copy(
            update={"request": value.request.model_copy(update={"bound": bound})}
        )
        if bound.max_call_count == 3:
            accepted(candidate)
        else:
            assert rejected(candidate).code == "DENIED"


async def test_response_and_complete_owner_output_boundaries() -> None:
    value = await fixture()
    raw_size = len(base64.b64decode(value.request.canonical_response_base64))
    run = value.captured_run
    for limit in (raw_size, raw_size - 1):
        changed = run.model_copy(
            update={"policy": run.policy.model_copy(update={"max_response_bytes": limit})}
        )
        candidate = bind_run(value, changed)
        if limit == raw_size:
            accepted(candidate)
        else:
            rejected(candidate)

    # Keep the bound's decimal width fixed so changing it cannot change wire length.
    inner = value.request
    value = value.model_copy(
        update={
            "request": inner.model_copy(
                update={
                    "bound": inner.bound.model_copy(update={"max_serialized_batch_bytes": 99999})
                }
            )
        }
    )
    size = len(accepted(value).canonical_bytes())
    assert 10000 <= size < 99999
    for limit in (size, size - 1):
        inner = value.request
        candidate = value.model_copy(
            update={
                "request": inner.model_copy(
                    update={
                        "bound": inner.bound.model_copy(
                            update={"max_serialized_batch_bytes": limit}
                        )
                    }
                )
            }
        )
        if limit == size:
            assert len(accepted(candidate).canonical_bytes()) == size
        else:
            assert rejected(candidate).code == "DENIED"


async def test_sealing_preserves_recovery_references_and_cannot_be_replayed_as_capture() -> None:
    value = await fixture()
    original_ref = value.request.captured_response
    run = value.captured_run.model_copy(
        update={"no_retry_references": (original_ref,), "suspension_baseline": original_ref}
    )
    value = bind_run(value, run)
    result = accepted(value)
    assert result.sealed_run.no_retry_references == (original_ref,)
    assert result.sealed_run.suspension_baseline == original_ref
    rejected(bind_run(value, result.sealed_run))


@pytest.mark.parametrize("field", ["tenant", "principal", "run_id", "turn_id", "contour_head"])
async def test_captured_manifest_must_belong_to_the_same_run(field: str) -> None:
    value = await fixture()
    run = value.captured_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    attempt = attempt.model_copy(
        update={"manifest": attempt.manifest.model_copy(update={field: "substituted"})}
    )
    turn = turn.model_copy(update={"attempts": (attempt,)})
    rejected(bind_run(value, run.model_copy(update={"turns": (turn,)})))
