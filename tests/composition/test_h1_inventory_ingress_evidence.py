from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.composition.h1_inventory_ingress_evidence import (
    REGISTRATIONS,
    decode_h1_ingress_evidence_scope,
)
from chiplog.composition.h1_inventory_leaf_contracts import (
    H1OwnerInventoryFailure,
    H1RawInventoryItem,
)
from chiplog.composition.h1_owner_inventory import _physical_item, _PhysicalRow
from chiplog.composition.r10 import r10_component
from chiplog.composition.r17_ingress_registry import retained_cli_profile
from chiplog.platform._ingress_contracts import (
    CliAuthentication,
    ReceiptToken,
    SourceBinding,
    UnknownEndpoint,
)
from chiplog.platform.broker import BrokerSession
from chiplog.platform.ingress_authenticated_contracts import (
    AuthenticatedCliCustody,
    AuthenticatedCustodyCommand,
    AuthenticatedCustodyRecord,
    EmbeddedCliInbox,
)
from chiplog.platform.ingress_custody_records import (
    CustodyCommand,
    RetentionClaim,
    allocation_fingerprint,
    canonical,
    prepare_custody,
    reference,
    subject_id,
)
from tests.support.evidence_journal import fixture


def _metadata(*, raw: bytes, state: str = "LOCAL_ACK_AUTHORIZED") -> bytes:
    return json.dumps(
        {
            "attempt_id": None,
            "cursor": None,
            "domain": "chiplog.h1.evidence-inbox-metadata.v1",
            "evidence_id": "evidence-1",
            "fingerprint": hashlib.sha256(raw).hexdigest(),
            "followup_kind": "PUSH",
            "source_id": "source-1",
            "state": state,
            "transport_version": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _inbox(
    *, raw: bytes = b"retained evidence", state: str = "LOCAL_ACK_AUTHORIZED"
) -> H1RawInventoryItem:
    fingerprint = hashlib.sha256(raw).hexdigest()
    return H1RawInventoryItem(
        surface="EVIDENCE_INBOX",
        locator="evidence_inbox/tenant-1/source-1/evidence-1",
        tenant="tenant-1",
        owner="evidence_inbox",
        schema="chiplog.evidence-inbox.row.v1",
        record_kind="evidence.inbox",
        record_id="evidence-1",
        fingerprint=fingerprint,
        raw=raw,
        metadata_bytes=_metadata(raw=raw, state=state),
    )


def test_decodes_exact_inbox_row_metadata_before_any_scope_filter() -> None:
    decoded = decode_h1_ingress_evidence_scope(_inbox())

    assert decoded.locator == "evidence_inbox/tenant-1/source-1/evidence-1"
    assert {(key.namespace, key.identity) for key in decoded.identities} == {
        ("evidence", "evidence-1"),
        ("source", "source-1"),
    }
    relations = [
        (edge.relation, edge.subject.identity, edge.target.identity) for edge in decoded.relations
    ]
    assert relations == [
        ("EVIDENCE_SOURCE", "evidence-1", "source-1"),
    ]
    assert decoded.families == ("EVIDENCE", "SOURCE")


def test_unknown_inbox_metadata_semantics_are_unsupported() -> None:
    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED") as raised:
        decode_h1_ingress_evidence_scope(_inbox(state="FUTURE_STATE"))

    assert raised.value.locator == "evidence_inbox/tenant-1/source-1/evidence-1"


def test_inbox_metadata_fingerprint_mismatch_is_corrupt() -> None:
    item = _inbox()
    bad_metadata = json.loads(item.metadata_bytes or b"{}")
    bad_metadata["fingerprint"] = "0" * 64
    corrupt = replace(
        item,
        metadata_bytes=json.dumps(bad_metadata, sort_keys=True, separators=(",", ":")).encode(),
    )

    with pytest.raises(H1OwnerInventoryFailure, match="CORRUPT"):
        decode_h1_ingress_evidence_scope(corrupt)


def test_generic_ingress_dto_schema_is_not_native_custody_registration() -> None:
    item = H1RawInventoryItem(
        surface="PHYSICAL",
        locator="records/generic",
        tenant="tenant-1",
        owner="broker_ingress",
        schema="chiplog.ingress.token-record.v1",
        record_kind="ingress.custody",
        record_id="generic",
        fingerprint="0" * 64,
        raw=b"{}",
    )

    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED"):
        decode_h1_ingress_evidence_scope(item)


def test_registrations_name_native_custody_and_evidence_surfaces() -> None:
    registered = {
        (entry.surface, entry.owner, entry.schema, entry.record_kind) for entry in REGISTRATIONS
    }

    assert (
        "PHYSICAL",
        "broker_ingress",
        "chiplog.ingress.custody-record.v1",
        None,
    ) in registered
    assert (
        "PHYSICAL",
        "broker_ingress",
        "chiplog.ingress.authenticated-record.v2",
        None,
    ) in registered
    assert (
        "EVIDENCE_INBOX",
        "evidence_inbox",
        "chiplog.evidence-inbox.row.v1",
        "evidence.inbox",
    ) in registered
    assert (
        "PHYSICAL",
        "evidence_journal",
        "chiplog.evidence_journal.record.v1",
        None,
    ) in registered


async def test_decodes_canonical_evidence_journal_record_before_scope_filter(
    tmp_path: Path,
) -> None:
    identity, command = fixture()
    async with r10_component(tmp_path / "evidence.sqlite", {"peer": identity}) as component:
        outcome = await component.journal.execute("peer", command)
        assert outcome.record is not None
        raw = outcome.record.canonical_bytes()
        decoded = decode_h1_ingress_evidence_scope(
            H1RawInventoryItem(
                surface="PHYSICAL",
                locator="records/evidence-0",
                tenant=outcome.record.tenant,
                owner="evidence_journal",
                schema="chiplog.evidence_journal.record.v1",
                record_kind=None,
                record_id=outcome.record.record_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
                raw=raw,
            )
        )

    assert decoded.locator == "records/evidence-0"
    assert {key.namespace for key in decoded.identities} == {"record", "evidence", "source"}


def test_decodes_actual_native_custody_record_without_authority_claim() -> None:
    profile = retained_cli_profile("hermetic-tenant", "hermetic-database")
    raw = b"raw ingress"
    claim = RetentionClaim(
        slot_id="slot",
        raw_digest=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        proof=reference("slot", b"historical observation"),
        observation_bytes=b"historical observation",
    )
    session = BrokerSession(
        tenant_id=profile.tenant_id,
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker_ingress",
        session_id="broker-session",
    )
    token = ReceiptToken(
        token_id=subject_id(profile, "slot", "ingress.receipt"),
        source=SourceBinding(
            manifest_row=profile.head(),
            source_class=profile.source_class,
            tenant_id=profile.tenant_id,
            database_id=profile.database_id,
            source_identity=profile.source_identity,
            endpoint_account_binding=UnknownEndpoint(),
            broker_epoch="1",
            broker_session=session.session_id,
            admission_epoch=profile.epoch(),
            admission_fence=0,
            transport_version=profile.transport_version,
        ),
        receive_slot="slot",
        maximum_bytes=len(raw),
        predecessor=None,
        command_fingerprint=allocation_fingerprint(profile, claim, len(raw)),
    )
    command = CustodyCommand(
        operation="ingress.allocate_receipt_token",
        command_id=subject_id(profile, "slot", "ingress.allocate_receipt_token"),
        profile=profile,
        predecessor=None,
        token=token,
        retention=claim,
        raw_bytes=None,
        broker_session=session,
        read_state_bytes=b"unverified-read-state",
        tenant_frontier=0,
    )
    allocation, snapshot = prepare_custody(command, None, profile.empty())
    staged_command = command.model_copy(
        update={
            "operation": "ingress.stage_raw_bytes",
            "command_id": subject_id(profile, "slot", "ingress.stage_raw_bytes"),
            "predecessor": allocation.head(),
            "raw_bytes": raw,
            "tenant_frontier": 1,
        }
    )
    staged, snapshot = prepare_custody(staged_command, allocation.head(), snapshot)
    retry_command = staged_command.model_copy(
        update={
            "operation": "ingress.publish_custody_successor",
            "command_id": subject_id(profile, "slot", "ingress.publish_custody_successor"),
            "predecessor": staged.head(),
            "raw_bytes": None,
            "tenant_frontier": 2,
        }
    )
    retry, _ = prepare_custody(retry_command, staged.head(), snapshot)

    for index, record in enumerate((allocation, staged, retry)):
        encoded = canonical(record)
        item = _physical_item(
            _PhysicalRow(
                record.head().head,
                "broker_ingress",
                "chiplog.ingress.custody-record.v1",
                encoded,
                index + 1,
            ),
            profile.tenant_id,
        )
        assert item.record_kind is None
        decoded = decode_h1_ingress_evidence_scope(item)

        assert decoded.locator == "records/" + record.head().head
        assert {key.namespace for key in decoded.identities} == {"record", "evidence", "source"}


def test_native_custody_operation_is_not_a_physical_record_kind() -> None:
    profile = retained_cli_profile("hermetic-tenant", "hermetic-database")
    raw = b"raw ingress"
    claim = RetentionClaim(
        slot_id="slot",
        raw_digest=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        proof=reference("slot", b"historical observation"),
        observation_bytes=b"historical observation",
    )
    session = BrokerSession(
        tenant_id=profile.tenant_id,
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker_ingress",
        session_id="broker-session",
    )
    token = ReceiptToken(
        token_id=subject_id(profile, "slot", "ingress.receipt"),
        source=SourceBinding(
            manifest_row=profile.head(),
            source_class=profile.source_class,
            tenant_id=profile.tenant_id,
            database_id=profile.database_id,
            source_identity=profile.source_identity,
            endpoint_account_binding=UnknownEndpoint(),
            broker_epoch="1",
            broker_session=session.session_id,
            admission_epoch=profile.epoch(),
            admission_fence=0,
            transport_version=profile.transport_version,
        ),
        receive_slot="slot",
        maximum_bytes=len(raw),
        predecessor=None,
        command_fingerprint=allocation_fingerprint(profile, claim, len(raw)),
    )
    command = CustodyCommand(
        operation="ingress.allocate_receipt_token",
        command_id=subject_id(profile, "slot", "ingress.allocate_receipt_token"),
        profile=profile,
        predecessor=None,
        token=token,
        retention=claim,
        raw_bytes=None,
        broker_session=session,
        read_state_bytes=b"unverified-read-state",
        tenant_frontier=0,
    )
    record, _ = prepare_custody(command, None, profile.empty())
    encoded = canonical(record)

    with pytest.raises(H1OwnerInventoryFailure, match="UNSUPPORTED"):
        decode_h1_ingress_evidence_scope(
            H1RawInventoryItem(
                surface="PHYSICAL",
                locator="records/0",
                tenant=profile.tenant_id,
                owner="broker_ingress",
                schema="chiplog.ingress.custody-record.v1",
                record_kind="ingress.allocate_receipt_token",
                record_id=record.head().head,
                fingerprint=hashlib.sha256(encoded).hexdigest(),
                raw=encoded,
            )
        )


def test_decodes_canonical_authenticated_custody_through_physical_item() -> None:
    profile = retained_cli_profile("hermetic-tenant", "hermetic-database")
    raw = b"authenticated ingress"
    claim = RetentionClaim(
        slot_id="slot",
        raw_digest=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        proof=reference("slot", b"historical observation"),
        observation_bytes=b"historical observation",
    )
    session = BrokerSession(
        tenant_id=profile.tenant_id,
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker_ingress",
        session_id="broker-session",
    )
    token = ReceiptToken(
        token_id="token-1",
        source=SourceBinding(
            manifest_row=profile.head(),
            source_class=profile.source_class,
            tenant_id=profile.tenant_id,
            database_id=profile.database_id,
            source_identity=profile.source_identity,
            endpoint_account_binding=UnknownEndpoint(),
            broker_epoch="1",
            broker_session=session.session_id,
            admission_epoch=profile.epoch(),
            admission_fence=0,
            transport_version=profile.transport_version,
        ),
        receive_slot="slot",
        maximum_bytes=len(raw),
        predecessor=None,
        command_fingerprint="a" * 64,
    )
    auth_head = reference("auth", b"auth")
    authentication = CliAuthentication(
        proof=auth_head,
        source=token.source,
        raw_digest=hashlib.sha256(raw).hexdigest(),
        principal_contour=auth_head,
        freshness=auth_head,
        replay_identity="replay-1",
        original_subject=token.token_id,
        contract_version="chiplog.cli.custody-authentication.v1",
        canonicalization_version="chiplog.ingress.sorted-json-base64-sha256.v1",
        unix_endpoint=auth_head,
        os_peer=auth_head,
        authenticated_cli_session=auth_head,
        credential_binding=auth_head,
        policy_head=auth_head,
    )
    command = AuthenticatedCustodyCommand(
        command_id="command-1",
        profile=profile,
        predecessor=auth_head,
        token=token,
        staged_head=auth_head,
        retention=claim,
        raw_bytes=raw,
        authentication_request_bytes=b"request",
        authentication_result_bytes=b"result",
        broker_session=session,
        read_state_bytes=b"state",
        tenant_frontier=1,
    )
    inbox = EmbeddedCliInbox(
        inbox_id="inbox-1",
        token=token,
        staged_head=auth_head,
        raw_bytes=raw,
        raw_digest=hashlib.sha256(raw).hexdigest(),
        authentication=authentication,
        authentication_request_fingerprint=hashlib.sha256(b"request").hexdigest(),
        authentication_result_fingerprint=hashlib.sha256(b"result").hexdigest(),
    )
    record = AuthenticatedCustodyRecord(
        command=command,
        inbox=inbox,
        custody=AuthenticatedCliCustody(
            token=auth_head,
            raw_bytes=raw,
            raw_digest=hashlib.sha256(raw).hexdigest(),
            authentication=authentication,
            inbox=reference(inbox.inbox_id, canonical(inbox)),
        ),
    )
    encoded = canonical(record)
    item = _physical_item(
        _PhysicalRow(
            record.command.command_id + "/record/" + hashlib.sha256(encoded).hexdigest(),
            "broker_ingress",
            "chiplog.ingress.authenticated-record.v2",
            encoded,
            1,
        ),
        profile.tenant_id,
    )

    assert item.record_kind is None
    assert item.record_id is not None
    decoded = decode_h1_ingress_evidence_scope(item)

    assert decoded.locator == "records/" + item.record_id
    assert {key.namespace for key in decoded.identities} == {"record", "evidence", "source"}
