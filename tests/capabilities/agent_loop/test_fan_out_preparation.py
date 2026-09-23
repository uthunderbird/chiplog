"""Pure producer evidence; supplied observations do not authenticate a runtime cut."""

import base64
import json

import pytest
from tests.support.captured_fan_out import bind_registry as _bind_registry
from tests.support.captured_fan_out import bind_run as _bind_run
from tests.support.captured_fan_out import fixture as _fixture
from tests.support.captured_fan_out import ref as _ref
from tests.support.captured_fan_out import sha as _sha
from tests.support.captured_fan_out import with_raw as _raw

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import contracts as loop
from chiplog.capabilities.agent_loop import fan_out_contracts as fan
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryCompletion,
)
from chiplog.capabilities.agent_loop.fan_out_preparation import prepare_captured_fan_out
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall


def _accepted(value: fan.CapturedFanOutRequest) -> fan.CapturedFanOutProposal:
    result = prepare_captured_fan_out(value)
    assert isinstance(result, fan.CapturedFanOutProposal), result
    return result


def _rejected(value: fan.CapturedFanOutRequest, code: str | None = None) -> None:
    result = prepare_captured_fan_out(value)
    assert isinstance(result, call.CallPreparationRejected), result
    if code is not None:
        assert result.code == code


def _proposal_hash(value: fan.CapturedFanOutProposal | call.PreparedCallFanOut) -> str:
    wire = json.loads(value.canonical_bytes())
    del wire["proposal_fingerprint"]
    return _sha(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode())


@pytest.mark.parametrize("delivery", (False, True))
@pytest.mark.parametrize("complete", (False, True))
async def test_registered_parsers_prepare_deterministic_complete_manifests(
    delivery: bool,
    complete: bool,
) -> None:
    value = await _fixture(delivery, complete)
    result = _accepted(value)
    assert result == prepare_captured_fan_out(value)
    assert result.source_request_fingerprint == _sha(value.canonical_bytes())
    inner = result.fan_out
    assert inner.source_request_fingerprint == _sha(value.request.canonical_bytes())
    assert result.proposal_fingerprint == _proposal_hash(result)
    assert inner.proposal_fingerprint == _proposal_hash(inner)
    seal = inner.response_seal
    assert seal.response_seal_id == "response-seal:" + _sha(value.request.canonical_bytes())
    assert seal.captured_response == value.request.captured_response
    assert len(inner.initialized_records) == (0 if complete else 2)
    for expected, actual in zip(
        value.request.ordered_calls, inner.initialized_records, strict=True
    ):
        assert actual.call == expected
        assert actual.original_call_id == "call:" + _sha(expected.original.canonical_bytes())
        assert actual.predecessor == Absent()
    heads = tuple(
        _ref(item.original_call_id, item.canonical_bytes()) for item in inner.initialized_records
    )
    assert seal.complete_ordered_initialized == heads
    assert inner.complete_ordered_record_manifest == (
        _ref(seal.response_seal_id, seal.canonical_bytes()),
        *heads,
    )


@pytest.mark.parametrize(
    "defect",
    (
        "subset",
        "superset",
        "reorder",
        "duplicate",
        "label",
        "bytes",
        "ordinal",
        "tenant",
        "turn",
        "schema",
        "policy",
        "capture",
    ),
)
async def test_calls_must_equal_all_parsed_calls_in_original_order(defect: str) -> None:
    value = await _fixture()
    calls = value.request.ordered_calls
    first = calls[0]
    if defect == "subset":
        calls = calls[:1]
    elif defect == "superset":
        calls = (*calls, first)
    elif defect == "reorder":
        calls = tuple(reversed(calls))
    elif defect == "duplicate":
        calls = (first, first)
    else:
        if defect in ("label", "ordinal", "tenant", "turn", "capture"):
            field, replacement = {
                "label": ("model_call_label", "foreign"),
                "ordinal": ("ordinal", 1),
                "tenant": ("tenant_id", "foreign"),
                "turn": ("original_turn_id", "foreign"),
                "capture": ("captured_response", _ref("foreign", b"foreign")),
            }[defect]
            first = first.model_copy(
                update={"original": first.original.model_copy(update={field: replacement})}
            )
        else:
            field = {
                "bytes": "canonical_call_base64",
                "schema": "tool_schema",
                "policy": "tool_policy",
            }[defect]
            replacement = "e30=" if defect == "bytes" else _ref("foreign", b"foreign")
            first = first.model_copy(update={field: replacement})
        calls = (first, calls[1])
    _rejected(
        value.model_copy(
            update={"request": value.request.model_copy(update={"ordered_calls": calls})}
        )
    )


