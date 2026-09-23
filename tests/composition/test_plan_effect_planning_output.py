"""Semantically altered real planning replies must not become PlanEffect authority."""

import base64
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from chiplog.adapters.driven.planning_sqlite import _publication
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.planning._planning import _PlanningPublication
from chiplog.composition.r7_planning import _decode_owner_result
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_effects import R16EffectsProducer
from chiplog.domain_primitives import TenantId
from chiplog.platform.broker import PublicPortCall, PublicPortResult, PublicPortSuccess
from tests.composition.test_plan_effect_publication import response


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _mutate_reply(returned: PublicPortSuccess, tenant: str, mutation: str) -> PublicPortSuccess:
    wrapper = _decode_owner_result(returned.canonical_payload)
    assert wrapper.canonical_result_bytes is not None
    proposal = json.loads(wrapper.canonical_result_bytes)
    rows: list[dict[str, Any]] = proposal["records"]
    envelopes = [json.loads(base64.b64decode(row["canonical_bytes"])) for row in rows]
    foreign_id = {"tenant_id": tenant, "value": "other-object"}
    revision = envelopes[3]["fields"]
    if mutation == "revision_activity":
        revision["activity"] = "RETIRED"
    elif mutation == "revision_outcome":
        revision["personal_outcome"] = "ACHIEVED"
    elif mutation == "revision_line":
        revision["intention_line_id"] = foreign_id
    elif mutation == "revision_ordinal":
        revision["ordinal"] = 1
    elif mutation == "revision_predecessor":
        revision["predecessor_revision_id"] = foreign_id
    elif mutation == "line_initial_revision":
        envelopes[2]["fields"]["initial_revision_id"] = foreign_id
    elif mutation == "evidence_actor":
        envelopes[1]["fields"]["principal_id"] = "other-principal"
    elif mutation == "evidence_trust":
        envelopes[1]["fields"]["trust_reference"]["trust_head"] += "-other"
    elif mutation == "command_scope":
        envelopes[0]["fields"]["permission_scope"] = "planning.read"
    else:
        assert mutation == "result_manifest"

    def publication() -> _PlanningPublication:
        return _publication(
            TenantId(tenant),
            proposal["result"]["command_id"]["value"],
            proposal["request_fingerprint"],
            proposal["commit_sequence"],
            tuple(
                (row["record_id"], _canonical(value))
                for row, value in zip(rows, envelopes, strict=True)
            ),
        )

    # Repair nested commitments as well as the outer result, so semantic
    # rejection cannot be attributed merely to stale record fingerprints.
    interim = publication()
    manifest = [
        {
            "record_id": {"tenant_id": tenant, "value": record.record_id.value},
            "record_type_id": record.record_type_id,
            "fingerprint": record.fingerprint,
        }
        for record in interim.records[:4]
    ]
    if mutation == "result_manifest":
        manifest.reverse()
    envelopes[-1]["fields"]["record_manifest"] = manifest
    envelopes[-1]["fields"]["batch_fingerprint"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    repaired = publication()
    proposal["result"] = asdict(repaired.result)
    for row, record in zip(rows, repaired.records, strict=True):
        row["canonical_bytes"] = base64.b64encode(record.canonical_bytes).decode()
        row["fingerprint"] = record.fingerprint
    wrapper = wrapper.model_copy(update={"canonical_result_bytes": _canonical(proposal)})
    return returned.model_copy(update={"canonical_payload": wrapper.canonical_bytes()})


@pytest.mark.parametrize(
    "mutation",
    (
        "revision_activity",
        "revision_outcome",
        "revision_line",
        "revision_ordinal",
        "revision_predecessor",
        "line_initial_revision",
        "evidence_actor",
        "evidence_trust",
        "command_scope",
        "result_manifest",
    ),
)
async def test_rehashed_planning_owner_semantic_mutation_is_never_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    async with open_r14_loop(tmp_path / "planning-output.sqlite", responses=(response(),)) as loop:
        created = await loop.create("r", "Propose", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        broker = runtime._supervisor.runtime()
        original = broker.call
        changed = 0

        async def corrupt(request: PublicPortCall) -> PublicPortResult:
            nonlocal changed
            returned = await original(request)
            if request.operation_id != "planning.r8_create_intention_line":
                return returned
            assert isinstance(returned, PublicPortSuccess)
            changed += 1
            return _mutate_reply(returned, runtime._tenant_id, mutation)

        monkeypatch.setattr(broker, "call", corrupt)
        with pytest.raises(
            ValueError, match=r"recorded meaning|exact initial planning publication"
        ):
            display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/effect")
            await runtime.publish_effect(
                "hermetic-ingress",
                display.display_id,
                display.display_digest,
                display.adoption_act_id,
            )
        assert changed >= 1
        assert not runtime._owner_decisions().snapshot().decisions
        with sqlite3.connect(runtime._database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM records WHERE owner IN ('planning', 'effects')"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM publications "
                "WHERE operation_kind='effects.publish_plan_effect'"
            ).fetchone() == (0,)
