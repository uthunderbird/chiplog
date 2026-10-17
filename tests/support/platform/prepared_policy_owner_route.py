"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.support.h1_installed_launch import (
    DATABASE,
    TENANT,
)

from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    OperatorPolicyAuthorizationPayloadV1,
    SignedOperatorPolicyAuthorizationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    IssuePreparedExternalSelfDeliveryPolicyRequestV1,
    RevokePreparedExternalSelfDeliveryPolicyRequestV1,
)
from chiplog.composition.common_cli_execution_runtime import (
    CommonCliExecutionRuntime,
)
from chiplog.platform.operator_policy_key_pin import OperatorPolicyKeyPinFileV1

_OPERATOR_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))


def _pin_path(database: Path) -> Path:
    return database.with_suffix(database.suffix + ".operator-policy-key.json")


def _write_pin(database: Path, *, status: Literal["ACTIVE", "REVOKED"] = "ACTIVE") -> Path:
    pin = OperatorPolicyKeyPinFileV1(
        tenant_id=TENANT,
        database_id=DATABASE,
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=(
            "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        ),
        status=status,
        public_key=_OPERATOR_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    path = _pin_path(database)
    path.write_bytes(pin.canonical_bytes())
    path.chmod(0o600)
    return path


def _signed_source(
    request: IssuePreparedExternalSelfDeliveryPolicyRequestV1
    | RevokePreparedExternalSelfDeliveryPolicyRequestV1,
    *,
    signing_key: Ed25519PrivateKey = _OPERATOR_KEY,
) -> bytes:
    request_bytes = request.canonical_bytes()
    payload = OperatorPolicyAuthorizationPayloadV1(
        algorithm="Ed25519",
        source_id="source:" + request.command_id,
        operator_key_id="operator-key",
        tenant_id=request.tenant_id,
        database_id=request.database_id,
        policy_id=request.policy_id,
        command_id=request.command_id,
        operation=request.operation,
        request_sha256=hashlib.sha256(request_bytes).hexdigest(),
        canonical_request_bytes=request_bytes,
    )
    return SignedOperatorPolicyAuthorizationV1(
        payload=payload, signature=signing_key.sign(payload.canonical_bytes())
    ).canonical_bytes()


async def _authorize(
    runtime: CommonCliExecutionRuntime, source: bytes, *, request_id: str
) -> object:
    return await runtime.authorize_prepared_self_delivery_policy(source, request_id=request_id)
