"""Consumer checks for the closed automatic scheduler seed wire."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from pydantic import ValidationError
from tests.support.scheduler_execution import DIGEST, call_head, ordinary_seed_batch
from tests.support.scheduler_seed_producer import (
    ordinary_request,
    public_request,
    retained_source,
)

from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedPrimitiveFirstPublicationV2,
)
from chiplog.capabilities.agent_loop.scheduler_seed_producer_contracts import (
    PreparedSchedulerExecutionSeedV2,
    SchedulerCycleSourceObservationV2,
    automatic_command_identity,
    automatic_request_reference,
    decode_scheduler_cycle_source_row,
    overflow_primitive_reference,
    primitive_parent_reference,
    validate_prepared_overflow_primitive,
    validate_prepared_primitive_parent,
)


def test_public_native_source_and_closed_eleven_rows_roundtrip() -> None:
    request = public_request()
    source = retained_source()
    assert automatic_request_reference(request) == source.observation.request_reference
    assert automatic_command_identity(request) == source.observation.command
    assert type(source).model_validate_json(source.canonical_bytes()) == source
    assert len(source.observation.source_preimages) == 11
    assert tuple(
        decode_scheduler_cycle_source_row(item) for item in source.observation.source_preimages
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_fresh_ordinary_zero_one_and_many_cuts_preserve_absences(count: int) -> None:
    request = ordinary_request(count)
    assert type(request).model_validate_json(request.canonical_bytes()) == request
    assert (
        tuple(item.expected_run for item in request.cut.ordered_initial_run_absences)
        == (Absent(),) * count
    )


def test_valid_baseline_mutations_reach_closed_source_validator() -> None:
    baseline = retained_source().observation
    wrong_family = baseline.source_preimages[0].model_copy(update={"family": "MANDATE"})
    with pytest.raises(ValidationError, match="wrong-family"):
        SchedulerCycleSourceObservationV2.model_validate(
            baseline.model_dump()
            | {"source_preimages": (wrong_family, *baseline.source_preimages[1:])}
        )

    body = base64.b64decode(baseline.source_preimages[4].canonical_value_base64)
    invalid_body = baseline.source_preimages[4].model_copy(
        update={"canonical_value_base64": base64.b64encode(body + b" ").decode()}
    )
    with pytest.raises(ValidationError, match="noncanonical or hybrid"):
        SchedulerCycleSourceObservationV2.model_validate(
            baseline.model_dump()
            | {
                "source_preimages": (
                    *baseline.source_preimages[:4],
                    invalid_body,
                    *baseline.source_preimages[5:],
                )
            }
        )
    recursive = baseline.current_applicability.sources[0].model_copy(
        update={"source_id": "scheduler-cycle.actor"}
    )
    with pytest.raises(ValidationError, match="recursively"):
        SchedulerCycleSourceObservationV2.model_validate(
            baseline.model_dump()
            | {
                "current_applicability": baseline.current_applicability.model_copy(
                    update={"sources": (recursive,)}
                )
            }
        )


def test_source_rejects_selected_replay_revocation_and_recursive_applicability() -> None:
    source = retained_source().observation
    with pytest.raises(ValidationError, match="hold or revocation"):
        SchedulerCycleSourceObservationV2.model_validate(
            source.model_dump()
            | {
                "current_applicability": source.current_applicability.model_copy(
                    update={"current_revocation": Present(head="revoked", fingerprint=DIGEST)}
                )
            }
        )


def test_selected_boundary_and_clock_cross_joins_reject_valid_baseline_mutants() -> None:
    source = retained_source().observation
    bad_boundary = source.boundary.model_copy(
        update={
            "schedule_definition_head": source.boundary.schedule_definition_head.model_copy(
                update={"head": "foreign"}
            )
        }
    )
    with pytest.raises(ValidationError, match="boundary, mandate scope"):
        SchedulerCycleSourceObservationV2.model_validate(
            source.model_dump() | {"boundary": bad_boundary}
        )


def _recanonicalized_row(row: Any, body: Any) -> Any:
    raw = body.canonical_bytes()
    return row.model_copy(
        update={
            "source": body.selected_reference,
            "canonical_value_base64": base64.b64encode(raw).decode(),
        }
    )


def test_recanonicalized_policy_row_with_schedule_subject_rejects_native_join() -> None:
    source = retained_source().observation
    row = source.source_preimages[5]
    body = decode_scheduler_cycle_source_row(row)
    wrong = body.model_copy(
        update={
            "selected_reference": body.selected_reference.model_copy(
                update={"subject_id": "schedule"}
            )
        }
    )
    replacement = _recanonicalized_row(row, wrong)
    with pytest.raises(ValidationError, match="native configuration source join"):
        SchedulerCycleSourceObservationV2.model_validate(
            source.model_dump()
            | {
                "source_preimages": (
                    *source.source_preimages[:5],
                    replacement,
                    *source.source_preimages[6:],
                )
            }
        )


@pytest.mark.parametrize(
    ("index", "updates", "match"),
    [
        (
            6,
            {"bound_reference": Present(head="foreign-bound", fingerprint=DIGEST)},
            "native configuration",
        ),
        (
            8,
            {
                "proof": retained_source().observation.clock.proof.model_copy(
                    update={"command_id": "foreign"}
                )
            },
            "applicability/clock/cut/budget",
        ),
        (9, {"tenant_commit_sequence": 2}, "applicability/clock/cut/budget"),
        (10, {"canonical_budget_bytes": b"foreign-budget"}, "applicability/clock/cut/budget"),
    ],
)
def test_recanonicalized_registered_rows_reach_selected_projection_joins(
    index: int, updates: dict[str, Any], match: str
) -> None:
    source = retained_source().observation
    row = source.source_preimages[index]
    body: Any = decode_scheduler_cycle_source_row(row).model_copy(update=updates)
    if "bound_reference" in updates:
        body = body.model_copy(
            update={
                "selected_reference": body.selected_reference.model_copy(
                    update={"revision": body.bound_reference}
                )
            }
        )
    replacement = _recanonicalized_row(row, body)
    with pytest.raises(ValidationError, match=match):
        SchedulerCycleSourceObservationV2.model_validate(
            source.model_dump()
            | {
                "source_preimages": (
                    *source.source_preimages[:index],
                    replacement,
                    *source.source_preimages[index + 1 :],
                )
            }
        )


def test_prepared_ordinary_and_streamed_seed_results_are_closed_wires() -> None:
    ordinary = PreparedSchedulerExecutionSeedV2(
        source_request_fingerprint=DIGEST,
        seed_batch=ordinary_seed_batch(1),
        proposal_fingerprint=DIGEST,
    )
    assert type(ordinary).model_validate_json(ordinary.canonical_bytes()) == ordinary
    assert "unknown" not in type(ordinary).model_fields


def test_parent_helpers_keep_ordinary_and_resolution_separate() -> None:
    ordinary = ordinary_seed_batch().source
    assert isinstance(ordinary, PreparedPrimitiveFirstPublicationV2)
    parent = ordinary.primitive_parent
    prepared = ordinary.model_copy(
        update={
            "primitive_parent_reference": primitive_parent_reference(parent),
            "canonical_primitive_parent_bytes": parent.canonical_bytes(),
        }
    )
    assert validate_prepared_primitive_parent(prepared, expected_kind="ORDINARY") == parent
    resolution = parent.model_copy(
        update={
            "kind": "RESOLUTION",
            "original_hold": Present(head="hold", fingerprint=DIGEST),
            "operator_proof": Present(head="proof", fingerprint=DIGEST),
            "current_bound": parent.current_bound.model_copy(
                update={"generation": 1, "head_id": "new-bound"}
            ),
        }
    )
    prepared_resolution = prepared.model_copy(
        update={
            "primitive_parent": resolution,
            "primitive_parent_reference": primitive_parent_reference(resolution),
            "canonical_primitive_parent_bytes": resolution.canonical_bytes(),
        }
    )
    assert (
        validate_prepared_primitive_parent(prepared_resolution, expected_kind="RESOLUTION")
        == resolution
    )
    with pytest.raises(ValueError, match="kind"):
        validate_prepared_primitive_parent(prepared_resolution, expected_kind="ORDINARY")


@pytest.mark.parametrize("streaming", [False, True])
def test_full_and_streamed_overflow_have_distinct_parentless_primitives(streaming: bool) -> None:
    ordinary = ordinary_seed_batch()
    parent = ordinary.source
    assert isinstance(parent, PreparedPrimitiveFirstPublicationV2)
    from chiplog.capabilities.agent_loop.scheduler_contracts import (
        FullEligibilityEvidence,
        SchedulerEligibilityManifest,
        StreamingEligibilityEvidence,
    )

    evidence = (
        StreamingEligibilityEvidence(
            manifest_digest=DIGEST,
            member_count=1,
            first_member=Absent(),
            last_member=Absent(),
            order_contract_version="order",
            enumeration_completeness_proof=Present(head="proof", fingerprint=DIGEST),
        )
        if streaming
        else FullEligibilityEvidence(
            manifest=SchedulerEligibilityManifest(members=(), fingerprint=DIGEST)
        )
    )
    primitive = __import__(
        "chiplog.capabilities.agent_loop.scheduler_execution_contracts",
        fromlist=["OverflowHoldPrimitiveV2"],
    ).OverflowHoldPrimitiveV2(
        command=call_head("overflow"),
        boundary=parent.primitive_parent.boundary,
        bound_head=parent.primitive_parent.current_bound,
        exceeded_dimension="MEMBER_COUNT",
        actual_value=2,
        limit=1,
        evidence=evidence,
    )
    prepared = PreparedOverflowPrimitiveFirstPublicationV2(
        overflow_primitive=primitive,
        overflow_primitive_reference=overflow_primitive_reference(primitive),
        canonical_overflow_primitive_bytes=primitive.canonical_bytes(),
    )
    assert validate_prepared_overflow_primitive(prepared) == primitive
    assert b"primitive_parent" not in prepared.canonical_bytes()
