"""Public initialization consumers retain selected input preimages across the seam."""

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.execution_fan_out import fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import execution_initialization_contracts as init
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    FirstPublication,
    PreRootDecisionFence,
)


async def request() -> init.PrepareInboxExecution:
    captured = await fixture()
    run = captured.captured_run
    return init.PrepareInboxExecution(
        create=CreateExecutionRun(
            command_id="create",
            tenant=run.tenant,
            principal=run.principal,
            run_id=run.run_id,
            prompt=run.prompt,
            policy=run.policy,
            origin=run.origin,
            contour_head=run.contour_head,
            policy_head=run.policy_head,
            worker_session=run.worker_session,
        ),
        admitted=init.SelectedAdmittedRunInput(
            tenant_id=run.tenant,
            database_id="database",
            source_class="CLI",
            source_contract=head("contract"),
            token=head("token"),
            custody=head("custody"),
            inbox=head("inbox"),
            selected_decision=head("decision"),
            physical_record=head("physical"),
            commit_sequence=1,
            raw_input_bytes=b"\xff\x00raw",
            custody_schema="custody.v1",
            canonical_custody_record=b"\xffcustody",
            inbox_schema="inbox.v1",
            canonical_inbox_record=b"\x80inbox",
            source_authentication=head("authentication"),
            authentication_schema="auth.v1",
            canonical_authentication=b"\xffauth",
            normalization=head("normalization"),
            normalization_schema="normalize.v1",
            canonical_normalization_record=b"\x80normalize",
            normalized_prompt=run.prompt,
            principal_id=run.principal,
            contour_head=run.contour_head,
            origin=run.origin,
        ),
        cut=init.ExecutionInitializationCut(
            tenant_id=run.tenant,
            database_id="database",
            tenant_commit_sequence=1,
            materialization_commitment="a" * 64,
            initial_run_absence=Absent(),
            worker_session_id=run.worker_session,
            runtime_generation="runtime",
            authority_registry=head("registry"),
            sources=captured.request.cut.sources,
        ),
    )


async def test_admitted_input_roundtrip_preserves_original_bytes_and_origin() -> None:
    original = await request()
    restored: init.ExecutionInitializationRequest = TypeAdapter(
        init.ExecutionInitializationRequest
    ).validate_json(original.canonical_bytes())
    assert restored == original
    assert isinstance(restored, init.PrepareInboxExecution)
    assert restored.admitted.raw_input_bytes == b"\xff\x00raw"
    assert restored.admitted.canonical_authentication == b"\xffauth"
    assert restored.admitted.origin == original.create.origin
    with pytest.raises(ValidationError):
        CreateExecutionRun.model_validate_json(original.canonical_bytes())


@pytest.mark.parametrize(
    "field",
    [
        "canonical_custody_record",
        "canonical_inbox_record",
        "canonical_authentication",
        "canonical_normalization_record",
        "normalization_schema",
        "selected_decision",
    ],
)
async def test_admitted_input_requires_source_preimages_and_versions(field: str) -> None:
    wire = (await request()).admitted.model_dump()
    del wire[field]
    with pytest.raises(ValidationError):
        init.SelectedAdmittedRunInput.model_validate(wire)


@pytest.mark.parametrize(
    "source", ["PROVIDER_CALLBACK", "PROVIDER_POLL", "RECONCILIATION", "TOOL_RESULT"]
)
async def test_evidence_feedback_is_not_an_implicit_new_run(source: str) -> None:
    wire = (await request()).admitted.model_dump()
    wire["source_class"] = source
    with pytest.raises(ValidationError):
        init.SelectedAdmittedRunInput.model_validate(wire)


async def test_initial_creation_has_no_live_run_lease_requirement() -> None:
    cut = (await request()).cut
    assert isinstance(cut.initial_run_absence, Absent)
    assert (
        not {"run", "run_head", "live_lease", "fence"}
        & init.ExecutionInitializationCut.model_fields.keys()
    )
    fence = PreRootDecisionFence(
        command_id="scheduler-create",
        disposition=FirstPublication(
            decision=Absent(), expected_canonical_absence_manifest="absent-manifest"
        ),
        scheduler_authority_head="scheduler",
        broker_generation="broker",
        runtime_generation=cut.runtime_generation,
    )
    schema = init.SchedulerExecutionSource.model_json_schema()
    assert schema["properties"]["pre_root_fence"]["$ref"].endswith("/PreRootDecisionFence")
    assert PreRootDecisionFence.model_validate_json(fence.canonical_bytes()) == fence


async def test_prepared_initialization_returns_executable_run_and_original_binding() -> None:
    captured = await fixture()
    prepared = init.PreparedExecutionInitialization(
        source_request_fingerprint="a" * 64,
        run=captured.captured_run,
        input_binding=init.AdmittedExecutionBinding(
            original_inbox=head("inbox"),
            original_custody=head("custody"),
            normalization=head("normalization"),
        ),
        proposal_fingerprint="b" * 64,
    )
    restored: init.ExecutionInitializationResult = TypeAdapter(
        init.ExecutionInitializationResult
    ).validate_json(prepared.canonical_bytes())
    assert restored == prepared
    assert isinstance(restored, init.PreparedExecutionInitialization)
    assert restored.run.schema_id == "chiplog.agent-loop.execution-record.v2"
