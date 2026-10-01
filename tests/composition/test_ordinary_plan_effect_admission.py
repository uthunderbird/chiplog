"""Pure ORDINARY admission claims for complete legacy PlanEffect output."""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.composition.r14 import open_r14_loop
from chiplog.composition.r14_runtime import R14PlanningRuntime
from chiplog.composition.r16_effects import HermeticEffectProposal, R16EffectsProducer
from chiplog.composition.r17_admission_records import (
    AdmissionAggregate,
    CapacityClaimed,
    EpochOpenedV2,
    hermetic_reserves_v2,
    ordinary_plan_effect_events,
    record_head,
)
from chiplog.composition.r17_ingress_registry import retained_cli_profile
from chiplog.platform._owner_publication_contracts import PlanEffectBatch


def _response() -> bytes:
    proposal = HermeticEffectProposal(
        schema_id="chiplog.hermetic-effect-proposal.v1",
        purpose="Ordinary admission fixture",
        payload_base64=base64.b64encode(b"ordinary-admission").decode(),
        bundle_members=("ordinary",),
    )
    return json.dumps(
        {
            "kind": "Continue",
            "tool_calls": [
                {
                    "call_id": "ordinary",
                    "tool": "propose_intent",
                    "text": proposal.canonical_bytes().decode(),
                }
            ],
        }
    ).encode()


async def _batch(tmp_path: Path) -> PlanEffectBatch:
    async with open_r14_loop(tmp_path / "ordinary.sqlite", responses=(_response(),)) as loop:
        created = await loop.create("r", "Propose ordinary admission", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R14PlanningRuntime)
        display = await R16EffectsProducer(runtime).display_effect("r/turn/1/proposal/ordinary")
        await runtime.publish_effect(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        selected = runtime._owner_decisions().snapshot().decisions[-1]
        assert isinstance(selected.prepared.request, PlanEffectBatch)
        return selected.prepared.request


def _profile():
    return retained_cli_profile("hermetic-tenant", "hermetic-database")


def _empty() -> AdmissionAggregate:
    return AdmissionAggregate(epoch=None, claims=(), ready=())


@pytest.mark.asyncio
async def test_ordinary_plan_effect_genesis_is_deterministic_and_emits_no_ready(
    tmp_path: Path,
) -> None:
    batch = await _batch(tmp_path)
    first = ordinary_plan_effect_events(
        batch=batch, retained_cli_profile=_profile(), aggregate=_empty()
    )
    assert first == ordinary_plan_effect_events(
        batch=batch, retained_cli_profile=_profile(), aggregate=_empty()
    )
    assert len(first) == 2
    opened, claim = first
    assert isinstance(opened, EpochOpenedV2)
    assert isinstance(claim, CapacityClaimed)
    assert claim.source_class == claim.occupancy_kind == "ORDINARY"
    assert claim.token_id is claim.custody_record is claim.inbox is None


@pytest.mark.asyncio
async def test_ordinary_plan_effect_uses_exact_ordinary_predecessor_and_reserves(
    tmp_path: Path,
) -> None:
    batch = await _batch(tmp_path)
    opened, first = ordinary_plan_effect_events(
        batch=batch, retained_cli_profile=_profile(), aggregate=_empty()
    )
    assert isinstance(opened, EpochOpenedV2) and isinstance(first, CapacityClaimed)
    second = first.model_copy(
        update={
            "claim_id": first.claim_id + ":second",
            "predecessor": record_head(first.claim_id, first),
        }
    )
    full = AdmissionAggregate(epoch=opened, claims=(first, second), ready=())
    with pytest.raises(ValueError, match="item reserve exhausted"):
        ordinary_plan_effect_events(batch=batch, retained_cli_profile=_profile(), aggregate=full)

    reserve = next(row for row in hermetic_reserves_v2() if row.source_class == "ORDINARY")
    first_full = first.model_copy(update={"accounted_bytes": reserve.maximum_item_bytes})
    second_full = second.model_copy(
        update={
            "accounted_bytes": reserve.maximum_item_bytes,
            "predecessor": record_head(first_full.claim_id, first_full),
        }
    )
    with pytest.raises(ValueError, match="byte reserve exhausted"):
        ordinary_plan_effect_events(
            batch=batch,
            retained_cli_profile=_profile(),
            aggregate=AdmissionAggregate(epoch=opened, claims=(first_full, second_full), ready=()),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ("foreign_epoch", "unknown_class", "orphan", "no_epoch"))
async def test_ordinary_plan_effect_rejects_untrusted_aggregate(
    tmp_path: Path, damage: str
) -> None:
    batch = await _batch(tmp_path)
    opened, claim = ordinary_plan_effect_events(
        batch=batch, retained_cli_profile=_profile(), aggregate=_empty()
    )
    assert isinstance(opened, EpochOpenedV2) and isinstance(claim, CapacityClaimed)
    if damage == "foreign_epoch":
        aggregate = AdmissionAggregate(
            epoch=opened.model_copy(update={"fence": 1}), claims=(claim,), ready=()
        )
    elif damage == "unknown_class":
        aggregate = AdmissionAggregate(
            epoch=opened, claims=(claim.model_copy(update={"source_class": "FOREIGN"}),), ready=()
        )
    elif damage == "orphan":
        aggregate = AdmissionAggregate(
            epoch=opened,
            claims=(claim.model_copy(update={"predecessor": record_head("foreign", claim)}),),
            ready=(),
        )
    else:
        aggregate = AdmissionAggregate(epoch=None, claims=(claim,), ready=())
    with pytest.raises(ValueError):
        ordinary_plan_effect_events(
            batch=batch, retained_cli_profile=_profile(), aggregate=aggregate
        )


@pytest.mark.asyncio
async def test_ordinary_plan_effect_rejects_malformed_or_crossed_original_batch(
    tmp_path: Path,
) -> None:
    batch = await _batch(tmp_path)
    malformed = batch.model_copy(update={"complete_batch_fingerprint": "0" * 64})
    with pytest.raises(ValueError):
        ordinary_plan_effect_events(
            batch=malformed, retained_cli_profile=_profile(), aggregate=_empty()
        )
    crossed_records = (
        batch.complete_records[1],
        batch.complete_records[0],
        *batch.complete_records[2:],
    )
    crossed = batch.model_copy(
        update={
            "complete_records": crossed_records,
            "complete_batch_fingerprint": hashlib.sha256(
                json.dumps(
                    [row.model_dump(mode="json") for row in crossed_records],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
        }
    )
    with pytest.raises(ValueError):
        ordinary_plan_effect_events(
            batch=crossed, retained_cli_profile=_profile(), aggregate=_empty()
        )
