"""Planning/projection reads include durably selected PlanEffect companions."""

import hashlib
import hmac
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.planning_sqlite import PlanningProjectionIntegrityError
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r8 import R8_SURFACES, command_bytes
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_effects import R16EffectsProducer, _decode_request
from chiplog.composition.r16_planning_reads import (
    _canonical,
    _OwnerResult,
    _project,
    merge_planning_publications,
    read_plan_effect_publications,
)
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
    PlanEffectBatch,
)
from chiplog.platform.authority_gate import AuthorityGateError
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.r8_gate import BrokerDeploymentGate
from tests.composition.test_effects_authenticated_cut import _response
from tests.support.deployment_gate import KEY, entitlement, request, signature


async def test_wrong_planning_owner_meaning_rejected_before_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real owner reply for another purpose cannot authorize the original display."""
    database = tmp_path / "wrong-meaning.sqlite"
    async with open_r14_loop(database, responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        broker = runtime._supervisor.runtime()
        original_call = broker.call
        substitutions = 0

        async def substitute(frame: PublicPortCall) -> PublicPortResult:
            nonlocal substitutions
            if frame.operation_id == "planning.r8_create_intention_line":
                # Keep reply identity, schema and all owner commitments genuine.
                # Substitute the actual owner's reply for a different inner command.
                original = _decode_request(frame.canonical_payload)
                command = json.loads(original.command_bytes)
                command["purpose"] = "Substituted planning purpose"
                altered = original.model_copy(update={"command_bytes": _canonical(command)})
                substitutions += 1
                return await original_call(
                    frame.model_copy(
                        update={
                            "canonical_payload": altered.canonical_bytes(),
                        }
                    )
                )
            return await original_call(frame)

        monkeypatch.setattr(broker, "call", substitute)
        display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
        with pytest.raises(ValueError, match="recorded meaning"):
            await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        assert substitutions == 2
        assert runtime._owner_decisions().snapshot().decisions == ()
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM records WHERE owner IN ('planning', 'effects')"
            ).fetchone() == (0,)


async def test_original_real_planning_result_has_closed_projection_shape(tmp_path: Path) -> None:
    async with open_r14_loop(tmp_path / "shape.sqlite", responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        producer = R16EffectsProducer(runtime)
        display = await producer.display_effect("r/turn/1/proposal/effect")
        adoption = await producer.adopt_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        original = adoption.planning.owner_result_bytes
        parsed = _OwnerResult.model_validate_json(original)
        assert _canonical(parsed.model_dump(mode="json")) == original
        assert parsed.operation_kind == "CREATE_INTENTION_LINE"
        with pytest.raises(AuthorityGateError):
            read_plan_effect_publications(runtime)
        with runtime._authority_gate().hold():
            publications, frontier = read_plan_effect_publications(runtime)
            assert publications == ()
            assert frontier == adoption.planning.expected_tenant_head


async def test_plan_effect_planning_reads_survive_later_commit_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "reads.sqlite"
    async with open_r14_loop(database, responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
        published = await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        assert isinstance(published, JournalSelectedPublication)
        with runtime._authority_gate().hold():
            publications, frontier = read_plan_effect_publications(runtime)
        assert len(publications) == 1
        inner = publications[0]
        assert inner.commit_sequence == frontier == published.tenant_commit_sequence
        assert inner.command_id.value == display.display_id + "/command"
        snapshot = json.loads(runtime._snapshot())
        assert snapshot["commands"][0]["command_id"] == inner.command_id.value
        assert snapshot["commands"][0]["request_fingerprint"] == inner.request_fingerprint
        assert json.loads(runtime._projection_snapshot())["lines"][0]["purpose"] == (
            "Record my hermetic action"
        )
        decision = runtime._owner_decisions().lookup(runtime._tenant_id, published.command_id)
        assert decision is not None
        with pytest.raises(ValueError, match="head differs"):
            _project(replace(decision, tenant_commit_sequence=frontier + 1), runtime._tenant_id)
        batch = decision.prepared.request
        assert isinstance(batch, PlanEffectBatch)
        missing = batch.model_copy(update={"complete_records": batch.complete_records[:-1]})
        with pytest.raises(ValueError, match="exact planning records"):
            _project(
                replace(decision, prepared=replace(decision.prepared, request=missing)),
                runtime._tenant_id,
            )
        original_request = _decode_request(batch.planning_command.canonical_bytes)
        changed_command = json.loads(original_request.command_bytes)
        changed_command["purpose"] = "Not the selected action"
        changed_request = original_request.model_copy(
            update={"command_bytes": _canonical(changed_command)}
        ).canonical_bytes()
        changed = batch.model_copy(
            update={
                "planning_command": batch.planning_command.model_copy(
                    update={
                        "canonical_bytes": changed_request,
                        "fingerprint": hashlib.sha256(changed_request).hexdigest(),
                    }
                ),
            }
        )
        with pytest.raises(ValueError, match="recorded meaning"):
            _project(
                replace(decision, prepared=replace(decision.prepared, request=changed)),
                runtime._tenant_id,
            )
        with pytest.raises(PlanningProjectionIntegrityError, match="overlap"):
            merge_planning_publications(publications, publications)
        tenant = TenantId(runtime._tenant_id)
        command = CreateIntentionLine(
            RecordId(tenant, "after-effect"),
            RecordId(tenant, "after-line"),
            RecordId(tenant, "after-revision"),
            "After the effect",
            "after-act",
        )
        # Independent bounded test entitlement exercises the existing legacy gate.
        gate = BrokerDeploymentGate(
            database.with_suffix(database.suffix + ".gate.sqlite3"),
            tenant_id=runtime._tenant_id,
            surfaces=R8_SURFACES,
            authenticate=lambda payload, proof: hmac.compare_digest(
                hmac.digest(KEY, payload, "sha256"), proof
            ),
            journal=IndependentTenantDecisionJournal(
                database.with_suffix(database.suffix + ".gate-journal")
            ),
            clock=lambda: 10,
        )
        value = entitlement()
        value = value.model_copy(
            update={
                "generation": value.generation.model_copy(update={"tenant_id": runtime._tenant_id}),
                "bounds": value.bounds.model_copy(
                    update={"capability_id": "planning.create_intention_line"}
                ),
            }
        )
        assert gate.import_current(value, signature(value), expected=None)
        runtime._gate = gate
        attempt = request(value, "after-effect").model_copy(
            update={
                "surface_id": "planning.create",
                "payload_digest": hashlib.sha256(command_bytes(command)).hexdigest(),
            }
        )
        subsequent = await runtime.create(
            principal_id="hermetic-principal",
            credential_id="hermetic-credential",
            session_id="hermetic-session",
            command=command,
            gate_request=attempt,
        )
        assert subsequent.disposition == "COMMITTED"
        commands = json.loads(runtime._snapshot())["commands"]
        assert [row["command_id"] for row in commands] == [inner.command_id.value, "after-effect"]
    async with open_r14_loop(database, responses=()) as loop:
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        commands = json.loads(runtime._snapshot())["commands"]
        assert [row["command_id"] for row in commands] == [inner.command_id.value, "after-effect"]
        assert [row["purpose"] for row in json.loads(runtime._projection_snapshot())["lines"]] == [
            "Record my hermetic action",
            "After the effect",
        ]
