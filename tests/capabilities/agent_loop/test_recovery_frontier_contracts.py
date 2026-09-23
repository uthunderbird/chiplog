"""Consumer-visible branch closure; runtime enumeration is tested separately."""

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CallRecoveryFrontier,
    ClosedBeforeTransition,
    ObligationObservation,
    RecoveryCommand,
    ReferenceExternalObligation,
    TerminalResult,
)


def _head() -> dict[str, str]:
    return {"kind": "PRESENT", "head": "exact-head", "fingerprint": "a" * 64}


def _obligation() -> dict[str, Any]:
    return {
        "original_run_id": "original-run",
        "original_call_id": "original-call",
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
        "evidence_head": {"kind": "ABSENT"},
    }


def _result(kind: str) -> dict[str, Any]:
    if kind == "RECOVERY_REQUIRED":
        return {
            "kind": kind,
            "terminal": _head(),
            "obligation": _obligation(),
            "result": {"kind": "ABSENT"},
            "recovered_outcome": {
                "original_call_id": "original-call",
                "recovery_obligation_id": "obligation",
                "outcome": {"kind": "ABSENT"},
            },
        }
    result: dict[str, Any] = {
        "kind": kind,
        "terminal": _head(),
        "recovered_outcome": {"kind": "NOT_APPLICABLE"},
    }
    if kind == "CANCELLED_BEFORE_ACCEPT":
        result.update(not_executed_result=_head(), initialized_predecessor=_head())
    else:
        result["result"] = _head()
    if kind == "OUTCOME_UNKNOWN":
        result["no_retry_boundary"] = _head()
    return result


@pytest.mark.parametrize(
    "kind",
    [
        "SUCCEEDED",
        "FAILED_DEFINITE",
        "OUTCOME_UNKNOWN",
        "CANCELLED_BEFORE_ACCEPT",
        "RECOVERY_REQUIRED",
    ],
)
def test_public_terminal_branch_retains_its_exact_result_form(kind: str) -> None:
    adapter: TypeAdapter[TerminalResult] = TypeAdapter(TerminalResult)
    result = adapter.validate_python(_result(kind))
    assert adapter.validate_json(result.canonical_bytes()) == result
    with pytest.raises(ValidationError):
        adapter.validate_python({**_result(kind), "unknown_result": _head()})


def test_recovery_required_cannot_replace_obligation_with_success_or_na_outcome() -> None:
    result = _result("RECOVERY_REQUIRED")
    for replacement in (_head(), {"kind": "NOT_APPLICABLE"}):
        with pytest.raises(ValidationError):
            TypeAdapter(TerminalResult).validate_python({**result, "result": replacement})
    with pytest.raises(ValidationError):
        TypeAdapter(TerminalResult).validate_python(
            {
                **result,
                "recovered_outcome": {"kind": "NOT_APPLICABLE"},
            }
        )


def test_terminal_cannot_carry_pending_branch_fields() -> None:
    value = {
        "kind": "TERMINAL",
        "original_call_id": "original-call",
        "response_id": "response",
        "acceptance": {"kind": "INITIALIZED", "initialized": _head()},
        "disposition": _result("CANCELLED_BEFORE_ACCEPT"),
    }
    adapter: TypeAdapter[CallRecoveryFrontier] = TypeAdapter(CallRecoveryFrontier)
    assert adapter.validate_python(value).kind == "TERMINAL"
    with pytest.raises(ValidationError):
        adapter.validate_python({**value, "pending": _head()})


