"""Consumer checks for the acyclic scheduled service primitive/debit leaf."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal

import pytest
from pydantic import ValidationError
from tests.support.scheduler_service_acceptance import ServiceAcceptanceFixture, fixture

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    InitializedCallRecord,
)
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    call_subject_id,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ProposedScheduledMandateBudgetV1,
    ProposedScheduledMandateConsumptionV1,
    ScheduledMandateBudgetV1,
)
from chiplog.capabilities.agent_loop.scheduler_service_acceptance_contracts import (
    InitializedScheduledToolV1,
    PreparedScheduledServiceAcceptancePrimitiveV1,
    RetainedScheduledCallOriginProvenanceV1,
    RetainedScheduledInitializedCallV1,
    RetainedScheduledRunV1,
    ScheduledServiceAcceptancePrimitiveV1,
    scheduled_service_primitive_reference,
    scheduled_service_request_fingerprint,
    validate_scheduled_call_origin_sources,
    validate_scheduled_service_acceptance_preparation,
)


def _rebuild_initialized_source(
    value: ServiceAcceptanceFixture,
    records: tuple[InitializedCallRecord, ...],
    *,
    captured_response: CallSubjectHead | None = None,
) -> RetainedScheduledInitializedCallV1:
    request = value.request
    ordinal = request.initialized_record.call.original.ordinal
    references = tuple(call_record_reference(record.original_call_id, record) for record in records)
    seal = request.initialized_source.sealed_response.model_copy(
        update={
            "complete_ordered_initialized": references,
            "captured_response": captured_response
            or request.initialized_source.sealed_response.captured_response,
        }
    )
    seal_reference = call_record_reference(seal.response_seal_id, seal)
    record = records[ordinal]
    return RetainedScheduledInitializedCallV1(
        reference=references[ordinal],
        selected_decision=request.initialized_source.selected_decision,
        canonical_initialized_bytes=record.canonical_bytes(),
        record=record,
        complete_ordered_initialized_records=records,
        sealed_response_reference=seal_reference,
        canonical_sealed_response_bytes=seal.canonical_bytes(),
        sealed_response=seal,
    )


def _validate_sources(
    value: ServiceAcceptanceFixture,
    *,
    initialized_source: RetainedScheduledInitializedCallV1 | None = None,
    initialized_tool: InitializedScheduledToolV1 | None = None,
    call_origin_run: RetainedScheduledRunV1 | None = None,
) -> None:
    request = value.request
    validate_scheduled_call_origin_sources(
        command_id=request.command_id,
        scheduler_genesis_run=request.scheduler_genesis_run,
        scheduler_initialization=request.scheduler_initialization,
        call_origin_run=call_origin_run or request.call_origin_run,
        initialized_source=initialized_source or request.initialized_source,
        origin_provenance=request.origin_provenance,
        current_run=request.current_run,
        selected_mandate=request.selected_mandate,
        initialized_tool=initialized_tool or request.initialized_tool,
        cut=request.cut,
    )


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("history", ["GENESIS", "BORN_B", "INHERITED_A_B", "INHERITED_B_C"])
async def test_native_v2_and_v3_sources_roundtrip_to_one_pre_intent_primitive(
    version: Literal["v2", "v3"],
    history: Literal["GENESIS", "BORN_B", "INHERITED_A_B", "INHERITED_B_C"],
) -> None:
    value = await fixture(version, history=history)
    request = type(value.request).model_validate_json(value.request.canonical_bytes())
    prepared = type(value.prepared).model_validate_json(value.prepared.canonical_bytes())

    assert request.scheduler_genesis_run.schema_id.endswith(version)
    assert request.scheduler_initialization.canonical_initialization_bytes != (
        request.scheduler_initialization.canonical_preparation_bytes
    )
    assert prepared.primitive_reference.subject_id == request.initialized_record.original_call_id
    if history == "BORN_B":
        assert request.call_origin_run.run.run_id != request.scheduler_genesis_run.run.run_id
    if history == "INHERITED_A_B":
        assert request.call_origin_run.run.run_id != request.current_run.run.run_id
    validate_scheduled_service_acceptance_preparation(
        request, prepared, expected_tenant_id="tenant", expected_database_id="database"
    )


async def test_exact_debit_mutation_recomputes_nested_bytes_then_reaches_delta_join() -> None:
    value = await fixture("v2")
    baseline = json.loads(value.prepared.canonical_bytes())
    PreparedScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(baseline))

    mutated = json.loads(value.prepared.canonical_bytes())
    debit = mutated["proposed_call_debit"]
    debit["basis"]["delta_consequential_calls"] = 2
    successor = debit["successor"]["budget"]
    successor["cumulative_consequential_calls"] = 2
    successor["consumption_basis"] = debit["basis"]
    successor_body = ScheduledMandateBudgetV1.model_validate_json(json.dumps(successor))
    debit["successor"]["canonical_budget_bytes"] = base64.b64encode(
        successor_body.canonical_bytes()
    ).decode()

    with pytest.raises(ValidationError, match=r"exact \(0, 0, 1\)"):
        PreparedScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(mutated))


async def test_primitive_rejects_a_future_intent_field_in_python_and_json() -> None:
    value = await fixture("v3")
    primitive = value.prepared.primitive.model_dump()
    primitive["external_intent"] = value.request.current_observation
    with pytest.raises(ValidationError):
        ScheduledServiceAcceptancePrimitiveV1.model_validate(primitive)

    baseline = json.loads(value.prepared.primitive.canonical_bytes())
    ScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(baseline))
    baseline["final_acceptance"] = value.request.current_observation.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(baseline))


async def test_rehashed_wrong_native_run_head_reaches_native_head_join() -> None:
    value = await fixture("v3")
    retained = value.request.scheduler_genesis_run
    wrong_run = retained.run.model_copy(update={"head": "wrong-native-head"})
    raw = wrong_run.canonical_bytes()

    with pytest.raises(ValidationError, match="not native-derived"):
        RetainedScheduledRunV1(
            schema_id=wrong_run.schema_id,
            reference=retained.reference.model_copy(
                update={
                    "revision": retained.reference.revision.model_copy(
                        update={
                            "head": wrong_run.head,
                            "fingerprint": hashlib.sha256(raw).hexdigest(),
                        }
                    )
                }
            ),
            selected_decision=retained.selected_decision,
            canonical_run_bytes=raw,
            run=wrong_run,
        )


async def test_prepared_join_rejects_a_rehashed_wrong_budget_predecessor() -> None:
    value = await fixture("v2")
    baseline = json.loads(value.prepared.canonical_bytes())
    PreparedScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(baseline))

    mutated = json.loads(value.prepared.canonical_bytes())
    predecessor = mutated["proposed_call_debit"]["selected_predecessor"]
    predecessor["budget_head"]["head"] = "another-selected-budget"
    mutated["proposed_call_debit"]["basis"]["selected_predecessor"] = predecessor
    mutated["proposed_call_debit"]["successor"]["budget"]["predecessor"] = predecessor[
        "budget_head"
    ]
    mutated["proposed_call_debit"]["successor"]["budget"]["consumption_basis"] = mutated[
        "proposed_call_debit"
    ]["basis"]
    successor = ScheduledMandateBudgetV1.model_validate_json(
        json.dumps(mutated["proposed_call_debit"]["successor"]["budget"])
    )
    mutated["proposed_call_debit"]["successor"]["canonical_budget_bytes"] = base64.b64encode(
        successor.canonical_bytes()
    ).decode()

    with pytest.raises(ValidationError, match="selected predecessor differs"):
        PreparedScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(mutated))


@pytest.mark.parametrize(
    ("max_calls", "generation", "message"),
    [(0, 0, "exceeds mandate capacity"), (2, 2**64 - 1, "generation overflows")],
)
async def test_zero_capacity_and_generation_overflow_are_denied_before_publication(
    max_calls: int, generation: int, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        await fixture(
            "v2",
            max_consequential_calls=max_calls,
            predecessor_generation=generation,
        )


async def test_rehashed_cycle_delta_reaches_the_exact_debit_join() -> None:
    value = await fixture("v3")
    mutated = json.loads(value.prepared.canonical_bytes())
    debit = mutated["proposed_call_debit"]
    debit["basis"]["delta_cycles"] = 1
    successor = debit["successor"]["budget"]
    successor["cumulative_cycles"] = 1
    successor["consumption_basis"] = debit["basis"]
    successor_body = ScheduledMandateBudgetV1.model_validate_json(json.dumps(successor))
    debit["successor"]["canonical_budget_bytes"] = base64.b64encode(
        successor_body.canonical_bytes()
    ).decode()

    with pytest.raises(ValidationError, match=r"exact \(0, 0, 1\)"):
        PreparedScheduledServiceAcceptancePrimitiveV1.model_validate_json(json.dumps(mutated))


async def test_coordinated_foreign_original_run_rejects_before_a_debit_can_be_prepared() -> None:
    value = await fixture("v2")
    original = value.request.initialized_record.call.original.model_copy(
        update={"original_run_id": "foreign-original-run"}
    )
    call = value.request.initialized_record.call.model_copy(update={"original": original})
    provisional = value.request.initialized_record.model_copy(update={"call": call})
    call_id = "call:" + hashlib.sha256(original.canonical_bytes()).hexdigest()
    record = provisional.model_copy(update={"original_call_id": call_id})
    initialized = value.request.initialized.model_copy(
        update={
            "subject_id": call_id,
            "revision": Present(
                head="record:" + hashlib.sha256(record.canonical_bytes()).hexdigest(),
                fingerprint=hashlib.sha256(record.canonical_bytes()).hexdigest(),
            ),
        }
    )
    request = value.request.model_dump()
    request["initialized_record"] = record
    request["initialized"] = initialized

    with pytest.raises(ValidationError, match="initialized source differs"):
        type(value.request).model_validate(request)


async def test_current_observation_and_tool_permission_are_exact_registered_inputs() -> None:
    value = await fixture("v3")
    baseline = value.request.model_dump()
    type(value.request).model_validate(baseline)

    observation = value.request.current_observation.model_copy(
        update={"subject_id": "other-command"}
    )
    with pytest.raises(ValidationError, match="current observation command differs"):
        type(value.request).model_validate({**baseline, "current_observation": observation})

    permission = value.request.selected_tool_permission.model_copy(update={"consequential": False})
    with pytest.raises(ValidationError, match="not consequential"):
        ScheduledServiceAcceptancePrimitiveV1.model_validate(
            {
                **value.prepared.primitive.model_dump(),
                "selected_tool_permission": permission,
            }
        )


async def test_rehashed_wrong_captured_label_reaches_the_native_response_join() -> None:
    value = await fixture("v2")
    ordinal = value.request.initialized_record.call.original.ordinal
    record = value.request.initialized_source.complete_ordered_initialized_records[ordinal]
    original = record.call.original.model_copy(update={"model_call_label": "foreign-effect"})
    call = record.call.model_copy(update={"original": original})
    altered = record.model_copy(
        update={"original_call_id": call_subject_id(original), "call": call}
    )
    records = list(value.request.initialized_source.complete_ordered_initialized_records)
    records[ordinal] = altered
    source = _rebuild_initialized_source(value, tuple(records))

    with pytest.raises(ValueError, match="initialized call label differs"):
        _validate_sources(value, initialized_source=source)


async def test_rehashed_wrong_captured_call_bytes_reach_the_native_response_join() -> None:
    value = await fixture("v3")
    ordinal = value.request.initialized_record.call.original.ordinal
    record = value.request.initialized_source.complete_ordered_initialized_records[ordinal]
    call = record.call.model_copy(
        update={"canonical_call_base64": base64.b64encode(b"foreign-call").decode()}
    )
    altered = record.model_copy(update={"call": call})
    records = list(value.request.initialized_source.complete_ordered_initialized_records)
    records[ordinal] = altered
    source = _rebuild_initialized_source(value, tuple(records))

    with pytest.raises(ValueError, match="initialized call bytes differ"):
        _validate_sources(value, initialized_source=source)


async def test_reordered_full_initialized_inventory_reaches_captured_order_join() -> None:
    value = await fixture("v2")
    records = list(value.request.initialized_source.complete_ordered_initialized_records)
    records[0], records[1] = records[1], records[0]
    source = _rebuild_initialized_source(value, tuple(records))

    with pytest.raises(ValueError, match="initialized call ordinal differs"):
        _validate_sources(value, initialized_source=source)


async def test_wrong_registered_tool_identity_reaches_captured_tool_join() -> None:
    value = await fixture("v3")
    tool = value.request.initialized_tool.model_copy(update={"tool_name": "propose_intent"})

    with pytest.raises(ValueError, match="initialized tool name differs"):
        _validate_sources(value, initialized_tool=tool)


async def test_rehashed_same_root_foreign_capture_reaches_registered_provenance_join() -> None:
    value = await fixture("v2")
    retained = value.request.call_origin_run
    turn = retained.run.turns[-1]
    attempt = turn.attempts[turn.selector]
    assert attempt.response_base64 is not None
    foreign_raw = base64.b64decode(attempt.response_base64, validate=True).replace(
        b'"effect"', b'"foreign-effect"'
    )
    foreign_attempt = attempt.model_copy(
        update={"response_base64": base64.b64encode(foreign_raw).decode()}
    )
    foreign_turn = turn.model_copy(update={"attempts": (foreign_attempt,)})
    foreign_pending = retained.run.model_copy(update={"head": "pending", "turns": (foreign_turn,)})
    foreign_run = foreign_pending.model_copy(update={"head": "loop:" + foreign_pending.digest()})
    foreign_raw_run = foreign_run.canonical_bytes()
    foreign_origin = RetainedScheduledRunV1(
        schema_id=foreign_run.schema_id,
        reference=CallSubjectHead(
            subject_id=foreign_run.run_id,
            revision=Present(
                head=foreign_run.head,
                fingerprint=hashlib.sha256(foreign_raw_run).hexdigest(),
            ),
        ),
        selected_decision=retained.selected_decision,
        canonical_run_bytes=foreign_raw_run,
        run=foreign_run,
    )
    records = []
    for record in value.request.initialized_source.complete_ordered_initialized_records:
        original = record.call.original.model_copy(
            update={"captured_response": foreign_origin.reference}
        )
        if original.ordinal == value.request.initialized_record.call.original.ordinal:
            original = original.model_copy(update={"model_call_label": "foreign-effect"})
        canonical_call_base64 = record.call.canonical_call_base64
        if original.ordinal == value.request.initialized_record.call.original.ordinal:
            canonical_call_base64 = base64.b64encode(
                base64.b64decode(canonical_call_base64, validate=True).replace(
                    b'"effect"', b'"foreign-effect"'
                )
            ).decode()
        call = record.call.model_copy(
            update={
                "original": original,
                "canonical_call_base64": canonical_call_base64,
            }
        )
        records.append(
            record.model_copy(update={"original_call_id": call_subject_id(original), "call": call})
        )
    source = _rebuild_initialized_source(
        value,
        tuple(records),
        captured_response=foreign_origin.reference,
    )

    with pytest.raises(ValueError, match="provenance origin differs"):
        _validate_sources(value, initialized_source=source, call_origin_run=foreign_origin)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tenant_id", "foreign-tenant", "provenance tenant differs from cut"),
        ("tenant_commit_sequence", 2, "provenance sequence differs from cut"),
        ("materialization_commitment", "0" * 64, "provenance materialization differs from cut"),
    ],
)
async def test_rehashed_provenance_coordinates_reach_the_exact_current_cut_join(
    field: str, value: str | int, message: str
) -> None:
    fixture_value = await fixture("v2")
    body = fixture_value.request.origin_provenance.observation.model_copy(update={field: value})
    raw = body.canonical_bytes()
    wrapper = RetainedScheduledCallOriginProvenanceV1(
        source_reference=fixture_value.request.origin_provenance.source_reference.model_copy(
            update={
                "revision": Present(
                    head="scheduler-call-origin-v1:" + hashlib.sha256(raw).hexdigest(),
                    fingerprint=hashlib.sha256(raw).hexdigest(),
                )
            }
        ),
        canonical_source_bytes=raw,
        observation=body,
    )
    request = fixture_value.request.model_dump()
    request["origin_provenance"] = wrapper
    with pytest.raises(ValidationError, match=message):
        type(fixture_value.request).model_validate(request)


async def test_public_preparation_reparses_model_copy_mutants_before_joining() -> None:
    value = await fixture("v3")
    mutated = value.request.model_copy(update={"initialized": value.request.current_observation})
    with pytest.raises(ValidationError, match="selected initialized head differs"):
        validate_scheduled_service_acceptance_preparation(
            mutated,
            value.prepared,
            expected_tenant_id="tenant",
            expected_database_id="database",
        )


async def test_rehashed_database_swap_fails_against_independent_expected_context() -> None:
    value = await fixture("v2")
    body = value.request.origin_provenance.observation.model_copy(
        update={"database_id": "foreign-database"}
    )
    raw = body.canonical_bytes()
    provenance = RetainedScheduledCallOriginProvenanceV1(
        source_reference=value.request.origin_provenance.source_reference.model_copy(
            update={
                "revision": Present(
                    head="scheduler-call-origin-v1:" + hashlib.sha256(raw).hexdigest(),
                    fingerprint=hashlib.sha256(raw).hexdigest(),
                )
            }
        ),
        canonical_source_bytes=raw,
        observation=body,
    )
    request = type(value.request).model_validate(
        {
            **value.request.model_dump(),
            "database_id": "foreign-database",
            "origin_provenance": provenance,
        }
    )
    primitive = value.prepared.primitive.model_copy(
        update={
            "database_id": "foreign-database",
            "origin_provenance": provenance.source_reference,
        }
    )
    primitive_reference = scheduled_service_primitive_reference(primitive)
    basis = value.prepared.proposed_call_debit.basis.model_copy(
        update={
            "primitive_command": primitive_reference,
            "primitive_fingerprint": primitive_reference.revision.fingerprint,
        }
    )
    successor_budget = value.prepared.proposed_call_debit.successor.budget.model_copy(
        update={"consumption_basis": basis}
    )
    debit = ProposedScheduledMandateConsumptionV1(
        selected_predecessor=value.prepared.proposed_call_debit.selected_predecessor,
        successor=ProposedScheduledMandateBudgetV1(
            proposed_record_id=value.prepared.proposed_call_debit.successor.proposed_record_id,
            canonical_budget_bytes=successor_budget.canonical_bytes(),
            budget=successor_budget,
        ),
        basis=basis,
    )
    prepared = PreparedScheduledServiceAcceptancePrimitiveV1(
        source_request_fingerprint=scheduled_service_request_fingerprint(request),
        primitive=primitive,
        canonical_primitive_bytes=primitive.canonical_bytes(),
        primitive_reference=primitive_reference,
        proposed_call_debit=debit,
    )
    with pytest.raises(ValueError, match="request database differs from expected database"):
        validate_scheduled_service_acceptance_preparation(
            request,
            prepared,
            expected_tenant_id="tenant",
            expected_database_id="database",
        )
