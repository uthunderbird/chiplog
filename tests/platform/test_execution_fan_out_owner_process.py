"""Actual isolated execution preparation and exact combined owner topology."""

import json
import os
import time
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    R14_FANOUT_PRODUCTION_MANIFEST,
    R14_R17_EXECUTION_EVALUATION_MANIFEST,
    R14_R17_EXECUTION_PRODUCTION_MANIFEST,
    R16_DISPATCH_PRODUCTION_MANIFEST,
    R17_CUSTODY_PRODUCTION_MANIFEST,
    RuntimeAssemblyManifest,
    RuntimeManifestViolation,
    verify_production_evaluation_equivalence,
    verify_runtime_manifest,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallPreparationRejected
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
    ExecutionCapturedFanOutResult,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import CapturedFanOutProposal
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.captured_fan_out import fixture as legacy_fixture
from tests.support.execution_fan_out import fixture

OPERATION = "agent_loop.prepare_execution_captured_fan_out"
REQUEST_SCHEMA = "chiplog.call.execution-captured-fanout-preparation.v2"
RESULT_SCHEMA = "chiplog.call.execution-captured-fanout-result.v2"
MANIFEST = R14_R17_EXECUTION_PRODUCTION_MANIFEST


def leaves(manifest: RuntimeAssemblyManifest) -> dict[str, object]:
    result: dict[str, object] = {
        "clock": ProductionClock(),
        "model": HermeticModel(),
        "planning_store": ProductionPlanningStore(),
    }
    if any(leaf.leaf_id == "effects_transport" for leaf in manifest.leaves):
        result["effects_transport"] = HermeticEffectsProvider(receipt_key=b"fixture", scenarios=())
    assert set(result) == {leaf.leaf_id for leaf in manifest.leaves}
    return result


def request(runtime: AuthorityBrokerRuntime, raw: bytes, identity: str) -> PublicPortCall:
    callee = runtime.session("agent_loop")
    return PublicPortCall(
        operation_id=OPERATION,
        request_id=identity,
        caller=BrokerSession(
            tenant_id=callee.tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker",
        ),
        callee=callee,
        schema_id=REQUEST_SCHEMA,
        canonical_payload=raw,
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )


def test_combined_profile_preserves_exact_existing_owner_routes_and_leaves() -> None:
    verify_runtime_manifest(MANIFEST)
    verify_production_evaluation_equivalence(MANIFEST, R14_R17_EXECUTION_EVALUATION_MANIFEST)
    assert MANIFEST.manifest_version == 11
    sources = (
        R14_FANOUT_PRODUCTION_MANIFEST,
        R16_DISPATCH_PRODUCTION_MANIFEST,
        R17_CUSTODY_PRODUCTION_MANIFEST,
    )
    for source in sources:
        assert set(source.leaves) <= set(MANIFEST.leaves)
        assert MANIFEST.exclusive_resources == source.exclusive_resources
        assert MANIFEST.broker_raw_capabilities == source.broker_raw_capabilities
        assert set(source.routes) <= set(MANIFEST.routes)
    assert MANIFEST.leaves == R16_DISPATCH_PRODUCTION_MANIFEST.leaves
    assert {
        leaf.leaf_id for leaf in set(MANIFEST.leaves) - set(R14_FANOUT_PRODUCTION_MANIFEST.leaves)
    } == {"effects_transport"}
    source_routes = set().union(*(set(source.routes) for source in sources))
    assert {route.operation_id for route in set(MANIFEST.routes) - source_routes} == {OPERATION}
    for owner_id, source in (
        ("effects", R16_DISPATCH_PRODUCTION_MANIFEST),
        ("deployment_trust", R17_CUSTODY_PRODUCTION_MANIFEST),
    ):
        assert next(owner for owner in MANIFEST.owners if owner.owner_id == owner_id) == next(
            owner for owner in source.owners if owner.owner_id == owner_id
        )


@pytest.mark.parametrize(
    "mutation", ["missing_route", "extra_route", "missing_cap", "extra_cap", "leaf"]
)
def test_combined_profile_rejects_incomplete_or_expanded_topology(mutation: str) -> None:
    manifest = MANIFEST
    if mutation == "missing_route":
        manifest = replace(
            manifest, routes=tuple(r for r in manifest.routes if r.operation_id != OPERATION)
        )
    elif mutation == "extra_route":
        manifest = replace(
            manifest,
            routes=(*manifest.routes, replace(manifest.routes[-1], operation_id="unknown")),
        )
    elif mutation == "leaf":
        manifest = replace(
            manifest,
            leaves=(
                replace(manifest.leaves[0], implementation="foreign:Leaf"),
                *manifest.leaves[1:],
            ),
        )
    else:
        owner = next(item for item in manifest.owners if item.owner_id == "agent_loop")
        capabilities = tuple(item for item in owner.capability_ids if item != OPERATION)
        if mutation == "extra_cap":
            capabilities = tuple(sorted((*owner.capability_ids, "unknown")))
        changed = replace(owner, capability_ids=capabilities)
        manifest = replace(
            manifest, owners=tuple(changed if item == owner else item for item in manifest.owners)
        )
    with pytest.raises(RuntimeManifestViolation):
        verify_runtime_manifest(manifest)


