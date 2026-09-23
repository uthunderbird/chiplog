"""Historical v2 custody equality and mechanical owner-result/inbox framing."""

import json

from chiplog.capabilities.deployment_trust.cli_custody_contracts import CliCustodyAuthenticated
from chiplog.capabilities.deployment_trust.cli_custody_validation import (
    CliCustodyOwnerCall,
    evaluate_cli_custody,
)
from chiplog.platform._ingress_contracts import CliAuthentication, Head, UnknownEndpoint
from chiplog.platform._ingress_domain import CustodySnapshot, publish_custody
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess
from chiplog.platform.ingress_authenticated_contracts import (
    AuthenticatedCliCustody,
    AuthenticatedCustodyCommand,
    AuthenticatedCustodyRecord,
    EmbeddedCliInbox,
)
from chiplog.platform.ingress_custody_records import canonical, digest, reference, subject_id

COMMAND_SCHEMA = "chiplog.ingress.authenticated-command.v2"
RECORD_SCHEMA = "chiplog.ingress.authenticated-record.v2"
OPERATION = "deployment_trust.authenticate_cli_custody"


def frame(value: PublicPortCall | PublicPortSuccess) -> bytes:
    return json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def decode_authentication(
    command: AuthenticatedCustodyCommand,
) -> tuple[CliCustodyOwnerCall, CliCustodyAuthenticated]:
    if (
        len(command.authentication_request_bytes) > 262144
        or len(command.authentication_result_bytes) > 65536
    ):
        raise ValueError("authenticated custody owner wire exceeds bound")
    sent = PublicPortCall.model_validate_json(command.authentication_request_bytes)
    returned = PublicPortSuccess.model_validate_json(command.authentication_result_bytes)
    original = CliCustodyOwnerCall.model_validate_json(sent.canonical_payload)
    result = CliCustodyAuthenticated.model_validate_json(returned.canonical_payload)
    request = original.request
    scope = request.offer.scope
    if (
        frame(sent) != command.authentication_request_bytes
        or frame(returned) != command.authentication_result_bytes
        or sent.operation_id != OPERATION
        or sent.schema_id != "chiplog.cli.custody-owner-call.v1"
        or sent.caller != command.broker_session
        or sent.callee.owner_id != "deployment_trust"
        or (sent.callee.tenant_id, sent.callee.broker_epoch, sent.callee.generation_id)
        != (sent.caller.tenant_id, sent.caller.broker_epoch, sent.caller.generation_id)
        or returned.responder != sent.callee
        or returned.request_id != sent.request_id
        or returned.schema_id != "chiplog.cli.custody-decision.v1"
        or original.canonical_bytes() != sent.canonical_payload
        or result.canonical_bytes() != returned.canonical_payload
        or evaluate_cli_custody(original) != result
        or scope.original_token_bytes != canonical(command.token)
        or scope.token_state.model_dump() != command.staged_head.model_dump()
        or scope.source_profile.model_dump() != command.profile.head().model_dump()
        or scope.replay_identity != command.command_id
        or scope.original_subject != command.token.token_id
        or request.raw_command_bytes != command.raw_bytes
        or request.socket.broker_epoch != str(command.broker_session.broker_epoch)
        or request.socket.broker_session != command.broker_session.session_id
        or request.socket.runtime_generation != command.broker_session.generation_id
        or request.socket.clock_epoch != command.broker_session.generation_id
        or sent.budget.absolute_deadline_ns != request.socket.valid_until_ns
    ):
        raise ValueError("historical CLI owner/source/correlation differs")
    return original, result


def prepare_authenticated_custody(
    command: AuthenticatedCustodyCommand, previous: Head | None, snapshot: CustodySnapshot
) -> tuple[AuthenticatedCustodyRecord, CustodySnapshot]:
    if (
        (snapshot.epoch_id, snapshot.epoch_head, snapshot.fence, snapshot.state)
        != (command.profile.epoch().identity, command.profile.epoch().head, 0, "OPEN")
        or command.predecessor != previous
        or command.command_id
        != subject_id(command.profile, command.retention.slot_id, command.operation)
        or not isinstance(command.token.source.endpoint_account_binding, UnknownEndpoint)
    ):
        raise ValueError("authenticated custody predecessor, identity or original endpoint differs")
    entries = [
        entry for entry in snapshot.entries if entry.token.token_id == command.token.token_id
    ]
    if (
        len(entries) != 1
        or entries[0].token != command.token
        or entries[0].state_head != command.staged_head
        or entries[0].custody is not None
        or entries[0].retention_proof != command.retention.proof
        or entries[0].staged_bytes != command.raw_bytes
        or len(command.raw_bytes) != command.retention.byte_count
        or digest(command.raw_bytes) != command.retention.raw_digest
    ):
        raise ValueError("authenticated custody requires exact unresolved staged token")
    original, result = decode_authentication(command)
    q, r = original.request, result.reference

    def wrap(field: str, value: object) -> Head:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return reference("ingress.cli." + field + ":" + r.replay_identity, raw)

    authentication = CliAuthentication(
        proof=reference(
            "ingress.cli.proof:" + r.replay_identity, command.authentication_result_bytes
        ),
        source=command.token.source,
        raw_digest=r.raw_digest,
        principal_contour=wrap(
            "principal_contour",
            {"tenant_id": r.tenant_id, "principal_id": r.principal_id, "contour": r.contour},
        ),
        freshness=wrap(
            "freshness",
            {
                "trust_head": r.trust_head,
                "materialization_head": r.materialization_head,
                "freshness_sequence": r.freshness_sequence,
                "clock_epoch": r.clock_epoch,
                "valid_until_ns": r.valid_until_ns,
            },
        ),
        replay_identity=r.replay_identity,
        original_subject=r.original_subject,
        contract_version="chiplog.cli.custody-authentication.v1",
        canonicalization_version="chiplog.ingress.sorted-json-base64-sha256.v1",
        unix_endpoint=Head.model_validate(r.endpoint_registration.model_dump()),
        os_peer=wrap(
            "os_peer",
            {
                "connection_id": q.socket.connection_id,
                "peer": q.socket.peer.model_dump(mode="json"),
            },
        ),
        authenticated_cli_session=wrap(
            "authenticated_cli_session",
            {
                "session_id": q.offer.challenge.session_id,
                "session_head": r.session_head,
                "clock_epoch": r.clock_epoch,
                "valid_until_ns": r.valid_until_ns,
            },
        ),
        credential_binding=wrap(
            "credential_binding",
            {
                "credential_id": q.offer.challenge.credential_id,
                "credential_head": r.credential_head,
            },
        ),
        policy_head=Head.model_validate(r.policy.model_dump()),
    )
    source = command.token.source
    identity = "ingress.inbox:" + digest(
        json.dumps(
            [source.tenant_id, source.database_id, source.source_identity, command.token.token_id],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )
    inbox = EmbeddedCliInbox(
        inbox_id=identity,
        token=command.token,
        staged_head=command.staged_head,
        raw_bytes=command.raw_bytes,
        raw_digest=digest(command.raw_bytes),
        authentication=authentication,
        authentication_request_fingerprint=digest(command.authentication_request_bytes),
        authentication_result_fingerprint=digest(command.authentication_result_bytes),
    )
    custody = AuthenticatedCliCustody(
        token=command.staged_head,
        raw_bytes=command.raw_bytes,
        raw_digest=digest(command.raw_bytes),
        authentication=authentication,
        inbox=reference(identity, canonical(inbox)),
    )
    record = AuthenticatedCustodyRecord(command=command, inbox=inbox, custody=custody)
    return record, publish_custody(snapshot, command.token.token_id, custody)
