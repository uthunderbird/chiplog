"""Retained transition envelope compatibility and closed H1 V3 dispatch."""

import hashlib
import json
from pathlib import Path

import pytest

from chiplog.adapters.driven.execution_prompts import render_execution_prompt
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionVisibilityManifest
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    AccumulateExecutionVisibility,
    CreateExecutionRun,
    ExecutionTransitionProposal,
    PrepareExecutionRequest,
)
from chiplog.capabilities.agent_loop.execution_transitions import prepare_execution_transition
from chiplog.capabilities.projections.r9_boundary import WorkspaceRejected
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_execution_transition_records import (
    ExecutionHistorySnapshot,
    RetainedExecutionTransition,
    RetainedExecutionTransitionV3,
    transition_command,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1WorkspaceIssuanceRefV1
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.platform.broker import BrokerSession
from tests.support.execution_fan_out import fixture


async def _prepare() -> tuple[PrepareExecutionRequest, ExecutionTransitionProposal]:
    seed = (await fixture()).captured_run

    def apply(request: object) -> ExecutionTransitionProposal:
        result = prepare_execution_transition(request)  # type: ignore[arg-type]
        assert isinstance(result, ExecutionTransitionProposal)
        return result

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
            worker_session="1:generation:session",
        )
    )
    from chiplog.capabilities.agent_loop.execution_transition_contracts import (
        ActivateExecutionRun,
        StartInitialExecutionTurn,
    )

    active = apply(ActivateExecutionRun(command_id="activate", run=created.run)).run
    started = apply(StartInitialExecutionTurn(command_id="start", run=active)).run
    artifact = await render_execution_prompt("workspace")
    label = DisclosureLabel(
        value="ENDPOINT_RESTRICTED", allowed_endpoints=(seed.origin.recipient.endpoint.identity,)
    )
    members = (
        VisibilityMember(
            record_id="workspace/batch",
            revision_head="batch",
            content="workspace",
            provenance_head=started.head,
            label_head=started.contour_head,
            label=label,
            producer="projections",
            surface="workspace",
        ),
        VisibilityMember(
            record_id="context",
            revision_head=started.head,
            content="workspace",
            provenance_head=started.head,
            label_head=started.contour_head,
            label=label,
            producer="fixture",
            surface="context",
        ),
        VisibilityMember(
            record_id="prompt",
            revision_head=artifact.content_hash,
            content=artifact.rendered,
            provenance_head=started.head,
            label_head=started.contour_head,
            label=label,
            producer="fixture",
            surface="prompt",
        ),
        VisibilityMember(
            record_id="schema",
            revision_head=artifact.digest(),
            content=artifact.response_schema_json,
            provenance_head=started.head,
            label_head=started.contour_head,
            label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
            producer="fixture",
            surface="schema",
        ),
    )
    accumulated = apply(
        AccumulateExecutionVisibility(command_id="accumulate", run=started, members=members)
    ).run
    request = PrepareExecutionRequest(
        command_id="prepare",
        run=accumulated,
        manifest=ExecutionVisibilityManifest(
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
        ),
    )
    return request, apply(request)


def _sessions(tenant: str, worker: str) -> tuple[BrokerSession, BrokerSession]:
    epoch, generation, session = worker.split(":", 2)
    callee = BrokerSession(
        tenant_id=tenant,
        broker_epoch=int(epoch),
        generation_id=generation,
        owner_id="agent_loop",
        session_id=session,
    )
    return (
        BrokerSession(
            tenant_id=tenant,
            broker_epoch=int(epoch),
            generation_id=generation,
            owner_id="broker",
            session_id="broker:" + generation,
        ),
        callee,
    )