def test_original_obligation_cannot_be_transferred_to_successor() -> None:
    adapter: TypeAdapter[ObligationObservation] = TypeAdapter(ObligationObservation)
    for kind in ("REFERENCE_EXTERNAL", "CLOSED_BEFORE_TRANSITION"):
        value: dict[str, Any] = {
            "kind": kind,
            "original": _obligation(),
            "observation_frontier": 1,
        }
        if kind == "CLOSED_BEFORE_TRANSITION":
            value["terminal"] = _head()
        parsed = adapter.validate_python(value)
        assert isinstance(parsed, (ReferenceExternalObligation, ClosedBeforeTransition))
        assert adapter.validate_json(parsed.canonical_bytes()) == parsed
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "kind": "TRANSFER_OPEN",
                "original": _obligation(),
                "observation_frontier": 1,
            }
        )


def test_recovery_command_surface_has_only_resume_and_successor() -> None:
    schema = TypeAdapter(RecoveryCommand).json_schema()
    assert set(schema["discriminator"]["mapping"]) == {"RESUME", "SUCCESSOR"}
    with pytest.raises(ValidationError):
        TypeAdapter(RecoveryCommand).validate_python({"kind": "PARTIAL_REPLAY"})


def _pending() -> dict[str, Any]:
    return {
        "kind": "READ_ONLY_RETRY_PENDING",
        "original_call_id": "original-call",
        "response_id": "response",
        "pending": _head(),
        "terminal": {"kind": "ABSENT"},
        "call_outcome": {"kind": "ABSENT"},
        "initialized": _head(),
        "lineage": {
            "lineage_id": "readonly-lineage",
            "original_call_id": "original-call",
            "max_attempts": 2,
            "budget_version": "budget.v1",
            "reducer_id": "reducer",
            "reducer_version": "reducer.v1",
        },
        "ordered_attempts": (
            {
                "ordinal": 0,
                "attempt_id": "attempt0",
                "initialized": _head(),
                "accepted": _head(),
                "outcome": _head(),
                "result_or_obligation": _head(),
                "predecessor": {"kind": "ABSENT"},
            },
        ),
        "last_retryable_failure": _head(),
        "counter": _head(),
        "attempts_consumed": 1,
        "next_ordinal": 1,
        "readonly_proof": _head(),
        "snapshot": _head(),
        "execution_binding": {"kind": "NOT_APPLICABLE"},
        "crossed_binding_heads": (),
        "closure_registry_id": "closure-registry",
        "closure_registry_version": "closure.v1",
    }


def test_pending_branch_roundtrips_and_cannot_hide_counter_or_publish_terminal() -> None:
    adapter: TypeAdapter[CallRecoveryFrontier] = TypeAdapter(CallRecoveryFrontier)
    value = _pending()
    parsed = adapter.validate_python(value)
    assert adapter.validate_json(parsed.canonical_bytes()) == parsed
    for omitted in ("counter", "ordered_attempts", "lineage", "last_retryable_failure"):
        with pytest.raises(ValidationError):
            adapter.validate_python({key: item for key, item in value.items() if key != omitted})
    with pytest.raises(ValidationError):
        adapter.validate_python({**value, "terminal": _head()})


