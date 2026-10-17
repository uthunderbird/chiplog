"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
)

from chiplog.capabilities.deployment_trust.operator_grant_authorization_contracts import (
    OperatorGrantAuthorizationPayloadV2,
    SignedOperatorGrantAuthorizationV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    IssuePreparedExternalDeliveryGrantRequestV2,
)
from chiplog.platform.operator_grant_key_pin import OperatorGrantKeyPinFileV2

_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def _write_grant_pin(database: Path, key: Ed25519PrivateKey = _KEY) -> None:
    pin = OperatorGrantKeyPinFileV2(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=("ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",),
        status="ACTIVE",
        public_key=key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    path = database.with_suffix(database.suffix + ".operator-grant-key.json")
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)


def _trust_observation(runtime: object) -> object:
    from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
    from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
        HermeticTrustObservationV1,
    )

    trust = runtime._trust  # type: ignore[attr-defined]
    decision_id, _, raw = trust._journal.entries()[-1]
    return HermeticTrustObservationV1(
        physical_journal_head=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        logical_snapshot_head=trust.owner_snapshot_entries()[-1][0],
    )


def _signed_grant(request: IssuePreparedExternalDeliveryGrantRequestV2) -> bytes:
    raw = request.canonical_bytes()
    payload = OperatorGrantAuthorizationPayloadV2(
        algorithm="Ed25519",
        source_id="grant-source:" + request.command_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        grant_id=request.grant_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_request_bytes=raw,
    )
    return SignedOperatorGrantAuthorizationV2(
        payload=payload, signature=_KEY.sign(payload.canonical_bytes())
    ).canonical_bytes()
