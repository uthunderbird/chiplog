"""Preparation consumer checks, not authentication or durable invocation evidence."""

import json

import pytest
from tests.support.execution_fan_out import fixture

from chiplog.adapters.driven.execution_prompts import render_execution_prompt
from chiplog.capabilities.agent_loop.contracts import (
    DisclosureLabel,
    LoopRejected,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.execution_attempts import (
    capture_execution_response,
    emit_execution_attempt,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionRunRecord,
    ExecutionVisibilityManifest,
)
from chiplog.capabilities.agent_loop.execution_lifecycle import (
    accumulate_execution_visibility,
    activate_execution_run,
    create_ingress_execution_run,
    start_initial_execution_turn,
)
from chiplog.capabilities.agent_loop.execution_preparation import (
    execution_context_label,
    prepare_execution_request,
)


@pytest.fixture
async def inputs() -> tuple[ExecutionRunRecord, ExecutionVisibilityManifest]:
    seed = await fixture()
    template = seed.captured_run
    created = create_ingress_execution_run(
        tenant=template.tenant,
        principal=template.principal,
        run_id=template.run_id,
        prompt=template.prompt,
        policy=template.policy,
        origin=template.origin,
        contour_head=template.contour_head,
        policy_head=template.policy_head,
        worker_session=template.worker_session,
    )
    run = start_initial_execution_turn(activate_execution_run(created))
    artifact = await render_execution_prompt("original context")
    label = execution_context_label(run)
    members = tuple(
        VisibilityMember(
            record_id=surface,
            revision_head=revision,
            content=content,
            provenance_head=run.head,
            label_head="label",
            label=member_label,
            producer="fixture",
            surface=surface,
        )
        for surface, revision, content, member_label in (
            ("context", run.head, "original context", label),
            ("prompt", artifact.content_hash, artifact.rendered, label),
            (
                "schema",
                artifact.digest(),
                artifact.response_schema_json,
                DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
            ),
        )
    )
    run = accumulate_execution_visibility(run, members)
    manifest = ExecutionVisibilityManifest(
        tenant=run.tenant,
        principal=run.principal,
        contour_head=run.contour_head,
        run_id=run.run_id,
        turn_id=run.turns[-1].turn_id,
        generation=0,
        worker_session=run.worker_session,
        members=members,
        joined_label=label,
        artifact=artifact,
    )
    return run, manifest


def test_preparation_emission_capture_preserves_original_request(
    inputs: tuple[ExecutionRunRecord, ExecutionVisibilityManifest],
) -> None:
    run, manifest = inputs
    prepared = prepare_execution_request(run, manifest)
    attempt = prepared.turns[-1].attempts[-1]
    assert attempt.state == "PREPARED_NOT_EMITTED"
    assert json.loads(attempt.request) == {
        "prompt": manifest.artifact.rendered,
        "schema": manifest.artifact.response_schema_json,
        "manifest": manifest.digest(),
    }
    assert attempt.lineage_id == run.turns[-1].turn_id + "/slot/0"
    with pytest.raises(LoopRejected):
        prepare_execution_request(prepared, manifest)
    emitted = emit_execution_attempt(prepared)
    captured = capture_execution_response(emitted, b"\xffexact", "receipt")
    assert captured.turns[-1].attempts[-1].request == attempt.request
    assert captured.turns[-1].attempts[-1].manifest == manifest
    assert captured.turns[-1].initialized_calls is None


def test_initialization_cannot_be_reused_for_continuation_or_unknown_attempt(
    inputs: tuple[ExecutionRunRecord, ExecutionVisibilityManifest],
) -> None:
    run, manifest = inputs
    for current in (
        run,
        prepare_execution_request(run, manifest),
        emit_execution_attempt(prepare_execution_request(run, manifest)),
    ):
        with pytest.raises(LoopRejected):
            activate_execution_run(current)
        with pytest.raises(LoopRejected):
            start_initial_execution_turn(current)
    with pytest.raises(LoopRejected, match="sealed"):
        accumulate_execution_visibility(prepare_execution_request(run, manifest), manifest.members)
    changed = manifest.members[0].model_copy(update={"content": "rebound"})
    with pytest.raises(LoopRejected, match="rebound"):
        accumulate_execution_visibility(run, (changed,))


@pytest.mark.parametrize(
    "mutation", ["worker", "members", "label", "schema", "duplicate", "context"]
)
def test_incomplete_or_substituted_visibility_rejects(
    inputs: tuple[ExecutionRunRecord, ExecutionVisibilityManifest],
    mutation: str,
) -> None:
    run, manifest = inputs
    if mutation == "worker":
        manifest = manifest.model_copy(update={"worker_session": "other"})
    elif mutation == "members":
        manifest = manifest.model_copy(update={"members": manifest.members[:-1]})
    elif mutation == "label":
        manifest = manifest.model_copy(
            update={"joined_label": DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())}
        )
    elif mutation == "schema":
        manifest = manifest.model_copy(
            update={"artifact": manifest.artifact.model_copy(update={"response_schema_json": "{}"})}
        )
    else:
        members = (
            (*manifest.members, manifest.members[0])
            if mutation == "duplicate"
            else (
                manifest.members[0].model_copy(update={"provenance_head": "other"}),
                *manifest.members[1:],
            )
        )
        manifest = manifest.model_copy(update={"members": members})
        turn = run.turns[-1].model_copy(update={"accumulator": members})
        run = run.model_copy(update={"head": "pending", "turns": (turn,)})
        run = run.model_copy(update={"head": "loop:" + run.digest()})
    with pytest.raises(LoopRejected):
        prepare_execution_request(run, manifest)


def test_request_transport_bound_counts_complete_serialized_manifest_reference(
    inputs: tuple[ExecutionRunRecord, ExecutionVisibilityManifest],
) -> None:
    run, manifest = inputs
    size = len(prepare_execution_request(run, manifest).turns[-1].attempts[-1].request.encode())
    for limit in (size, size - 1):
        bounded = run.model_copy(
            update={
                "head": "pending",
                "policy": run.policy.model_copy(update={"max_request_bytes": limit}),
            }
        )
        bounded = bounded.model_copy(update={"head": "loop:" + bounded.digest()})
        if limit == size:
            prepare_execution_request(bounded, manifest)
        else:
            with pytest.raises(LoopRejected, match="transport bound"):
                prepare_execution_request(bounded, manifest)
