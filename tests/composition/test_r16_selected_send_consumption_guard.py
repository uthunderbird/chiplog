"""Post-SEND consumption checks current trust without reusing elapsed RPC deadlines."""

import json
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r16_denial_inputs import (
    require_selected_send_consumption_invocation,
)


def _runtime_and_observation(*, current_observation=None, generation="generation"):
    observation = object()
    session = SimpleNamespace(broker_epoch="epoch", generation_id=generation)
    principal = json.dumps(
        {"tenant_id": "tenant", "principal_id": "hermetic-principal", "contour": "CLI"}
    ).encode()
    runtime = SimpleNamespace(
        _tenant_id="tenant",
        _supervisor=SimpleNamespace(runtime=lambda: SimpleNamespace(session=lambda owner: session)),
        _read_ledger=SimpleNamespace(
            current_state=lambda tenant: SimpleNamespace(
                owner_draining=False,
                broker_epoch="epoch",
                owner_generation=generation,
            )
        ),
        _trust=SimpleNamespace(
            capture_verified_observation=lambda: (
                observation if current_observation is None else current_observation
            )
        ),
    )
    observed = SimpleNamespace(
        observation=observation,
        admission_valid_until_ns=0,
        request=SimpleNamespace(
            operation_id="deployment_trust.authenticate",
            schema_id="chiplog.deployment-trust.owner-call.v1",
            callee=session,
            budget=SimpleNamespace(absolute_deadline_ns=0),
        ),
        result=SimpleNamespace(disposition="VALID", reference_bytes=principal),
    )
    return runtime, observed, principal


def test_selected_send_consumption_accepts_exact_current_auth_after_rpc_deadline() -> None:
    runtime, observed, principal = _runtime_and_observation()

    assert require_selected_send_consumption_invocation(runtime, observed) == principal


def test_selected_send_consumption_rejects_changed_trust_observation() -> None:
    runtime, observed, _ = _runtime_and_observation(current_observation=object())

    with pytest.raises(LoopRejected, match="no longer current"):
        require_selected_send_consumption_invocation(runtime, observed)