@pytest.mark.parametrize(
    "defect",
    ("subset", "duplicate", "reorder", "unknown", "schema", "consequential", "readonly", "retry"),
)
async def test_registry_must_exactly_cover_registered_proposal_specs(defect: str) -> None:
    value = await _fixture()
    entries = value.tool_registry.entries
    if defect == "subset":
        entries = entries[:1]
    elif defect == "duplicate":
        entries = (entries[0], entries[0], entries[1])
    elif defect == "reorder":
        entries = tuple(reversed(entries))
    elif defect == "unknown":
        entries = (*entries, entries[0].model_copy(update={"tool_name": "zzz"}))
    else:
        updates: dict[str, dict[str, object]] = {
            "schema": {"tool_schema": _ref("foreign", b"foreign")},
            "consequential": {"classification": "CONSEQUENTIAL"},
            "readonly": {"classification": "READ_ONLY"},
            "retry": {
                "retry_policy": fan.ReadOnlyFanOutPolicy(
                    max_attempts=3,
                    budget_version="b",
                    reducer_id="r",
                    reducer_version="1",
                )
            },
        }
        entries = (entries[0].model_copy(update=updates[defect]), entries[1])
    _rejected(_bind_registry(value, entries))


@pytest.mark.parametrize(
    "defect",
    (
        "selfhead",
        "worker",
        "selector",
        "attempt_state",
        "manifest_tenant",
        "manifest_generation",
        "event",
        "run_state",
        "turn_state",
    ),
)
async def test_selected_capture_and_run_identity_are_checked(defect: str) -> None:
    value = await _fixture()
    run = value.captured_run
    turn = run.turns[-1]
    attempt = turn.attempts[-1]
    if defect == "selfhead":
        _rejected(
            value.model_copy(update={"captured_run": run.model_copy(update={"head": "forged"})})
        )
        return
    if defect in ("event", "run_state"):
        run = run.model_copy(
            update={
                "event" if defect == "event" else "state": "Other"
                if defect == "event"
                else "CREATED"
            }
        )
    else:
        if defect == "worker":
            attempt = attempt.model_copy(update={"worker_session": "foreign"})
        elif defect == "attempt_state":
            attempt = attempt.model_copy(update={"state": "EMITTED_OUTCOME_UNKNOWN"})
        elif defect in ("manifest_tenant", "manifest_generation"):
            key = "tenant" if defect == "manifest_tenant" else "generation"
            attempt = attempt.model_copy(
                update={
                    "manifest": attempt.manifest.model_copy(
                        update={
                            key: "foreign" if key == "tenant" else 1,
                        }
                    )
                }
            )
        turn = turn.model_copy(update={"attempts": (attempt,)})
        if defect == "selector":
            turn = turn.model_copy(update={"selector": 1})
        if defect == "turn_state":
            turn = turn.model_copy(update={"state": "CALL_ACTIVE"})
        run = run.model_copy(update={"turns": (turn,)})
    _rejected(_bind_run(value, run))