async def test_v3_retained_envelope_accepts_only_native_prepare_and_changes_fingerprint() -> None:
    request, proposal = await _prepare()
    caller, callee = _sessions(request.run.tenant, request.run.worker_session)
    v2 = RetainedExecutionTransition(
        request=request,
        proposal=proposal,
        expected_head=4,
        predecessor_commitment="a" * 64,
        expected_snapshot_fingerprint=ExecutionHistorySnapshot(tenant_head=4, records=()).digest(),
        caller=caller,
        callee=callee,
        request_id="request",
        deadline_ns=1,
    )
    v3 = RetainedExecutionTransitionV3(
        request=request,
        proposal=proposal,
        expected_head=4,
        predecessor_commitment="a" * 64,
        expected_snapshot_fingerprint=ExecutionHistorySnapshot(tenant_head=4, records=()).digest(),
        caller=caller,
        callee=callee,
        request_id="request",
        deadline_ns=1,
        workspace_issuance=H1WorkspaceIssuanceRefV1(
            tenant=request.run.tenant,
            batch_id="batch",
            entry_id="b" * 64,
            payload_digest="c" * 64,
        ),
    )
    assert (
        RetainedExecutionTransition.model_validate_json(v2.canonical_bytes()).canonical_bytes()
        == v2.canonical_bytes()
    )
    assert transition_command(v3).request_fingerprint != transition_command(v2).request_fingerprint


async def test_v3_retained_envelope_rejects_non_prepare_request() -> None:
    request, _ = await _prepare()
    caller, callee = _sessions(request.run.tenant, request.run.worker_session)
    create = CreateExecutionRun(
        command_id="other",
        tenant=request.run.tenant,
        principal=request.run.principal,
        run_id="other",
        prompt="prompt",
        policy=request.run.policy,
        origin=request.run.origin,
        contour_head=request.run.contour_head,
        policy_head=request.run.policy_head,
        worker_session=request.run.worker_session,
    )
    proposal = prepare_execution_transition(create)
    assert isinstance(proposal, ExecutionTransitionProposal)
    evidence = RetainedExecutionTransitionV3(
        request=create,
        proposal=proposal,
        expected_head=0,
        predecessor_commitment="a" * 64,
        expected_snapshot_fingerprint=hashlib.sha256(b"snapshot").hexdigest(),
        caller=caller,
        callee=callee,
        request_id="request",
        deadline_ns=1,
        workspace_issuance=H1WorkspaceIssuanceRefV1(
            tenant=request.run.tenant, batch_id="batch", entry_id="b" * 64, payload_digest="c" * 64
        ),
    )
    with pytest.raises(ValueError, match="must prepare"):
        transition_command(evidence)


async def test_h1_issuance_append_failure_prevents_accumulate_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The native capture path cannot fall back to a V2 Prepare after H1 failure."""
    from chiplog.composition.r14_h1_workspace_issuance import H1WorkspaceIssuanceJournal

    def fail_append(self: H1WorkspaceIssuanceJournal, issuance: object) -> object:
        raise WorkspaceRejected("injected H1 issuance append failure")

    monkeypatch.setattr(H1WorkspaceIssuanceJournal, "append", fail_append)
    async with open_execution_runtime(tmp_path / "h1-append.sqlite") as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        with pytest.raises(WorkspaceRejected, match="injected H1 issuance"):
            await runtime.capture_execution("hermetic-ingress", "run", started.head)
        assert read_execution_history(runtime).records[-1] == started
        assert runtime._execution_model.requests == []


async def test_native_capture_selects_v3_prepare_and_reopens_original_workspace(
    tmp_path: Path,
) -> None:
    database = tmp_path / "h1-v3-prepare.sqlite"
    async with open_execution_runtime(database, responses=(b"{}",)) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        selected = [
            json.loads(raw)["execution_transition"]
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("kind") == "DECIDED"
            and "execution_transition" in json.loads(raw)
        ]
        retained = [
            RetainedExecutionTransitionV3.model_validate_json(raw)
            for raw in selected
            if json.loads(raw).get("kind") == "R14_SELECTED_EXECUTION_TRANSITION_V3"
        ]
        assert len(retained) == 1
        assert retained[0].request.run.predecessor == started.head
        assert read_execution_history(runtime).records[-1] == captured
    async with open_execution_runtime(database) as reopened:
        assert read_execution_history(reopened).records[-1] == captured
