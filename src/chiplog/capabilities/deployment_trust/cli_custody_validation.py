"""Trust-owned closed CLI custody interpretation of an original verified snapshot."""

import hashlib
import json
from typing import Literal

from pydantic import Field

from ._r7_process import _snapshot
from .cli_custody_contracts import (
    CliCustodyAuthenticated,
    CliCustodyAuthenticationRequest,
    CliCustodyDecision,
    CliCustodyDTO,
    CliCustodyHead,
    CliCustodyReference,
    CliCustodyRejected,
)

MAX_AGE_NS = 5_000_000_000


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def policy_head() -> CliCustodyHead:
    raw = b"chiplog.cli.retained-custody.self-only.v1:5000000000:no-ack:no-action"
    value = digest(raw)
    return CliCustodyHead(
        identity="cli.custody.policy", head="cli.custody.policy/" + value, fingerprint=value
    )


class CliCustodyOwnerCall(CliCustodyDTO):
    schema_id: Literal["chiplog.cli.custody-owner-call.v1"] = "chiplog.cli.custody-owner-call.v1"
    snapshot_bytes: bytes = Field(min_length=1)
    request: CliCustodyAuthenticationRequest


def evaluate_cli_custody(call: CliCustodyOwnerCall) -> CliCustodyDecision:
    request = call.request
    scope, challenge, socket = request.offer.scope, request.offer.challenge, request.socket
    fingerprint = digest(request.canonical_bytes())

    def reject(
        reason: Literal[
            "SCOPE_MISMATCH",
            "HANDSHAKE_MISMATCH",
            "SESSION_MISMATCH",
            "CREDENTIAL_MISMATCH",
            "PEER_MISMATCH",
            "POLICY_MISMATCH",
            "EXPIRED",
            "UNAVAILABLE",
        ],
    ) -> CliCustodyRejected:
        return CliCustodyRejected(
            disposition="DENIED", request_fingerprint=fingerprint, reason=reason
        )

    snapshot = _snapshot(call.snapshot_bytes)
    credential = snapshot["credential"]
    if snapshot["phase"] != "ACTIVE" or not isinstance(credential, dict):
        return reject("UNAVAILABLE")
    token = json.loads(scope.original_token_bytes)
    if (
        scope.tenant_id != snapshot["tenant_id"]
        or scope.tenant_id != "hermetic-tenant"
        or scope.database_id != "hermetic-database"
        or token["token_id"] != scope.token_id
        or token["source"]["tenant_id"] != scope.tenant_id
        or token["source"]["database_id"] != scope.database_id
        or token["source"]["source_class"] != "CLI"
        or token["source"]["manifest_row"] != scope.source_profile.model_dump()
        or digest(request.raw_command_bytes) != scope.raw_digest
        or len(request.raw_command_bytes) != scope.raw_byte_count
        or scope.original_subject != scope.token_id
    ):
        return reject("SCOPE_MISMATCH")
    if (
        challenge.scope_fingerprint != digest(scope.canonical_bytes())
        or challenge.connection_id != socket.connection_id
        or request.response.challenge_fingerprint != digest(challenge.canonical_bytes())
        or challenge.clock_epoch != socket.clock_epoch
    ):
        return reject("HANDSHAKE_MISMATCH")
    endpoint = digest(
        json.dumps(
            {
                "schema": "chiplog.cli.private-retained-endpoint.v1",
                "tenant": scope.tenant_id,
                "database": scope.database_id,
                "path": socket.socket_path,
                "device": socket.socket_device,
                "inode": socket.socket_inode,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    if challenge.policy != policy_head() or socket.endpoint_registration != CliCustodyHead(
        identity="cli.custody.endpoint",
        head="cli.custody.endpoint/" + endpoint,
        fingerprint=endpoint,
    ):
        return reject("POLICY_MISMATCH")
    if (
        not socket.observed_at_ns <= request.observed_time_ns < challenge.valid_until_ns
        or challenge.valid_until_ns != socket.valid_until_ns
        or socket.valid_until_ns - socket.observed_at_ns > MAX_AGE_NS
    ):
        return reject("EXPIRED")
    if credential.get("revoked") or credential["credential_id"] != challenge.credential_id:
        return reject("CREDENTIAL_MISMATCH")
    if credential["session_id"] != challenge.session_id:
        return reject("SESSION_MISMATCH")
    if credential["peer_credential"] != f"uid:{socket.peer.uid}":
        return reject("PEER_MISMATCH")
    freshness = snapshot["freshness"]
    if type(freshness) is not int:
        return reject("UNAVAILABLE")
    return CliCustodyAuthenticated(
        reference=CliCustodyReference(
            request_fingerprint=fingerprint,
            tenant_id=scope.tenant_id,
            database_id=scope.database_id,
            principal_id=str(snapshot["principal_id"]),
            credential_head=str(credential["head"]),
            session_head=str(credential["session_head"]),
            trust_head=str(snapshot["trust_head"]),
            materialization_head=str(snapshot["materialization_head"]),
            freshness_sequence=freshness,
            policy=challenge.policy,
            endpoint_registration=socket.endpoint_registration,
            socket_observation_fingerprint=digest(socket.canonical_bytes()),
            challenge_fingerprint=request.response.challenge_fingerprint,
            raw_digest=scope.raw_digest,
            token_state=scope.token_state,
            replay_identity=scope.replay_identity,
            original_subject=scope.original_subject,
            clock_epoch=socket.clock_epoch,
            valid_until_ns=challenge.valid_until_ns,
        )
    )
