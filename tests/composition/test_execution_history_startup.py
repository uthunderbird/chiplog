"""Semantic startup rejection after the trusted journal interface, before recovery."""

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.capabilities.agent_loop.execution_transition_contracts import (
    CreateExecutionRun,
    ExecutionTransitionProposal,
)
from chiplog.capabilities.agent_loop.execution_transitions import prepare_execution_transition
from chiplog.composition.r14_execution_transition_records import (
    EXECUTION_TRANSITION_OPERATION,
    RetainedExecutionTransition,
    transition_command,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r14_runtime import open_r14_runtime
from chiplog.platform._sqlite import EventAppender, PhysicalPublicationCommand, PublicationResult
from chiplog.platform.broker import BrokerSession
from tests.support.execution_fan_out import fixture


@pytest.mark.parametrize("damage", ["semantic", "operation", "schema"])
async def test_invalid_selected_execution_never_reaches_recovery_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    database = tmp_path / "execution.sqlite"
    template = (await fixture()).captured_run
    async with open_r14_runtime(database) as runtime:
        snapshot = read_execution_history(runtime)
        callee = runtime._supervisor.runtime().session("agent_loop")
        request = CreateExecutionRun(
            command_id="create",
            tenant=runtime._tenant_id,
            principal="hermetic-principal",
            run_id="execution",
            prompt="request",
            policy=template.policy,
            origin=template.origin,
            contour_head=template.contour_head,
            policy_head=template.policy_head,
            worker_session=runtime.current_worker(),
        )
        proposal = prepare_execution_transition(request)
        assert isinstance(proposal, ExecutionTransitionProposal)
        predecessor = runtime._commitment_journal.load(runtime._tenant_id)
        assert predecessor is not None
        retained = RetainedExecutionTransition(
            request=request,
            proposal=proposal,
            expected_head=snapshot.tenant_head,
            predecessor_commitment=predecessor,
            expected_snapshot_fingerprint=snapshot.digest(),
            request_id="owner-request",
            deadline_ns=10,
            caller=BrokerSession(
                tenant_id=callee.tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker",
            ),
            callee=callee,
        )
        command = transition_command(retained)
        if damage == "semantic":
            run = proposal.run.model_copy(update={"state": "SUCCEEDED", "head": "pending"})
            run = run.model_copy(update={"head": "loop:" + run.digest()})
            changed = proposal.model_copy(update={"run": run})
            body = json.loads(changed.canonical_bytes())
            del body["proposal_fingerprint"]
            changed = changed.model_copy(
                update={
                    "proposal_fingerprint": hashlib.sha256(
                        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                    ).hexdigest()
                }
            )
            retained = retained.model_copy(update={"proposal": changed})
        run = retained.proposal.run
        raw = run.canonical_bytes()
        injected = json.dumps(
            {
                "version": 1,
                "kind": "DECIDED",
                "operation_id": run.head,
                "operation_kind": "planning.disguised"
                if damage == "operation"
                else EXECUTION_TRANSITION_OPERATION,
                "expected_head": command.expected_head,
                "fingerprint": retained.digest(),
                "predecessor": predecessor,
                "resulting": "c" * 64,
                "records": [
                    {
                        "record_id": run.head,
                        "owner": "agent_loop",
                        "schema": "unknown" if damage == "schema" else command.records[0].schema_id,
                        "payload": base64.b64encode(raw).decode(),
                        "digest": hashlib.sha256(raw).hexdigest(),
                    }
                ],
                "execution_transition": retained.canonical_bytes().decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    actual_entries = IndependentTenantDecisionJournal.entries

    def corrupt_after_authentication(
        self: IndependentTenantDecisionJournal,
    ) -> tuple[tuple[str, str | None, bytes], ...]:
        rows = actual_entries(self)
        if self._path.name.endswith(".loop-journal"):
            return (*rows, ("injected-selected", rows[-1][0] if rows else None, injected))
        return rows

    async def forbidden(
        self: EventAppender, submitted: PhysicalPublicationCommand
    ) -> PublicationResult:
        raise AssertionError("invalid selected execution reached recovery submission")

    # Deliberately inject after journal authentication; this does not claim a MAC
    # bypass or a genuinely selected valid transaction/resulting commitment.
    with monkeypatch.context() as patch:
        patch.setattr(IndependentTenantDecisionJournal, "entries", corrupt_after_authentication)
        patch.setattr(EventAppender, "submit", forbidden)
        with pytest.raises(LoopIntegrityError):
            async with open_r14_runtime(database):
                raise AssertionError("invalid execution accepted on restart")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM records WHERE owner='agent_loop'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM publications WHERE operation_kind=?",
            (EXECUTION_TRANSITION_OPERATION,),
        ).fetchone() == (0,)
