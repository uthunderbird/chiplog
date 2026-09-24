from __future__ import annotations

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST,
    verify_runtime_manifest,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def test_h1_local_effects_manifest_mounts_aggregate_effects_owner() -> None:
    manifest = R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST
    assert manifest.manifest_version == 18
    assert verify_runtime_manifest(manifest)
    effects = next(owner for owner in manifest.owners if owner.owner_id == "effects")
    assert effects.target_ids == ("chiplog.capabilities.effects._h1_local_process:dispatch",)
    assert "effects.prepare_h1_local_commentary" in effects.capability_ids
    leaves = {
        "clock": ProductionClock(),
        "planning_store": ProductionPlanningStore(),
        "model": HermeticModel(),
        "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
    }
    with AuthorityBrokerRuntime(
        "tenant", 1, "h1-local-effects", manifest, b"secret", realized_leaves=leaves
    ) as runtime:
        assert runtime.session("effects").owner_id == "effects"
