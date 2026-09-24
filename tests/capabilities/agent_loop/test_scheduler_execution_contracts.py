"""Closed V2 scheduler-execution consumer wires."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

import pytest
from pydantic import ValidationError
from tests.support.execution_versions import execution_run_v3
from tests.support.scheduler_execution import DIGEST, call_head, ordinary_seed_batch, present

from chiplog.capabilities.agent_loop import execution_initialization_contracts as v1_init
from chiplog.capabilities.agent_loop.execution_history_transition_contracts import (
    CreateExecutionRunV3,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_contracts import StreamingEligibilityEvidence
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ORDINARY_CHARGED_EDGES_V2,
    OVERFLOW_SAFETY_EDGES_V2,
    CompleteSchedulerExecutionDependencyManifestV2,
    FinalizeScheduledIntervalV2,
    InitialRunAbsenceV2,
    OverflowHoldPrimitiveV2,
    PreparedFinalizedScheduledIntervalV2,
    PreparedOverflowPrimitiveFirstPublicationV2,
    PreparedOverflowSeedBatchV2,
    PreparedPrimitiveFirstPublicationV2,
    PreparedScheduledExecutionInitializationV2,
    PreparedScheduledRunV2,
    PrepareScheduledIntervalExecutionsV2,
    ProposedScheduledWholeEnvelopeV2,
    SafetyHoldNoDebitAccountingV2,
    ScheduledExecutionBindingV2,
    ScheduledWholeIntervalEnvelopeV2,
    ScheduledWholeIntervalMemberDescriptorV2,
    SelectedExactDecisionV2,
    mandate_is_fresh_at,
)


@pytest.mark.parametrize("count", [0, 1, 3])
def test_ordinary_seed_batches_preserve_zero_one_or_n_subject_qualified_absences(
    count: int,
) -> None:
    batch = ordinary_seed_batch(count)
    assert type(batch).model_validate_json(batch.canonical_bytes()) == batch
    assert len(batch.occurrence_seeds) == count
    assert tuple(seed.initial_run_absence for seed in batch.occurrence_seeds) == (
        batch.cut.ordered_initial_run_absences
    )


def test_absence_fields_are_closed_and_mandate_keeps_zero_capacity_and_principal_split() -> None:
    batch = ordinary_seed_batch(1)
    absence = batch.cut.ordered_initial_run_absences[0].model_dump()
    del absence["subject"]
    with pytest.raises(ValidationError):
        InitialRunAbsenceV2.model_validate(absence)
    with pytest.raises(ValidationError):
        InitialRunAbsenceV2.model_validate(
            {**batch.cut.ordered_initial_run_absences[0].model_dump(), "unknown": "value"}
        )
    scope = batch.selected_mandate.mandate.scope.model_copy(
        update={
            "horizon": batch.selected_mandate.mandate.scope.horizon.model_copy(
                update={"max_cycles": 0, "max_runs": 0, "max_consequential_calls": 0}
            )
        }
    )
    assert type(scope).model_validate_json(scope.canonical_bytes()) == scope
    assert scope.service_identity != scope.beneficiary_principal
    assert mandate_is_fresh_at(scope.horizon, 99)
    assert not mandate_is_fresh_at(scope.horizon, 100)
    assert not mandate_is_fresh_at(scope.horizon, 101)


def overflow_batch() -> PreparedOverflowSeedBatchV2:
    ordinary = ordinary_seed_batch()
    assert isinstance(ordinary.source, PreparedPrimitiveFirstPublicationV2)
    primitive = OverflowHoldPrimitiveV2(
        command=call_head("overflow-command"),
        boundary=ordinary.source.primitive_parent.boundary,
        bound_head=ordinary.source.primitive_parent.current_bound,
        exceeded_dimension="MANIFEST_BYTES",
        actual_value=101,
        limit=100,
        evidence=StreamingEligibilityEvidence(
            manifest_digest=DIGEST,
            member_count=3,
            first_member=Absent(),
            last_member=Absent(),
            order_contract_version="order.v1",
            enumeration_completeness_proof=present("enumeration"),
        ),
    )
    return PreparedOverflowSeedBatchV2(
        configuration_source=ordinary.configuration_source,
        source=PreparedOverflowPrimitiveFirstPublicationV2(
            overflow_primitive=primitive,
            overflow_primitive_reference=present("overflow"),
            canonical_overflow_primitive_bytes=primitive.canonical_bytes(),
        ),
        cut=ordinary.cut,
        selected_mandate=ordinary.selected_mandate,
        selected_budget_predecessor=ordinary.selected_budget_predecessor,
        occurrence_seeds=(),
        skipped_dispositions=(),
    )


def test_streaming_overflow_source_has_no_ordinary_parent_anywhere() -> None:
    batch = overflow_batch()
    assert isinstance(batch.source, PreparedOverflowPrimitiveFirstPublicationV2)
    encoded = batch.canonical_bytes()
    assert b"primitive_parent" not in encoded
    assert batch.source.overflow_primitive.evidence.kind == "STREAMING_MANIFEST"
    malformed = json.loads(encoded)
    malformed["source"] = ordinary_seed_batch().source.model_dump(mode="json")
    with pytest.raises(ValidationError):
        PreparedOverflowSeedBatchV2.model_validate_json(json.dumps(malformed))


def test_seed_branch_and_overflow_cut_reject_valid_json_baseline_mutations() -> None:
    ordinary = ordinary_seed_batch()
    ordinary_wire = json.loads(ordinary.canonical_bytes())
    ordinary_wire["branch"] = "OVERFLOW_HOLD"
    with pytest.raises(ValidationError):
        type(ordinary).model_validate_json(json.dumps(ordinary_wire))

    overflow = overflow_batch()
    overflow_wire = json.loads(overflow.canonical_bytes())
    overflow_wire["cut"]["ordered_initial_run_absences"] = [
        ordinary_seed_batch(1).cut.ordered_initial_run_absences[0].model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="empty initial-Run absence"):
        PreparedOverflowSeedBatchV2.model_validate_json(json.dumps(overflow_wire))


def manifest(
    branch: Literal["ORDINARY_CHARGED", "OVERFLOW_CHARGED", "OVERFLOW_SAFETY_NO_DEBIT"],
    edges: tuple[tuple[str, tuple[str, ...]], ...],
) -> CompleteSchedulerExecutionDependencyManifestV2:
    return CompleteSchedulerExecutionDependencyManifestV2(branch=branch, registered_edges=edges)


def test_dependency_manifest_requires_exact_budget_branch_edges_without_backedge() -> None:
    value = manifest("ORDINARY_CHARGED", ORDINARY_CHARGED_EDGES_V2)
    assert ("budget_basis", ("selected_budget", "primitive_identity")) in value.registered_edges
    changed = list(ORDINARY_CHARGED_EDGES_V2)
    changed[-1] = ("whole_envelope", ("result",))
    with pytest.raises(ValidationError, match="exact registered edge"):
        manifest("ORDINARY_CHARGED", tuple(changed))
    with pytest.raises(ValidationError, match="exact registered edge"):
        manifest("ORDINARY_CHARGED", ORDINARY_CHARGED_EDGES_V2[:-1])


def test_finalize_request_is_input_only_and_result_carries_produced_members() -> None:
    overflow = overflow_batch()
    request = FinalizeScheduledIntervalV2(
        seed_batch=overflow,
        ordered_prepared_runs=(),
        accounting=SafetyHoldNoDebitAccountingV2(
            retained_selected_budget_predecessor=overflow.selected_budget_predecessor,
            proposed_successor=Absent(),
            delta_cycles=0,
            delta_runs=0,
            delta_consequential_calls=0,
        ),
        dependency_manifest=manifest("OVERFLOW_SAFETY_NO_DEBIT", OVERFLOW_SAFETY_EDGES_V2),
    )
    assert "outcome" not in type(request).model_fields
    assert PreparedFinalizedScheduledIntervalV2.model_fields["outcome"].is_required()


def test_strict_zero_rejects_python_and_json_booleans() -> None:
    overflow = overflow_batch()
    raw = SafetyHoldNoDebitAccountingV2(
        retained_selected_budget_predecessor=overflow.selected_budget_predecessor,
        proposed_successor=Absent(),
        delta_cycles=0,
        delta_runs=0,
        delta_consequential_calls=0,
    ).model_dump(mode="json")
    raw["delta_cycles"] = True
    with pytest.raises(ValidationError):
        SafetyHoldNoDebitAccountingV2.model_validate(raw)
    with pytest.raises(ValidationError):
        SafetyHoldNoDebitAccountingV2.model_validate_json(json.dumps(raw))


def test_selected_replay_retains_bytes_and_v2_initialization_does_not_widen_v1() -> None:
    replay = SelectedExactDecisionV2(
        selected_decision=present("decision"),
        canonical_selected_decision_bytes=b"decision",
        canonical_selected_result_bytes=b"result",
        exact_prefix_materialization=present("prefix"),
        selected_debit=Absent(),
        canonical_selected_debit_bytes=None,
    )
    assert replay.canonical_selected_result_bytes == b"result"
    assert "PREPARED_SCHEDULED_EXECUTION_INITIALIZATION_V2" not in str(
        v1_init.ExecutionInitializationResult
    )
    assert PreparedScheduledExecutionInitializationV2.model_fields["run"].is_required()


def test_scheduler_v3_create_and_prepared_run_roundtrip_and_version_mismatch_reject() -> None:
    run = execution_run_v3()
    batch = ordinary_seed_batch(1)
    command = CreateExecutionRunV3(
        command_id="create-v3",
        tenant=run.tenant,
        principal=run.principal,
        run_id=run.run_id,
        prompt=run.prompt,
        policy=run.policy,
        origin=run.origin,
        contour_head=run.contour_head,
        policy_head=run.policy_head,
        worker_session=run.worker_session,
    )
    prepared = PreparedScheduledRunV2(
        command=command,
        initialization=PreparedScheduledExecutionInitializationV2(
            source_request_fingerprint=DIGEST,
            run=run,
            scheduled_binding=ScheduledExecutionBindingV2(
                service_identity=batch.selected_mandate.mandate.scope.service_identity,
                mandate_head=batch.selected_mandate.mandate_head,
                primitive_parent=present("parent"),
                materialization=batch.occurrence_seeds[0].materialization,
                original_configuration=batch.configuration_source.configuration,
            ),
            proposal_fingerprint=DIGEST,
        ),
    )
    assert type(prepared).model_validate_json(prepared.canonical_bytes()) == prepared
    mutant = json.loads(prepared.canonical_bytes())
    mutant["command"]["kind"] = "CREATE_EXECUTION_RUN_V2"
    with pytest.raises(ValidationError, match="version differ"):
        PreparedScheduledRunV2.model_validate_json(json.dumps(mutant))

    request_command = command.model_copy(update={"run_id": "run-0"})
    request = PrepareScheduledIntervalExecutionsV2(
        seed_batch=batch, ordered_create_commands=(request_command,)
    )
    assert type(request).model_validate_json(request.canonical_bytes()) == request


def test_whole_envelope_body_and_proposal_reject_descriptor_and_byte_mutations() -> None:
    batch = ordinary_seed_batch()
    descriptor = ScheduledWholeIntervalMemberDescriptorV2(
        record_kind="scheduler.interval-result",
        subject_id="command",
        record_id="result",
        schema_id="chiplog.scheduler.interval-result.v2",
        fingerprint=DIGEST,
    )
    body = ScheduledWholeIntervalEnvelopeV2(
        tenant_id="tenant",
        interval_command=call_head("command"),
        branch=batch.branch,
        primitive_reference=present("parent"),
        selected_configuration=batch.configuration_source.configuration,
        selected_mandate=batch.selected_mandate.mandate_head,
        selected_applicability=call_head("applicability"),
        selected_budget_predecessor=batch.selected_budget_predecessor.budget_head,
        budget_successor=Absent(),
        selected_clock_cut_fingerprint=DIGEST,
        outcome=present("result"),
        ordered_non_envelope_members=(descriptor,),
        expected_ordinary_seed_count=0,
        ordered_occurrence_materializations=(),
        resulting_boundary="10",
        dependency_manifest_fingerprint=DIGEST,
    )
    payload = body.canonical_bytes()
    proposal = ProposedScheduledWholeEnvelopeV2(
        proposed_record_id="whole",
        body=body,
        canonical_envelope_bytes=payload,
        external_reference=present_with_fingerprint("whole", payload),
    )
    assert type(proposal).model_validate_json(proposal.canonical_bytes()) == proposal
    wire = json.loads(body.canonical_bytes())
    wire["ordered_non_envelope_members"].append(descriptor.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        ScheduledWholeIntervalEnvelopeV2.model_validate_json(json.dumps(wire))
    with pytest.raises(ValidationError, match="canonical body"):
        ProposedScheduledWholeEnvelopeV2(
            proposed_record_id="whole",
            body=body,
            canonical_envelope_bytes=b"wrong",
            external_reference=present("whole"),
        )


def present_with_fingerprint(name: str, payload: bytes) -> Present:
    return Present(head=name, fingerprint=hashlib.sha256(payload).hexdigest())
