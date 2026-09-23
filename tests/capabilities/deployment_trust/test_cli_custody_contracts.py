"""Consumer of only the declared public owner boundary; no authentication claim."""

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    BsdSocketPeer,
    CliCustodyAuthenticated,
    CliCustodyAuthenticationPort,
    CliCustodyAuthenticationRequest,
    CliCustodyChallenge,
    CliCustodyDecision,
    CliCustodyHead,
    CliCustodyOffer,
    CliCustodyReference,
    CliCustodyResponse,
    CliCustodyScope,
    CliSocketObservation,
    LinuxSocketPeer,
)


def _request() -> CliCustodyAuthenticationRequest:
    head = CliCustodyHead(identity="h", head="h/version", fingerprint="a" * 64)
    raw = b"\xff\x00exact retained bytes"
    scope = CliCustodyScope(
        tenant_id="tenant",
        database_id="database",
        token_id="receipt",
        token_state=head,
        source_profile=head,
        original_token_bytes=b'{"endpoint":"UNKNOWN_PRE_AUTH"}',
        raw_digest=hashlib.sha256(raw).hexdigest(),
        raw_byte_count=len(raw),
        replay_identity="receipt/authentication",
        original_subject="receipt",
    )
    challenge = CliCustodyChallenge(
        nonce=bytes(range(32)),
        connection_id="connection",
        scope_fingerprint=hashlib.sha256(scope.canonical_bytes()).hexdigest(),
        credential_id="credential",
        session_id="short-cli-session",
        policy=head,
        clock_epoch="clock-epoch",
        valid_until_ns=20,
    )
    return CliCustodyAuthenticationRequest(
        offer=CliCustodyOffer(scope=scope, challenge=challenge),
        socket=CliSocketObservation(
            endpoint_registration=head,
            socket_path="/registered/cli.sock",
            socket_device=1,
            socket_inode=2,
            connection_id="connection",
            peer=BsdSocketPeer(uid=501, gid=20),
            broker_epoch="broker-epoch",
            broker_session="broker-session",
            runtime_generation="generation",
            clock_epoch="clock-epoch",
            observed_at_ns=10,
            valid_until_ns=20,
        ),
        response=CliCustodyResponse(
            challenge_fingerprint=hashlib.sha256(challenge.canonical_bytes()).hexdigest()
        ),
        raw_command_bytes=raw,
        observed_time_ns=11,
    )


def test_public_boundary_retains_binary_command_and_original_token() -> None:
    request = _request()
    decoded = CliCustodyAuthenticationRequest.model_validate_json(request.canonical_bytes())
    assert decoded == request
    assert decoded.raw_command_bytes == b"\xff\x00exact retained bytes"
    assert decoded.offer.scope.original_token_bytes == b'{"endpoint":"UNKNOWN_PRE_AUTH"}'
    assert callable(CliCustodyAuthenticationPort.authenticate_custody)
    # A consumer can express only the two explicit peer mechanisms; it has no port
    # granting authority from these constructed sample observations.
    linux = LinuxSocketPeer(uid=501, gid=20, pid=123)
    assert linux.mechanism == "LINUX_SO_PEERCRED"


@pytest.mark.parametrize("value", [True, -1, "501", 501.0, 2**64])
def test_peer_identity_rejects_numeric_aliases_and_overflow(value: object) -> None:
    with pytest.raises(ValidationError):
        BsdSocketPeer.model_validate({"uid": value, "gid": 20})


def test_public_wire_rejects_grants_and_handshake_payload_extension() -> None:
    request = _request()
    raw = request.model_dump(mode="json")
    raw["authority_granted"] = True
    with pytest.raises(ValidationError):
        CliCustodyAuthenticationRequest.model_validate(raw)
    with pytest.raises(ValidationError):
        CliCustodyResponse.model_validate(
            {"challenge_fingerprint": "a" * 64, "raw_evidence": "new payload"}
        )
    with pytest.raises(ValidationError):
        CliCustodyChallenge.model_validate(
            {**request.offer.challenge.model_dump(), "nonce": b"too short"}
        )
    with pytest.raises(ValidationError):
        CliCustodyAuthenticationRequest.model_validate(
            {**request.model_dump(), "raw_command_bytes": b"x" * 65537}
        )


def test_refusal_union_has_no_optional_valid_reference_or_unknown_fallback() -> None:
    codec: TypeAdapter[CliCustodyDecision] = TypeAdapter(CliCustodyDecision)
    rejected = codec.validate_python(
        {
            "disposition": "STALE",
            "request_fingerprint": "a" * 64,
            "reason": "EXPIRED",
        }
    )
    assert rejected.disposition == "STALE"
    for value in (
        {"disposition": "VALID"},
        {"disposition": "UNKNOWN", "request_fingerprint": "a" * 64, "reason": "UNAVAILABLE"},
    ):
        with pytest.raises(ValidationError):
            codec.validate_python(value)


def test_consumer_can_retain_correlated_owner_result_without_treating_it_as_issuance() -> None:
    request = _request()
    scope, challenge = request.offer.scope, request.offer.challenge
    reference = CliCustodyReference(
        request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        tenant_id=scope.tenant_id,
        database_id=scope.database_id,
        principal_id="principal",
        credential_head="credential/head",
        session_head="session/head",
        trust_head="trust/head",
        materialization_head="materialization/head",
        freshness_sequence=1,
        policy=challenge.policy,
        endpoint_registration=request.socket.endpoint_registration,
        socket_observation_fingerprint=hashlib.sha256(request.socket.canonical_bytes()).hexdigest(),
        challenge_fingerprint=request.response.challenge_fingerprint,
        raw_digest=scope.raw_digest,
        token_state=scope.token_state,
        replay_identity=scope.replay_identity,
        original_subject=scope.original_subject,
        clock_epoch=challenge.clock_epoch,
        valid_until_ns=challenge.valid_until_ns,
    )
    # Shape fixture only: no authenticator or broker ever issued this sample.
    sample = CliCustodyAuthenticated(reference=reference)
    codec: TypeAdapter[CliCustodyDecision] = TypeAdapter(CliCustodyDecision)
    assert codec.validate_json(sample.canonical_bytes()) == sample
