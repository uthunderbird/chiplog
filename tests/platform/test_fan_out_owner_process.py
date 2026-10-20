"""Actual isolated captured fan-out IPC; preparation does not publish or authenticate."""

import json
import os
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from chiplog.architecture.r7_runtime import (
    R14_FANOUT_EVALUATION_MANIFEST,
    R14_FANOUT_PRODUCTION_MANIFEST,
    RuntimeAssemblyManifest,
    RuntimeManifestViolation,
    verify_runtime_manifest,
)
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import fan_out_contracts as fan
from chiplog.capabilities.agent_loop.fan_out_preparation import prepare_captured_fan_out
from chiplog.platform.broker import (
    PublicPortCall,
    PublicPortRejected,
    PublicPortResult,
    PublicPortSuccess,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.platform.test_call_owner_process import _request as call_request
from tests.support.captured_fan_out import fixture as _fixture

_OPERATION = "agent_loop.prepare_captured_fan_out"
_REQUEST_SCHEMA = "chiplog.call.captured-fanout-preparation.v1"
_RESULT_SCHEMA = "chiplog.call.captured-fanout-result.v1"
_NEW_MODULES = {
    "chiplog.capabilities.agent_loop._fan_out_process",
    "chiplog.capabilities.agent_loop._r14_fanout_process",
}


def _request(runtime: AuthorityBrokerRuntime, payload: bytes, identity: str) -> PublicPortCall:
    return call_request(runtime, 0, payload, identity).model_copy(
        update={
            "operation_id": _OPERATION,
            "schema_id": _REQUEST_SCHEMA,
        }
    )


def _decode(sent: PublicPortCall, response: PublicPortResult) -> fan.CapturedFanOutResult:
    assert isinstance(response, PublicPortSuccess), response
    assert response.request_id == sent.request_id
    assert response.responder == sent.callee
    assert response.schema_id == _RESULT_SCHEMA
    adapter: TypeAdapter[fan.CapturedFanOutResult] = TypeAdapter(fan.CapturedFanOutResult)
    result = adapter.validate_json(response.canonical_payload)
    assert result.canonical_bytes() == response.canonical_payload
    return result


@pytest.mark.parametrize("delivery", (False, True))
@pytest.mark.parametrize("complete", (False, True))
async def test_isolated_owner_prepares_both_registered_parsers_after_io_sealing(
    delivery: bool,
    complete: bool,
) -> None:
    value = await _fixture(delivery=delivery, complete=complete)
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_FANOUT_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        owner = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert owner.process_id != os.getpid()
        assert owner.parent_process_id == os.getpid()
        assert set(owner.loaded_policy_modules) >= _NEW_MODULES
        assert _OPERATION in owner.identity.capability_ids
        assert owner.target_ids == ("chiplog.capabilities.agent_loop._r14_fanout_process:dispatch",)
        sent = _request(runtime, value.canonical_bytes(), "prepare")
        result = _decode(sent, runtime.call_sync(sent))
        assert isinstance(result, fan.CapturedFanOutProposal)
        assert result == prepare_captured_fan_out(value)
        assert len(result.fan_out.initialized_records) == (0 if complete else 2)


async def test_typed_rejection_then_valid_request_preserves_owner_pid_and_session() -> None:
    value = await _fixture()
    invalid = value.model_copy(
        update={
            "request": value.request.model_copy(
                update={
                    "ordered_calls": value.request.ordered_calls[:1],
                }
            )
        }
    )
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_FANOUT_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        before = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        sent = _request(runtime, invalid.canonical_bytes(), "domain-rejected")
        rejected = _decode(sent, runtime.call_sync(sent))
        assert isinstance(rejected, call.CallPreparationRejected)
        assert rejected == prepare_captured_fan_out(invalid)
        valid = _request(runtime, value.canonical_bytes(), "valid-after-domain-rejection")
        assert isinstance(_decode(valid, runtime.call_sync(valid)), fan.CapturedFanOutProposal)
        after = next(item for item in runtime.attest() if item.identity.owner_id == "agent_loop")
        assert after.process_id == before.process_id
        assert after.identity == before.identity


@pytest.mark.parametrize("defect", ("noncanonical", "schema", "session", "route"))
async def test_invalid_wire_is_refused_and_owner_remains_usable(defect: str) -> None:
    value = await _fixture()
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_FANOUT_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        payload = value.canonical_bytes()
        sent = _request(runtime, payload, "invalid-wire")
        if defect == "noncanonical":
            sent = sent.model_copy(
                update={"canonical_payload": json.dumps(json.loads(payload), indent=2).encode()}
            )
        elif defect == "schema":
            sent = sent.model_copy(update={"schema_id": "chiplog.unregistered.v1"})
        elif defect == "session":
            sent = sent.model_copy(
                update={"callee": sent.callee.model_copy(update={"session_id": "stale"})}
            )
        else:
            # A real older route must not accept the new route's well-formed payload.
            sent = call_request(runtime, 0, payload, "wrong-route")
        result = runtime.call_sync(sent)
        assert isinstance(result, PublicPortRejected)
        assert result.request_id == sent.request_id
        assert result.failure.kind == (
            "STALE_SESSION" if defect == "session" else "PROTOCOL_REJECTED"
        )
        valid = _request(runtime, payload, "valid-after-wire-rejection")
        assert isinstance(_decode(valid, runtime.call_sync(valid)), fan.CapturedFanOutProposal)


@pytest.mark.parametrize(
    "manifest", (R14_FANOUT_PRODUCTION_MANIFEST, R14_FANOUT_EVALUATION_MANIFEST)
)
def test_version_six_is_exact_additive_extension_and_rejects_foreign_leaf(
    manifest: RuntimeAssemblyManifest,
) -> None:
    verify_runtime_manifest(manifest)
    assert manifest.manifest_version == 6
    assert _OPERATION in {route.operation_id for route in manifest.routes}
    altered = replace(manifest.leaves[0], implementation="foreign.leaf:Implementation")
    with pytest.raises(RuntimeManifestViolation, match="registered hermetic leaf"):
        verify_runtime_manifest(replace(manifest, leaves=(altered, *manifest.leaves[1:])))
