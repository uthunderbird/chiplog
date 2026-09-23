"""Broker-private v2 authenticated successor shapes, with an embedded raw inbox.

No v1 token/record interpretation changes. The broker must independently validate
these proposals, including one-successor CAS, issued owner authentication and
complete historical/physical membership. DTO construction proves no admission.
"""

from typing import Literal, Protocol

from pydantic import Field

from chiplog.platform._ingress_contracts import (
    CliAuthentication,
    Digest,
    DurableCustody,
    Head,
    Identity,
    IngressDTO,
    Positive,
    ReceiptToken,
    UInt64,
)
from chiplog.platform.broker import BrokerSession
from chiplog.platform.ingress_custody_records import CustodyProfile, RetentionClaim


class AuthenticatedCustodyCommand(IngressDTO):
    schema_id: Literal["chiplog.ingress.authenticated-command.v2"] = (
        "chiplog.ingress.authenticated-command.v2"
    )
    operation: Literal["ingress.publish_custody_successor"] = "ingress.publish_custody_successor"
    command_id: Identity
    profile: CustodyProfile
    # Full mixed-version custody stream predecessor and exact per-token staged cut.
    predecessor: Head
    token: ReceiptToken
    staged_head: Head
    retention: RetentionClaim
    raw_bytes: bytes = Field(max_length=65536)
    # Owner-local public wire crosses mechanically as exact original bytes.
    authentication_request_bytes: bytes = Field(min_length=1, max_length=262144)
    authentication_result_bytes: bytes = Field(min_length=1, max_length=65536)
    broker_session: BrokerSession
    read_state_bytes: bytes = Field(min_length=1)
    tenant_frontier: UInt64


class EmbeddedCliInbox(IngressDTO):
    """Physically embedded in one custody record, never a dangling separate row."""

    schema_id: Literal["chiplog.ingress.embedded-cli-inbox.v1"] = (
        "chiplog.ingress.embedded-cli-inbox.v1"
    )
    inbox_id: Identity
    token: ReceiptToken
    staged_head: Head
    raw_bytes: bytes = Field(max_length=65536)
    raw_digest: Digest
    authentication: CliAuthentication
    authentication_request_fingerprint: Digest
    authentication_result_fingerprint: Digest


class AuthenticatedCliCustody(DurableCustody):
    authentication: CliAuthentication


class AuthenticatedCustodyRecord(IngressDTO):
    schema_id: Literal["chiplog.ingress.authenticated-record.v2"] = (
        "chiplog.ingress.authenticated-record.v2"
    )
    command: AuthenticatedCustodyCommand
    inbox: EmbeddedCliInbox
    custody: AuthenticatedCliCustody


class AdmittedInboxObservation(IngressDTO):
    """Reader returns exact containing record plus its independent selected identity."""

    selected_decision: Head
    physical_record: Head
    commit_sequence: Positive
    record: AuthenticatedCustodyRecord


class AdmittedInboxReader(Protocol):
    """Broker-private reader after complete selected/physical history verification.

    None means no selected ADMITTED_DURABLE successor for the requested token;
    corrupt, unknown, omitted or multiply selected records raise integrity failure.
    It does not request current peer/source authentication for historical bytes.
    """

    def read_admitted_inbox(self, token_id: str) -> AdmittedInboxObservation | None: ...
