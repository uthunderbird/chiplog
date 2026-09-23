"""Closed trust route binding, independent of semantic authority evaluation."""

import base64
import json

import pytest

from chiplog.capabilities.deployment_trust import _r7_process as owner

PAIRS = (
    ("deployment_trust.authenticate", "AUTHENTICATE"),
    ("deployment_trust.bootstrap", "BOOTSTRAP"),
    ("deployment_trust.revalidate", "REVALIDATE"),
    ("deployment_trust.runtime_admission", "RUNTIME_ADMISSION"),
)


def _wire(mode: str, request: dict[str, object]) -> bytes:
    return owner._TrustOwnerCall.model_validate(
        {
            "mode": mode,
            "snapshot_bytes": b"[]",
            "request_bytes": json.dumps(request, sort_keys=True, separators=(",", ":")).encode(),
        }
    ).canonical_bytes()


def test_authenticate_cannot_evaluate_bootstrap_proposal() -> None:
    result = owner.dispatch(
        "deployment_trust.authenticate",
        _wire(
            "BOOTSTRAP",
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
        ),
    )
    assert result.get("failure") == "PROTOCOL_REJECTED"
    assert "payload" not in result


@pytest.mark.parametrize(
    "operation,expected,mode",
    [
        (operation, expected, mode)
        for operation, expected in PAIRS
        for _, mode in PAIRS
        if expected != mode
    ],
)
def test_cross_mode_pairs_reject_before_evaluation(
    operation: str,
    expected: str,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(call: owner._TrustOwnerCall) -> owner._TrustOwnerResult:
        pytest.fail(f"route expecting {expected} evaluated {call.mode}")

    monkeypatch.setattr(owner, "_evaluate", forbidden)
    result = owner.dispatch(operation, _wire(mode, {}))
    assert result.get("failure") == "PROTOCOL_REJECTED"
    assert "payload" not in result


@pytest.mark.parametrize("operation,mode", PAIRS)
def test_matching_route_preserves_exact_evaluator_result(
    operation: str,
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []
    expected = owner._TrustOwnerResult(disposition="DENIED", reference_bytes=None, reason="fixture")

    def evaluate(call: owner._TrustOwnerCall) -> owner._TrustOwnerResult:
        seen.append(call.mode)
        return expected

    monkeypatch.setattr(owner, "_evaluate", evaluate)
    payload = _wire(mode, {})
    assert owner.dispatch(operation, payload) == {
        "payload": base64.b64encode(expected.canonical_bytes()).decode(),
        "schema_id": "chiplog.deployment-trust.owner-result.v1",
    }
    assert seen == [mode]
    assert owner.dispatch(operation, b" " + payload)["failure"] == "PROTOCOL_REJECTED"
    assert owner.dispatch("deployment_trust.unknown", payload)["failure"] == "UNAVAILABLE"
    assert seen == [mode]