async def test_isolated_owner_seals_execution_capture_and_rejects_changed_wire() -> None:
    values = (await fixture(), await fixture(complete=True))
    adapter: TypeAdapter[ExecutionCapturedFanOutResult] = TypeAdapter(ExecutionCapturedFanOutResult)
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", MANIFEST, b"secret", realized_leaves=leaves(MANIFEST)
    ) as runtime:
        owner = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert owner.process_id != os.getpid()
        assert owner.parent_process_id == os.getpid()
        assert owner.target_ids == ("chiplog.capabilities.agent_loop._execution_process:dispatch",)
        assert owner.loaded_policy_modules == tuple(
            "chiplog.capabilities.agent_loop." + name
            for name in (
                "_call_process",
                "_delivery_process",
                "_execution_process",
                "_fan_out_process",
                "_r13_process",
                "_r14_calls_process",
                "_r14_fanout_process",
                "_r14_process",
                "_scheduler_process",
            )
        )
        for index, value in enumerate(values):
            sent = request(runtime, value.canonical_bytes(), f"valid:{index}")
            result = runtime.call_sync(sent)
            assert isinstance(result, PublicPortSuccess), result
            assert result.request_id == sent.request_id and result.responder == sent.callee
            assert result.schema_id == RESULT_SCHEMA
            decoded = adapter.validate_json(result.canonical_payload)
            assert decoded.canonical_bytes() == result.canonical_payload
            assert isinstance(decoded, ExecutionCapturedFanOutProposal)
            assert len(decoded.fan_out.initialized_records) == (3 if index == 0 else 0)
            assert decoded.sealed_run.state == "ACTIVE"
            assert decoded.sealed_run.turns[-1].initialized_calls is not None
            if index == 1:
                assert decoded.sealed_run.delivery_acceptance is None
                assert decoded.sealed_run.turns[-1].state == "RESPONSE_AVAILABLE"
                assert (
                    decoded.sealed_run.turns[-1].attempts[-1]
                    == value.captured_run.turns[-1].attempts[-1]
                )
        value = values[0]
        invalid = value.model_copy(
            update={"request": value.request.model_copy(update={"ordered_calls": ()})}
        )
        result = runtime.call_sync(request(runtime, invalid.canonical_bytes(), "missing-calls"))
        assert isinstance(result, PublicPortSuccess)
        assert isinstance(adapter.validate_json(result.canonical_payload), CallPreparationRejected)
        for defect in ("noncanonical", "schema", "session", "legacy_route"):
            sent = request(runtime, value.canonical_bytes(), "bad:" + defect)
            if defect == "noncanonical":
                sent = sent.model_copy(
                    update={
                        "canonical_payload": json.dumps(
                            json.loads(sent.canonical_payload), indent=2
                        ).encode()
                    }
                )
            elif defect == "schema":
                sent = sent.model_copy(
                    update={"schema_id": "chiplog.call.captured-fanout-preparation.v1"}
                )
            elif defect == "session":
                sent = sent.model_copy(
                    update={"callee": sent.callee.model_copy(update={"session_id": "stale"})}
                )
            else:
                sent = sent.model_copy(
                    update={
                        "operation_id": "agent_loop.prepare_captured_fan_out",
                        "schema_id": "chiplog.call.captured-fanout-preparation.v1",
                    }
                )
            result = runtime.call_sync(sent)
            assert isinstance(result, PublicPortRejected), result
        result = runtime.call_sync(
            request(runtime, value.canonical_bytes(), "valid-after-rejections")
        )
        assert isinstance(result, PublicPortSuccess)
        after = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert after.process_id == owner.process_id and after.identity == owner.identity


async def test_new_profile_preserves_legacy_fanout_and_old_profile_rejects_new_route() -> None:
    old = await legacy_fixture()
    value = await fixture()
    for manifest in (MANIFEST, R14_FANOUT_PRODUCTION_MANIFEST):
        with AuthorityBrokerRuntime(
            "tenant", 1, "generation", manifest, b"secret", realized_leaves=leaves(manifest)
        ) as runtime:
            sent = request(runtime, old.canonical_bytes(), "legacy").model_copy(
                update={
                    "operation_id": "agent_loop.prepare_captured_fan_out",
                    "schema_id": "chiplog.call.captured-fanout-preparation.v1",
                }
            )
            result = runtime.call_sync(sent)
            assert isinstance(result, PublicPortSuccess), result
            assert result.schema_id == "chiplog.call.captured-fanout-result.v1"
            assert isinstance(
                CapturedFanOutProposal.model_validate_json(result.canonical_payload),
                CapturedFanOutProposal,
            )
            if manifest is R14_FANOUT_PRODUCTION_MANIFEST:
                result = runtime.call_sync(request(runtime, value.canonical_bytes(), "new-on-old"))
                assert isinstance(result, PublicPortRejected)
                assert "not manifested" in result.failure.reason