@pytest.mark.parametrize(
    "defect", ("tenant", "inventory", "capture", "fence", "registry_head", "raw")
)
async def test_input_cut_and_capture_references_are_checked(defect: str) -> None:
    value = await _fixture()
    inner = value.request
    if defect == "registry_head":
        value = value.model_copy(update={"tool_registry_head": _ref("foreign", b"foreign")})
    elif defect == "raw":
        inner = inner.model_copy(update={"canonical_response_base64": "e30="})
    elif defect == "capture":
        inner = inner.model_copy(update={"captured_response": _ref("foreign", b"foreign")})
    else:
        changes: dict[str, dict[str, object]] = {
            "tenant": {"tenant_id": "foreign"},
            "inventory": {"complete_call_inventory": _ref("foreign", b"foreign")},
            "fence": {"fence": inner.cut.fence.model_copy(update={"worker_session_id": "foreign"})},
        }
        inner = inner.model_copy(update={"cut": inner.cut.model_copy(update=changes[defect])})
    _rejected(value.model_copy(update={"request": inner}))


async def test_malformed_nested_model_copy_is_typed_rejection() -> None:
    value = await _fixture()
    invalid = value.request.bound.model_copy(update={"max_call_count": "many"})
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        _rejected(
            value.model_copy(
                update={"request": value.request.model_copy(update={"bound": invalid})}
            ),
            "INTEGRITY_FAULT",
        )


@pytest.mark.parametrize("delivery", (False, True))
async def test_parser_canonicality_uses_exact_captured_bytes(delivery: bool) -> None:
    value = await _fixture(delivery)
    raw = base64.b64decode(value.request.canonical_response_base64)
    pretty = json.dumps(json.loads(raw), indent=2).encode() + b"\n"
    value = _raw(value, pretty)
    if delivery:
        _rejected(value)
    else:
        result = _accepted(value)
        assert result.fan_out.response_seal.captured_response == value.request.captured_response
        assert base64.b64decode(value.request.canonical_response_base64) == pretty


@pytest.mark.parametrize("bound", ("max_call_count", "max_tool_calls"))
async def test_call_count_exact_limit_and_one_excess(bound: str) -> None:
    value = await _fixture()
    for maximum in (2, 1):
        if bound == "max_call_count":
            changed = value.model_copy(
                update={
                    "request": value.request.model_copy(
                        update={
                            "bound": value.request.bound.model_copy(update={bound: maximum}),
                        }
                    )
                }
            )
        else:
            changed = _bind_run(
                value,
                value.captured_run.model_copy(
                    update={
                        "policy": value.captured_run.policy.model_copy(update={bound: maximum}),
                    }
                ),
            )
        if maximum == 2:
            _accepted(changed)
        else:
            _rejected(changed)


@pytest.mark.parametrize("complete", (False, True))
async def test_complete_semantic_manifest_byte_limit_includes_seal(complete: bool) -> None:
    value = await _fixture(complete=complete)
    prepared = _accepted(value).fan_out
    size = len(
        b"["
        + b",".join(head.canonical_bytes() for head in prepared.complete_ordered_record_manifest)
        + b"]"
    )
    for maximum in (size, size - 1):
        changed = value.model_copy(
            update={
                "request": value.request.model_copy(
                    update={
                        "bound": value.request.bound.model_copy(
                            update={"max_manifest_bytes": maximum}
                        ),
                    }
                )
            }
        )
        if maximum == size:
            accepted = _accepted(changed)
            assert (
                len(
                    b"["
                    + b",".join(
                        head.canonical_bytes()
                        for head in accepted.fan_out.complete_ordered_record_manifest
                    )
                    + b"]"
                )
                == size
            )
        else:
            _rejected(changed)


async def test_existing_initialized_original_conflicts() -> None:
    value = await _fixture()
    initialized = _accepted(value).fan_out.initialized_records[0]
    reference = _ref(initialized.original_call_id, initialized.canonical_bytes())
    observation = call.CallLifecycleObservation(
        original_call_id=initialized.original_call_id,
        initialized=reference,
        initialized_record=initialized,
        acceptance=InitializedCall(initialized=reference.revision),
        terminal=Absent(),
    )
    inventory = value.request.cut.predecessor_inventory.model_copy(
        update={"ordered_calls": (observation,)}
    )
    cut = value.request.cut.model_copy(
        update={
            "predecessor_inventory": inventory,
            "complete_call_inventory": _ref("call-inventory:tenant", inventory.canonical_bytes()),
        }
    )
    _rejected(
        value.model_copy(update={"request": value.request.model_copy(update={"cut": cut})}),
        "CONFLICT",
    )


