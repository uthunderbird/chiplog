"""Independent transition verifier against all commands and rehashed owner mutations."""

import hashlib
import json

import pytest

from chiplog.adapters.driven.execution_prompts import render_execution_prompt
from chiplog.capabilities.agent_loop.contracts import DisclosureLabel, VisibilityMember
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionVisibilityManifest
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    AccumulateExecutionVisibility,
    ActivateExecutionRun,
    CaptureExecutionResponse,
    CreateExecutionRun,
    EmitExecutionAttempt,
    ExecutionTransitionProposal,
    ExecutionTransitionRequest,
    PrepareExecutionRequest,
    StartInitialExecutionTurn,
)
from chiplog.capabilities.agent_loop.execution_transitions import prepare_execution_transition
from chiplog.composition.r14_execution_transition_verification import verify_execution_transition
from tests.support.execution_fan_out import fixture


async def _history() -> list[tuple[ExecutionTransitionRequest, ExecutionTransitionProposal]]:
    seed = (await fixture()).captured_run
    history: list[tuple[ExecutionTransitionRequest, ExecutionTransitionProposal]] = []

    def apply(request: ExecutionTransitionRequest) -> ExecutionTransitionProposal:
        proposal = prepare_execution_transition(request)
        assert isinstance(proposal, ExecutionTransitionProposal), proposal
        history.append((request, proposal))
        return proposal

    created = apply(
        CreateExecutionRun(
            command_id="create",
            tenant=seed.tenant,
            principal=seed.principal,
            run_id=seed.run_id,
            prompt=seed.prompt,
            policy=seed.policy,
            origin=seed.origin,
            contour_head=seed.contour_head,
            policy_head=seed.policy_head,
            worker_session=seed.worker_session,
        )
    )
    active = apply(ActivateExecutionRun(command_id="activate", run=created.run))
    started = apply(StartInitialExecutionTurn(command_id="start", run=active.run)).run
    artifact = await render_execution_prompt("original context")
    label = DisclosureLabel(
        value="ENDPOINT_RESTRICTED", allowed_endpoints=(seed.origin.recipient.endpoint.identity,)
    )
    members = tuple(
        VisibilityMember(
            record_id=surface,
            revision_head=revision,
            content=content,
            provenance_head=started.head,
            label_head=started.policy_head,
            label=member_label,
            producer="fixture",
            surface=surface,
        )
        for surface, revision, content, member_label in (
            ("context", started.head, "original context", label),
            ("prompt", artifact.content_hash, artifact.rendered, label),
            (
                "schema",
                artifact.digest(),
                artifact.response_schema_json,
                DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
            ),
        )
    )
    accumulated = apply(
        AccumulateExecutionVisibility(command_id="accumulate", run=started, members=members)
    ).run
    manifest = ExecutionVisibilityManifest(
        tenant=started.tenant,
        principal=started.principal,
        run_id=started.run_id,
        turn_id=started.turns[-1].turn_id,
        contour_head=started.contour_head,
        generation=0,
        worker_session=started.worker_session,
        members=members,
        joined_label=label,
        artifact=artifact,
    )
    prepared = apply(
        PrepareExecutionRequest(command_id="prepare", run=accumulated, manifest=manifest)
    ).run
    emitted = apply(EmitExecutionAttempt(command_id="emit", run=prepared)).run
    apply(
        CaptureExecutionResponse(
            command_id="capture", run=emitted, raw=b"\xff\x00exact", receipt="transport-receipt"
        )
    )
    return history


async def test_all_registered_initial_transitions_verify() -> None:
    history = await _history()
    assert len(history) == 7
    for request, proposal in history:
        verify_execution_transition(request, proposal)
    assert history[-1][1].run.turns[-1].attempts[-1].state == "RESPONSE_CAPTURED"


@pytest.mark.parametrize("field", ["principal", "event", "predecessor", "state"])
async def test_resigned_owner_mutations_reject_for_every_transition(field: str) -> None:
    for request, proposal in await _history():
        verify_execution_transition(request, proposal)
        value = "SUCCEEDED" if field == "state" else "substituted"
        run = proposal.run.model_copy(update={field: value, "head": "pending"})
        run = run.model_copy(update={"head": "loop:" + run.digest()})
        changed = proposal.model_copy(update={"run": run})
        wire = json.loads(changed.canonical_bytes())
        del wire["proposal_fingerprint"]
        changed = changed.model_copy(
            update={
                "proposal_fingerprint": hashlib.sha256(
                    json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
            }
        )
        with pytest.raises(ValueError):
            verify_execution_transition(request, changed)
