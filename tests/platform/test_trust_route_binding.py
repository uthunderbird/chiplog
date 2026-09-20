"""Actual isolated public-port route/mode binding; no journal is published."""

import base64
import json
import time

import pytest

from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST, R14_PRODUCTION_MANIFEST
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


@pytest.mark.parametrize("current", [False, True])
def test_all_four_modes_follow_only_their_registered_isolated_route(current: bool) -> None:
    pairs = (
        ("deployment_trust.authenticate", "AUTHENTICATE"),
        ("deployment_trust.bootstrap", "BOOTSTRAP"),
        ("deployment_trust.revalidate", "REVALIDATE"),
        ("deployment_trust.runtime_admission", "RUNTIME_ADMISSION"),
    )
    with AuthorityBrokerRuntime(
        "tenant",
        1,
        "generation",
        R14_PRODUCTION_MANIFEST if current else R7_PRODUCTION_MANIFEST,
        b"secret",
    ) as runtime:

        def invoke(
            operation: str, mode: str, snapshot: bytes, request: object
        ) -> PublicPortSuccess | PublicPortRejected:
            payload = _canonical(
                {
                    "mode": mode,
                    "snapshot_bytes": base64.b64encode(snapshot).decode(),
                    "request_bytes": base64.b64encode(_canonical(request)).decode(),
                }
            )
            return runtime.call_sync(
                PublicPortCall(
                    operation_id=operation,
                    request_id=operation + ":" + mode,
                    caller=BrokerSession(
                        tenant_id="tenant",
                        broker_epoch=1,
                        generation_id="generation",
                        owner_id="broker",
                        session_id="broker",
                    ),
                    callee=runtime.session("deployment_trust"),
                    schema_id="chiplog.deployment-trust.owner-call.v1",
                    canonical_payload=payload,
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                        policy_version=1,
                    ),
                )
            )

        for operation, expected in pairs:
            for _, mode in pairs:
                if mode != expected:
                    result = invoke(operation, mode, b"[]", {})
                    assert isinstance(result, PublicPortRejected)
                    assert "mode differs" in result.failure.reason

        bootstrap = invoke(
            "deployment_trust.bootstrap",
            "BOOTSTRAP",
            b"[]",
            {
                "credential_id": "credential",
                "database_instance_id": "database",
                "expected_peer": "uid:test",
                "peer": "uid:test",
                "principal_id": "principal",
                "session_id": "session",
                "tenant_id": "tenant",
                "token_fingerprint": "token",
            },
        )
        assert isinstance(bootstrap, PublicPortSuccess)
        decoded = json.loads(bootstrap.canonical_payload)
        assert decoded["disposition"] == "VALID"
        decisions = json.loads(base64.b64decode(decoded["reference_bytes"]))["decisions"]
        snapshot = _canonical(
            [
                [f"decision-{index}", None, base64.b64encode(_canonical(decision)).decode()]
                for index, decision in enumerate(decisions)
            ]
        )
        auth = invoke(
            "deployment_trust.authenticate",
            "AUTHENTICATE",
            snapshot,
            {
                "contour": "CLI",
                "credential_id": "credential",
                "session_id": "session",
                "peer_credential": "uid:test",
            },
        )
        assert isinstance(auth, PublicPortSuccess)
        authenticated = json.loads(auth.canonical_payload)
        assert authenticated["disposition"] == "VALID"
        reference = json.loads(base64.b64decode(authenticated["reference_bytes"]))
        revalidated = invoke(
            "deployment_trust.revalidate",
            "REVALIDATE",
            snapshot,
            {
                "operation": "CREATE_INTENTION_LINE",
                "reference": reference,
            },
        )
        admission = invoke(
            "deployment_trust.runtime_admission",
            "RUNTIME_ADMISSION",
            snapshot,
            {"tenant_id": "tenant"},
        )
        for result in (revalidated, admission):
            assert isinstance(result, PublicPortSuccess)
            assert result.responder == runtime.session("deployment_trust")
            assert result.schema_id == "chiplog.deployment-trust.owner-result.v1"
            assert json.loads(result.canonical_payload)["disposition"] == "VALID"