def test_readonly_terminal_requires_complete_original_lineage() -> None:
    pending = _pending()
    acceptance = {
        "kind": "READ_ONLY_ACCEPTED",
        "initialized": _head(),
        "accepted": _head(),
        "lineage_id": "readonly-lineage",
        "ordinal": 0,
        "readonly_proof": _head(),
        "snapshot_binding": _head(),
        "lineage": pending["lineage"],
        "complete_ordered_attempts": pending["ordered_attempts"],
        "shared_counter": _head(),
        "attempts_consumed": 1,
        "call_level_outcome": _head(),
        "complete_lineage_reducer_batch": _head(),
    }
    value = {
        "kind": "TERMINAL",
        "original_call_id": "original-call",
        "response_id": "response",
        "acceptance": acceptance,
        "disposition": _result("FAILED_DEFINITE"),
    }
    adapter: TypeAdapter[CallRecoveryFrontier] = TypeAdapter(CallRecoveryFrontier)
    assert adapter.validate_python(value).kind == "TERMINAL"
    for omitted in (
        "complete_ordered_attempts",
        "shared_counter",
        "complete_lineage_reducer_batch",
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(
                {
                    **value,
                    "acceptance": {k: v for k, v in acceptance.items() if k != omitted},
                }
            )


@pytest.mark.parametrize("kind", ["RESUME", "SUCCESSOR"])
def test_public_recovery_commands_retain_full_frontier_and_original_obligations(kind: str) -> None:
    frontier = {
        "tenant_id": "tenant",
        "run_id": "original-run",
        "tenant_commit_sequence": 1,
        "registry": {
            "registry_id": "registry",
            "version": "registry.v1",
            "fingerprint": "a" * 64,
            "ordered_rows": (
                {
                    "family": "CALL",
                    "ordinal": 0,
                    "subject_extractor_id": "sealed-calls",
                    "cardinality_rule": "one",
                    "terminal_conflict_rule": "closed",
                    "serialization_rule": "registry-order",
                    "canonicalization_version": "v1",
                },
            ),
        },
        "ordered_members": (
            {
                "family": "CALL",
                "subject": "original-call",
                "branch": "READ_ONLY_RETRY_PENDING",
                "ordered_heads": (_head(),),
                "fingerprint": "a" * 64,
            },
        ),
        "ordered_calls": (_pending(),),
        "canonicalization_version": "chiplog.recovery.frontier.v1",
        "fingerprint": "a" * 64,
    }
    baseline = {
        "baseline_id": "baseline",
        "suspended_run_head": _head(),
        "frontier": frontier,
        "bindings": {
            "objective": "objective",
            "requested_work": "requested",
            "prompt_artifact": _head(),
            "ordered_tool_specs": (_head(),),
            "generated_schema": _head(),
            "semantic_bindings": (),
            "recipient_effect_bindings": (),
            "authority_scope": _head(),
            "authority_mandate_heads": (),
            "policy": _head(),
            "no_retry_boundaries": (_head(),),
        },
        "original_obligations": (_obligation(),),
        "activation_blocking_predicates": (_head(),),
        "fingerprint": "a" * 64,
    }
    value: dict[str, Any] = {
        "kind": kind,
        "identity": {
            "tenant_id": "tenant",
            "command_id": "command",
            "payload_fingerprint": "a" * 64,
            "schema_version": "chiplog.recovery.command.v1",
        },
        "expected_suspended_head": _head(),
        "baseline": baseline,
        "observed_frontier": frontier,
        "disposition_version": "disposition.v1",
        "fence": {
            "kind": "NON_SCHEDULER_NOT_APPLICABLE",
            **{
                key: {"kind": "NOT_APPLICABLE"}
                for key in ("lineage", "physical_root", "lease", "clock_proof")
            },
            "run_id": "original-run",
            "run_head": "head",
            "worker_session_id": "worker",
            "runtime_generation": "generation",
        },
    }
    if kind == "RESUME":
        value.update(run_id="original-run", activation_payload="activation")
    else:
        value.update(
            predecessor_run_id="original-run",
            changed_binding_manifest=(_head(),),
            successor_run_id="successor",
            successor_initialization_manifest=(_head(),),
            original_obligation_observations=(
                {
                    "kind": "REFERENCE_EXTERNAL",
                    "original": _obligation(),
                    "observation_frontier": 1,
                },
            ),
            inherited_no_retry_boundaries=(_head(),),
        )
    adapter: TypeAdapter[RecoveryCommand] = TypeAdapter(RecoveryCommand)
    parsed = adapter.validate_python(value)
    assert adapter.validate_json(parsed.canonical_bytes()) == parsed
    # These are proposed observations only: a pending branch cannot authorize
    # resume. That semantic rejection belongs to the owner/writer tests.
    for omitted in ("baseline", "observed_frontier", "expected_suspended_head", "fence"):
        with pytest.raises(ValidationError):
            adapter.validate_python({key: item for key, item in value.items() if key != omitted})
