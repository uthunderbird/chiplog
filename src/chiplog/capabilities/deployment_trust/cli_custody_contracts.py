"""Public inert CLI custody authentication boundary; construction grants nothing.

Composition captures an actual accepted-socket peer and binds its handshake to
already retained bytes. The trust owner decides current identity/session policy;
the broker must authenticate the owner response and revalidate at writer use.
No socket, issuance registry, authentication implementation or tenant writer lives
here. The handshake is an authorization exchange, never a raw evidence upload.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Identity = Annotated[str, Field(min_length=1, max_length=4096)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]


class CliCustodyDTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()


class CliCustodyHead(CliCustodyDTO):
    identity: Identity
    head: Identity
    fingerprint: Digest


class LinuxSocketPeer(CliCustodyDTO):
    mechanism: Literal["LINUX_SO_PEERCRED"] = "LINUX_SO_PEERCRED"
    uid: UInt64
    gid: UInt64
    pid: Annotated[int, Field(gt=0, le=2**31 - 1)]


class BsdSocketPeer(CliCustodyDTO):
    mechanism: Literal["BSD_GETPEEREID"] = "BSD_GETPEEREID"
    uid: UInt64
    gid: UInt64


SocketPeer = Annotated[LinuxSocketPeer | BsdSocketPeer, Field(discriminator="mechanism")]


class CliSocketObservation(CliCustodyDTO):
    """Claimed immutable observation; only its private live issuer proves provenance."""

    schema_id: Literal["chiplog.cli.socket-observation.v1"] = "chiplog.cli.socket-observation.v1"
    endpoint_registration: CliCustodyHead
    socket_path: Identity
    socket_device: UInt64
    socket_inode: UInt64
    connection_id: Identity
    peer: SocketPeer
    broker_epoch: Identity
    broker_session: Identity
    runtime_generation: Identity
    clock_epoch: Identity
    observed_at_ns: UInt64
    valid_until_ns: UInt64


class CliCustodyScope(CliCustodyDTO):
    """Retain original PRE_AUTH token bytes; the observed endpoint is additional."""

    tenant_id: Identity
    database_id: Identity
    token_id: Identity
    token_state: CliCustodyHead
    source_profile: CliCustodyHead
    original_token_bytes: bytes = Field(min_length=1, max_length=65536)
    raw_digest: Digest
    raw_byte_count: Annotated[int, Field(ge=0, le=65536)]
    replay_identity: Identity
    original_subject: Identity


class CliCustodyChallenge(CliCustodyDTO):
    """Broker-generated challenge; no caller chooses its token or nonce binding."""

    schema_id: Literal["chiplog.cli.custody-challenge.v1"] = "chiplog.cli.custody-challenge.v1"
    nonce: bytes = Field(min_length=32, max_length=32)
    connection_id: Identity
    scope_fingerprint: Digest
    credential_id: Identity
    session_id: Identity
    policy: CliCustodyHead
    clock_epoch: Identity
    valid_until_ns: UInt64


class CliCustodyResponse(CliCustodyDTO):
    """Exact client confirmation from the same observed connection, not evidence."""

    schema_id: Literal["chiplog.cli.custody-response.v1"] = "chiplog.cli.custody-response.v1"
    challenge_fingerprint: Digest
    disposition: Literal["CONFIRM_RETAINED_CUSTODY"] = "CONFIRM_RETAINED_CUSTODY"


class CliCustodyOffer(CliCustodyDTO):
    """Server sends both exact scope and its challenge on the observed connection."""

    schema_id: Literal["chiplog.cli.custody-offer.v1"] = "chiplog.cli.custody-offer.v1"
    scope: CliCustodyScope
    challenge: CliCustodyChallenge


class CliCustodyAuthenticationRequest(CliCustodyDTO):
    schema_id: Literal["chiplog.cli.custody-authentication.v1"] = (
        "chiplog.cli.custody-authentication.v1"
    )
    offer: CliCustodyOffer
    socket: CliSocketObservation
    response: CliCustodyResponse
    # Original exact retained command, not a normalized JSON reserialization.
    raw_command_bytes: bytes = Field(max_length=65536)
    observed_time_ns: UInt64


class CliCustodyReference(CliCustodyDTO):
    schema_id: Literal["chiplog.cli.custody-reference.v1"] = "chiplog.cli.custody-reference.v1"
    request_fingerprint: Digest
    tenant_id: Identity
    database_id: Identity
    principal_id: Identity
    contour: Literal["CLI"] = "CLI"
    credential_head: Identity
    session_head: Identity
    trust_head: Identity
    materialization_head: Identity
    freshness_sequence: UInt64
    policy: CliCustodyHead
    endpoint_registration: CliCustodyHead
    socket_observation_fingerprint: Digest
    challenge_fingerprint: Digest
    raw_digest: Digest
    token_state: CliCustodyHead
    replay_identity: Identity
    original_subject: Identity
    clock_epoch: Identity
    valid_until_ns: UInt64


class CliCustodyAuthenticated(CliCustodyDTO):
    disposition: Literal["VALID"] = "VALID"
    reference: CliCustodyReference


class CliCustodyRejected(CliCustodyDTO):
    disposition: Literal["DENIED", "STALE", "INDETERMINATE"]
    request_fingerprint: Digest
    reason: Literal[
        "UNREGISTERED_ENDPOINT",
        "UNSUPPORTED_PEER_MECHANISM",
        "PEER_MISMATCH",
        "SESSION_MISMATCH",
        "CREDENTIAL_MISMATCH",
        "SCOPE_MISMATCH",
        "HANDSHAKE_MISMATCH",
        "POLICY_MISMATCH",
        "EXPIRED",
        "UNAVAILABLE",
    ]


CliCustodyDecision = Annotated[
    CliCustodyAuthenticated | CliCustodyRejected, Field(discriminator="disposition")
]


class CliCustodyAuthenticationPort(Protocol):
    """Private broker invocation of the public owner boundary; no ACK or SEND grant."""

    async def authenticate_custody(
        self, request: CliCustodyAuthenticationRequest
    ) -> CliCustodyDecision: ...
