from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.composition.r10 import r10_component
from chiplog.platform._sqlite import LostCommitAcknowledgement
from tests.support.evidence_journal import confirm_transport, fixture, rows


async def test_lost_ack_and_restart(tmp_path: Path) -> None:
    identity, command = fixture()
    path = tmp_path / "db"
    async with r10_component(path, {"peer": identity}) as component:
        component.storage.fault = "after_commit"
        with pytest.raises(LostCommitAcknowledgement):
            await component.journal.execute("peer", command)
    async with r10_component(path, {"peer": identity}) as component:
        assert (await component.journal.execute("peer", command)).disposition == "REPLAY"
        assert len(rows(path)) == 1


async def test_existing_real_plan_bytes_survive_all_journal_actions(tmp_path: Path) -> None:
    from chiplog.capabilities.planning import CreateIntentionLine
    from chiplog.composition.r6 import open_r6_runtime
    from chiplog.domain_primitives import RecordId, TenantId

    path = tmp_path / "db"
    tenant = TenantId("tenant")
    async with open_r6_runtime(path, operator_secret=b"r10-component-operator") as runtime:
        await runtime.bootstrap(
            tenant_id="tenant",
            database_instance_id="db-instance",
            principal_id="owner",
            credential_id="credential",
            session_id="session",
            token="bootstrap",
        )
        planned = await runtime.create(
            tenant_id="tenant",
            principal_id="owner",
            credential_id="credential",
            session_id="session",
            command=CreateIntentionLine(
                RecordId(tenant, "plan-command"),
                RecordId(tenant, "pool-plan"),
                RecordId(tenant, "pool-revision"),
                "Pool remains planned",
                "direct-act",
            ),
        )
        assert planned.disposition == "COMMITTED"
    before = tuple(row for row in rows(path) if row[0] == "planning")
    assert before
    identity, command = fixture()
    async with r10_component(path, {"peer": identity}) as component:
        direct = command.model_copy(
            update={"heads": command.heads.model_copy(update={"journal": 1})}
        )
        initial = await component.journal.execute("peer", direct)
        assert initial.disposition == "COMMITTED" and initial.record is not None
        predecessor = initial.record
        for index, action in enumerate(("record_fact", "correct_claim", "retract_claim")):
            head = component.storage.snapshot("tenant").head
            proposal = command.model_copy(
                update={
                    "command_id": f"prepare-action-{index}",
                    "action": action,
                    "heads": command.heads.model_copy(update={"journal": head}),
                    "predecessor": None if action == "record_fact" else predecessor.record_id,
                }
            )
            candidate = await component.journal.prepare("peer", proposal)
            assert candidate.record is not None and candidate.record.display is not None
            display = candidate.record.display
            confirmation = proposal.model_copy(
                update={
                    "command_id": f"confirm-action-{index}",
                    "action": "confirm_candidate",
                    "display": display,
                    "heads": display.heads,
                }
            )
            confirm_transport(component, confirmation)
            result = await component.journal.execute("peer", confirmation)
            assert result.disposition == "COMMITTED" and result.record is not None
            predecessor = result.record
            assert tuple(row for row in rows(path) if row[0] == "planning") == before
