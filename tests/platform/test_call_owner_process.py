"""Real subprocess preparation IPC over supplied snapshots, not publication authority."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from chiplog.architecture.r7_runtime import (
    R14_CALLS_EVALUATION_MANIFEST,
    R14_CALLS_PRODUCTION_MANIFEST,
    R14_FANOUT_PRODUCTION_MANIFEST,
    R14_PRODUCTION_MANIFEST,
    RuntimeAssemblyManifest,
    RuntimeManifestViolation,
    verify_runtime_manifest,
)
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.composition.r14_runtime import open_r14_runtime
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortResult,
    PublicPortSuccess,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.capabilities.agent_loop.test_call_acceptance_preparation import _requests

_OPERATIONS = (
    "agent_loop.prepare_consequential_acceptance",
    "agent_loop.prepare_pre_accept_cancellation",
)
_SCHEMAS = (
    "chiplog.call.acceptance-preparation.v1",
    "chiplog.call.cancellation-preparation.v1",
)


def _request(
    runtime: AuthorityBrokerRuntime, route: int, payload: bytes, identity: str
) -> PublicPortCall:
    callee = runtime.session("agent_loop")
    return PublicPortCall(
        operation_id=_OPERATIONS[route],
        request_id=identity,
        caller=BrokerSession(
            tenant_id=callee.tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker",
        ),
        callee=callee,
        schema_id=_SCHEMAS[route],
        canonical_payload=payload,
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )


def _decode(sent: PublicPortCall, response: PublicPortResult) -> call.CallPreparationResult:
    assert isinstance(response, PublicPortSuccess), response
    assert response.request_id == sent.request_id
    assert response.responder == sent.callee
    assert response.schema_id == "chiplog.call.preparation-result.v1"
    adapter: TypeAdapter[call.CallPreparationResult] = TypeAdapter(call.CallPreparationResult)
    result = adapter.validate_json(response.canonical_payload)
    assert result.canonical_bytes() == response.canonical_payload
    return result


@pytest.mark.parametrize("route", (0, 1))
def test_isolated_call_owner_prepares_both_routes_and_survives_domain_rejection(route: int) -> None:
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_CALLS_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        before = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert before.process_id != os.getpid()
        assert before.parent_process_id == os.getpid()
        assert "chiplog.capabilities.agent_loop._call_process" in before.loaded_policy_modules
        assert "chiplog.capabilities.agent_loop._r14_calls_process" in before.loaded_policy_modules
        valid = _requests()[route]
        value = valid.model_dump(mode="json")
        invalid: call.AcceptConsequentialCallRequest | call.CancelBeforeAcceptRequest
        if route == 0:
            value["binding"]["original_call_id"] = "other-call"
            invalid = call.AcceptConsequentialCallRequest.model_validate_json(json.dumps(value))
        else:
            value["original_call_id"] = "other-call"
            invalid = call.CancelBeforeAcceptRequest.model_validate_json(json.dumps(value))
        rejected_request = _request(runtime, route, invalid.canonical_bytes(), "domain-rejected")
        rejected = _decode(rejected_request, runtime.call_sync(rejected_request))
        assert isinstance(rejected, call.CallPreparationRejected)
        assert rejected.code == "INTEGRITY_FAULT"
        valid_request = _request(runtime, route, valid.canonical_bytes(), "valid-after-rejection")
        prepared = _decode(valid_request, runtime.call_sync(valid_request))
        if route == 0:
            assert isinstance(prepared, call.PreparedConsequentialAcceptance)
        else:
            assert isinstance(prepared, call.PreparedPreAcceptCancellation)
        after = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert after.process_id == before.process_id
        assert after.identity == before.identity


@pytest.mark.parametrize("defect", ("noncanonical", "route", "schema", "session"))
def test_call_owner_rejects_invalid_wire_or_route_and_remains_usable(defect: str) -> None:
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_CALLS_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        payload = _requests()[0].canonical_bytes()
        request = _request(runtime, 0, payload, "invalid-wire")
        if defect == "noncanonical":
            request = request.model_copy(
                update={"canonical_payload": json.dumps(json.loads(payload), indent=2).encode()}
            )
        elif defect == "route":
            # Use a real registered route/schema with the other route's payload.
            request = request.model_copy(
                update={"operation_id": _OPERATIONS[1], "schema_id": _SCHEMAS[1]}
            )
        elif defect == "schema":
            request = request.model_copy(update={"schema_id": "chiplog.unregistered.v1"})
        else:
            request = request.model_copy(
                update={"callee": request.callee.model_copy(update={"session_id": "old-session"})}
            )
        rejected = runtime.call_sync(request)
        assert isinstance(rejected, PublicPortRejected)
        assert rejected.request_id == request.request_id
        assert rejected.failure.kind == (
            "STALE_SESSION" if defect == "session" else "PROTOCOL_REJECTED"
        )
        valid = _request(runtime, 0, payload, "valid-after-wire-rejection")
        assert isinstance(
            _decode(valid, runtime.call_sync(valid)), call.PreparedConsequentialAcceptance
        )


def test_version_four_owner_starts_without_new_call_preparation_routes() -> None:
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        owner = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert not set(_OPERATIONS) & set(owner.identity.capability_ids)
        assert "chiplog.capabilities.agent_loop._call_process" not in owner.loaded_policy_modules
        for route, prepared in enumerate(_requests()):
            result = runtime.call_sync(
                _request(runtime, route, prepared.canonical_bytes(), f"v4:{route}")
            )
            assert isinstance(result, PublicPortRejected)
            assert result.failure.kind == "PROTOCOL_REJECTED"
            assert "not manifested" in result.failure.reason


@pytest.mark.parametrize("manifest", (R14_CALLS_PRODUCTION_MANIFEST, R14_CALLS_EVALUATION_MANIFEST))
def test_version_five_manifest_is_additive_and_rejects_leaf_substitution(
    manifest: RuntimeAssemblyManifest,
) -> None:
    verify_runtime_manifest(manifest)
    assert manifest.manifest_version == 5
    assert set(R14_PRODUCTION_MANIFEST.routes) <= set(manifest.routes)
    additions = set(manifest.routes) - set(R14_PRODUCTION_MANIFEST.routes)
    assert {route.operation_id for route in additions} == set(_OPERATIONS)
    altered = replace(manifest.leaves[0], implementation="foreign.leaf:Implementation")
    with pytest.raises(RuntimeManifestViolation, match="registered hermetic leaf"):
        verify_runtime_manifest(replace(manifest, leaves=(altered, *manifest.leaves[1:])))


async def test_canonical_r14_factory_preserves_call_preparation_routes(tmp_path: Path) -> None:
    async with open_r14_runtime(tmp_path / "calls.sqlite") as runtime:
        graph = runtime._supervisor.admitted_graph
        assert graph is not None
        assert graph.manifest_digest == R14_FANOUT_PRODUCTION_MANIFEST.fingerprint()
        assert set(_OPERATIONS) <= {route[0] for route in graph.routes}
        owner = next(
            item
            for item in runtime._supervisor.runtime().attest()
            if item.identity.owner_id == "agent_loop"
        )
        assert set(_OPERATIONS) <= set(owner.identity.capability_ids)
        assert owner.target_ids == ("chiplog.capabilities.agent_loop._r14_fanout_process:dispatch",)