async def test_scheduler_root_cannot_hide_behind_non_scheduler_fence() -> None:
    value = await _fixture()
    root = loop.SchedulerRootReference(
        root_id="root",
        subject_canonical_base64="e30=",
        subject_schema_version="chiplog.execution-lineage-subject.v1",
        root_fingerprint="a" * 64,
        initial_run_id="run",
    )
    value = _bind_run(value, value.captured_run.model_copy(update={"root_binding": root}))
    # All hashes and capture/fence references are repaired: lineage itself must reject.
    assert value.request.cut.fence.kind == "NON_SCHEDULER_NOT_APPLICABLE"
    assert value.request.cut.current_run == value.request.captured_response
    _rejected(value, "UNSUPPORTED")


async def test_duplicate_labels_in_capture_are_rejected_by_selected_parser() -> None:
    value = await _fixture()
    wire = json.loads(base64.b64decode(value.request.canonical_response_base64))
    wire["tool_calls"][1]["call_id"] = wire["tool_calls"][0]["call_id"]
    value = _raw(value, json.dumps(wire).encode())
    calls = value.request.ordered_calls
    duplicate = calls[1].model_copy(
        update={
            "original": calls[1].original.model_copy(
                update={"model_call_label": wire["tool_calls"][0]["call_id"]}
            ),
            "canonical_call_base64": base64.b64encode(
                loop.ToolCall.model_validate(wire["tool_calls"][1]).canonical_bytes()
            ).decode(),
        }
    )
    value = value.model_copy(
        update={
            "request": value.request.model_copy(
                update={
                    "ordered_calls": (calls[0], duplicate),
                }
            )
        }
    )
    _rejected(value)


@pytest.mark.parametrize("field", ("tenant", "run_id", "turn_id"))
async def test_delivery_completion_scope_must_match_capture(field: str) -> None:
    value = await _fixture(delivery=True, complete=True)
    complete = DeliveryCompletion.model_validate_json(
        base64.b64decode(value.request.canonical_response_base64)
    )
    value = _raw(value, complete.model_copy(update={field: "foreign"}).canonical_bytes())
    _rejected(value)


async def test_complete_cannot_smuggle_requested_calls() -> None:
    value = await _fixture(complete=True)
    other = await _fixture()
    value = value.model_copy(
        update={
            "request": value.request.model_copy(
                update={
                    "ordered_calls": other.request.ordered_calls,
                }
            )
        }
    )
    value = _bind_run(value, value.captured_run)
    _rejected(value)


async def test_response_byte_budget_exact_limit_and_one_excess() -> None:
    value = await _fixture()
    size = len(base64.b64decode(value.request.canonical_response_base64))
    for maximum in (size, size - 1):
        changed = _bind_run(
            value,
            value.captured_run.model_copy(
                update={
                    "policy": value.captured_run.policy.model_copy(
                        update={"max_response_bytes": maximum}
                    ),
                }
            ),
        )
        if maximum == size:
            _accepted(changed)
        else:
            _rejected(changed)


@pytest.mark.parametrize("delivery", (False, True))
async def test_rehashed_artifact_cannot_substitute_registered_response_schema(
    delivery: bool,
) -> None:
    value = await _fixture(delivery=delivery)
    turn = value.captured_run.turns[-1]
    attempt = turn.attempts[-1]
    artifact = attempt.manifest.artifact.model_copy(update={"response_schema_json": "{}"})
    manifest = attempt.manifest.model_copy(update={"artifact": artifact})
    attempt = attempt.model_copy(update={"manifest": manifest})
    run = value.captured_run.model_copy(
        update={
            "turns": (turn.model_copy(update={"attempts": (attempt,)}),),
        }
    )
    _rejected(_bind_run(value, run))
