"""Pure retained PRE_AUTH records: consistency is neither source auth nor custody I/O."""

import hashlib
from dataclasses import replace
from typing import Literal

import pytest

from chiplog.composition.r17_ingress_registry import retained_cli_profile
from chiplog.platform._ingress_contracts import (
    Head,
    KnownEndpoint,
    ReceiptToken,
    RetainedRetry,
    SourceBinding,
    UnknownEndpoint,
)
from chiplog.platform._ingress_domain import CustodySnapshot, IngressConflict, IngressHold
from chiplog.platform.broker import BrokerSession
from chiplog.platform.ingress_custody_records import (
    CustodyCommand,
    CustodyProfile,
    CustodyRecord,
    RetentionClaim,
    allocation_fingerprint,
    canonical,
    prepare_custody,
    reference,
    subject_id,
)

Operation = Literal[
    "ingress.allocate_receipt_token", "ingress.stage_raw_bytes", "ingress.publish_custody_successor"
]
ALLOCATE: Operation = "ingress.allocate_receipt_token"
STAGE: Operation = "ingress.stage_raw_bytes"
RETRY: Operation = "ingress.publish_custody_successor"


def _profile() -> CustodyProfile:
    return retained_cli_profile("hermetic-tenant", "hermetic-database")


def _allocation(
    raw: bytes = b"item",
    *,
    slot: str = "slot",
    maximum: int | None = None,
    profile: CustodyProfile | None = None,
    previous: Head | None = None,
) -> CustodyCommand:
    profile = _profile() if profile is None else profile
    maximum = max(1, len(raw)) if maximum is None else maximum
    claim = RetentionClaim(
        slot_id=slot,
        raw_digest=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        proof=reference(slot, b"historical-unverified-observation"),
        observation_bytes=b"historical-unverified-observation",
    )
    session = BrokerSession(
        tenant_id=profile.tenant_id,
        broker_epoch=1,
        generation_id="generation",
        owner_id="broker_ingress",
        session_id="broker-session",
    )
    token = ReceiptToken(
        token_id=subject_id(profile, slot, "ingress.receipt"),
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
        receive_slot=slot,
        maximum_bytes=maximum,
        predecessor=None,
        command_fingerprint=allocation_fingerprint(profile, claim, maximum),
    )
    return CustodyCommand(
        operation=ALLOCATE,
        command_id=subject_id(profile, slot, ALLOCATE),
        profile=profile,
        predecessor=previous,
        token=token,
        retention=claim,
        raw_bytes=None,
        broker_session=session,
        read_state_bytes=b"unverified-read-state",
        tenant_frontier=0,
    )


def _next(record: CustodyRecord, operation: Operation, raw: bytes | None = None) -> CustodyCommand:
    command = record.command
    return command.model_copy(
        update={
            "operation": operation,
            "command_id": subject_id(command.profile, command.retention.slot_id, operation),
            "predecessor": record.head(),
            "raw_bytes": raw,
            "tenant_frontier": command.tenant_frontier + 1,
        }
    )


def test_allocation_staging_retry_preserve_exact_bytes_and_do_not_authenticate() -> None:
    raw = bytes(range(256)) + b"\x00\xff"
    command = _allocation(raw)
    empty = command.profile.empty()
    allocated, first = prepare_custody(command, None, empty)
    assert empty.entries == ()
    assert allocated.resulting_entry.token == command.token
    assert allocated.resulting_entry.staged_bytes is None
    assert allocated.resulting_entry.custody is None
    staged, second = prepare_custody(_next(allocated, STAGE, raw), allocated.head(), first)
    assert first.entries[0].staged_bytes is None
    assert staged.resulting_entry.staged_bytes == raw
    assert staged.resulting_entry.staged_digest == hashlib.sha256(raw).hexdigest()
    assert staged.resulting_entry.state_head != allocated.resulting_entry.state_head
    retry, third = prepare_custody(_next(staged, RETRY), staged.head(), second)
    assert second.entries[0].custody is None
    assert retry.resulting_entry.custody == RetainedRetry(
        token=staged.resulting_entry.state_head,
        continuing_retention_proof=command.retention.proof,
    )
    assert third.entries[0].staged_bytes == raw
    assert third.entries[0].retention_proof == command.retention.proof
    for record in (allocated, staged, retry):
        assert CustodyRecord.model_validate_json(canonical(record)) == record
        assert record.head().fingerprint == hashlib.sha256(canonical(record)).hexdigest()
        assert isinstance(record.command.token.source.endpoint_account_binding, UnknownEndpoint)
        assert "authentication" not in record.resulting_entry.model_dump()
    assert prepare_custody(command, None, empty) == (allocated, first)


def test_retry_without_transfer_keeps_token_and_retention_and_prevents_late_staging() -> None:
    command = _allocation()
    allocated, first = prepare_custody(command, None, command.profile.empty())
    retry, second = prepare_custody(_next(allocated, RETRY), allocated.head(), first)
    assert retry.resulting_entry.staged_bytes is None
    assert retry.resulting_entry.retention_proof == command.retention.proof
    assert second.entries[0].custody is not None
    with pytest.raises(IngressConflict):
        prepare_custody(_next(retry, STAGE, b"item"), retry.head(), second)


@pytest.mark.parametrize("raw", (b"", b"a", bytes(65536)))
def test_exact_raw_item_bounds_include_empty_and_maximum(raw: bytes) -> None:
    command = _allocation(raw)
    allocated, snapshot = prepare_custody(command, None, command.profile.empty())
    staged, _ = prepare_custody(_next(allocated, STAGE, raw), allocated.head(), snapshot)
    assert staged.resulting_entry.staged_bytes == raw


@pytest.mark.parametrize("mutation", ("item_limit", "claim_limit"))
def test_allocation_rejects_one_byte_over_registered_or_claimed_limit(mutation: str) -> None:
    command = (
        _allocation(bytes(65537)) if mutation == "item_limit" else _allocation(b"four", maximum=3)
    )
    with pytest.raises(IngressHold):
        prepare_custody(command, None, command.profile.empty())


@pytest.mark.parametrize("raw", (None, b"ITEM", b"ite", b"items"))
def test_staging_rejects_missing_changed_same_size_shorter_or_longer_bytes(
    raw: bytes | None,
) -> None:
    command = _allocation(b"item")
    allocated, snapshot = prepare_custody(command, None, command.profile.empty())
    with pytest.raises(IngressConflict, match="raw bytes"):
        prepare_custody(_next(allocated, STAGE, raw), allocated.head(), snapshot)
    assert snapshot.entries[0].staged_bytes is None


@pytest.mark.parametrize("operation", (ALLOCATE, STAGE, RETRY))
def test_selected_operations_require_publication_replay_instead_of_new_preparation(
    operation: Operation,
) -> None:
    command = _allocation()
    record, snapshot = prepare_custody(command, None, command.profile.empty())
    if operation != ALLOCATE:
        command = _next(record, operation, b"item" if operation == STAGE else None)
        record, snapshot = prepare_custody(command, record.head(), snapshot)
    with pytest.raises(IngressConflict, match="publication replay"):
        prepare_custody(command, command.predecessor, snapshot)


@pytest.mark.parametrize("operation", (ALLOCATE, STAGE, RETRY))
@pytest.mark.parametrize("predecessor", (None, reference("foreign", b"other")))
def test_each_operation_rejects_changed_complete_predecessor(
    operation: Operation,
    predecessor: Head | None,
) -> None:
    allocation = _allocation()
    prior, snapshot = prepare_custody(allocation, None, allocation.profile.empty())
    command = _next(prior, operation, b"item" if operation == STAGE else None)
    command = command.model_copy(update={"predecessor": predecessor})
    with pytest.raises(IngressConflict, match="complete predecessor"):
        prepare_custody(command, prior.head(), snapshot)


@pytest.mark.parametrize(
    "mutation",
    (
        "claim_digest",
        "claim_size",
        "claim_proof",
        "claim_slot",
        "claim_observation",
        "token_id",
        "token_slot",
        "token_max",
        "token_fingerprint",
        "profile_limit",
        "profile_source",
        "profile_transport",
        "profile_database",
        "profile_tenant",
        "known_endpoint",
    ),
)
def test_staging_rejects_changed_claim_token_or_profile(mutation: str) -> None:
    allocation = _allocation()
    prior, snapshot = prepare_custody(allocation, None, allocation.profile.empty())
    command = _next(prior, STAGE, b"item")
    if mutation.startswith("claim_"):
        field, value = {
            "claim_digest": ("raw_digest", "b" * 64),
            "claim_size": ("byte_count", 3),
            "claim_proof": ("proof", reference("slot", b"changed")),
            "claim_slot": ("slot_id", "other"),
            "claim_observation": ("observation_bytes", b"other"),
        }[mutation]
        claim = command.retention.model_copy(update={field: value})
        # Even a re-bound allocation fingerprint cannot rewrite an allocated token.
        token = command.token.model_copy(
            update={
                "command_fingerprint": allocation_fingerprint(
                    command.profile, claim, command.token.maximum_bytes
                )
            }
        )
        command = command.model_copy(update={"retention": claim, "token": token})
    elif mutation.startswith("token_"):
        field, value = {
            "token_id": ("token_id", "other"),
            "token_slot": ("receive_slot", "other"),
            "token_max": ("maximum_bytes", 5),
            "token_fingerprint": ("command_fingerprint", "b" * 64),
        }[mutation]
        command = command.model_copy(
            update={"token": command.token.model_copy(update={field: value})}
        )
    elif mutation == "known_endpoint":
        command = command.model_copy(
            update={
                "token": command.token.model_copy(
                    update={
                        "source": command.token.source.model_copy(
                            update={
                                "endpoint_account_binding": KnownEndpoint(
                                    binding=reference("endpoint", b"x")
                                )
                            }
                        )
                    }
                )
            }
        )
    else:
        field, value = {
            "profile_limit": ("maximum_items", 9),
            "profile_source": ("source_identity", "other"),
            "profile_transport": ("transport_version", "other"),
            "profile_database": ("database_id", "other"),
            "profile_tenant": ("tenant_id", "other"),
        }[mutation]
        command = command.model_copy(
            update={"profile": command.profile.model_copy(update={field: value})}
        )
    with pytest.raises(IngressHold):
        prepare_custody(command, prior.head(), snapshot)


@pytest.mark.parametrize("operation", (ALLOCATE, STAGE, RETRY))
@pytest.mark.parametrize(
    "field,value", (("epoch_id", "other"), ("epoch_head", "other"), ("fence", 1))
)
def test_all_operations_reject_changed_snapshot_epoch_or_fence(
    operation: Operation,
    field: str,
    value: str | int,
) -> None:
    command = _allocation()
    previous = None
    snapshot = command.profile.empty()
    if operation != ALLOCATE:
        prior, snapshot = prepare_custody(command, None, snapshot)
        previous = prior.head()
        command = _next(prior, operation, b"item" if operation == STAGE else None)
    if field == "epoch_id":
        changed = replace(snapshot, epoch_id=str(value))
    elif field == "epoch_head":
        changed = replace(snapshot, epoch_head=str(value))
    else:
        changed = replace(snapshot, fence=int(value))
    with pytest.raises(IngressHold):
        prepare_custody(command, previous, changed)


def test_known_endpoint_cannot_enter_pre_auth_allocation() -> None:
    command = _allocation()
    command = command.model_copy(
        update={
            "token": command.token.model_copy(
                update={
                    "source": command.token.source.model_copy(
                        update={
                            "endpoint_account_binding": KnownEndpoint(
                                binding=reference("endpoint", b"x")
                            )
                        }
                    )
                }
            )
        }
    )
    with pytest.raises(IngressHold):
        prepare_custody(command, None, command.profile.empty())


@pytest.mark.parametrize("reserve", ("count", "total_bytes"))
def test_retained_retry_does_not_release_item_or_reserved_maximum_byte_capacity(
    reserve: str,
) -> None:
    profile = _profile().model_copy(
        update={
            "maximum_items": 2 if reserve == "count" else 10,
            "maximum_total_bytes": 100 if reserve == "count" else 8,
            "maximum_item_bytes": 4,
        }
    )
    snapshot: CustodySnapshot = profile.empty()
    previous: Head | None = None
    for ordinal in range(2):
        command = _allocation(
            b"x", slot=str(ordinal), maximum=4, profile=profile, previous=previous
        )
        record, snapshot = prepare_custody(command, previous, snapshot)
        retry, snapshot = prepare_custody(_next(record, RETRY), record.head(), snapshot)
        previous = retry.head()
    assert len(snapshot.entries) == 2
    assert all(entry.custody is not None for entry in snapshot.entries)
    assert sum(entry.token.maximum_bytes for entry in snapshot.entries) == 8
    assert all(entry.staged_bytes is None for entry in snapshot.entries)
    command = _allocation(b"x", slot="overflow", maximum=1, profile=profile, previous=previous)
    with pytest.raises(IngressHold, match="reserve exhausted"):
        prepare_custody(command, previous, snapshot)


@pytest.mark.parametrize("state", ("QUIESCING", "DRAINING", "CLOSED"))
def test_allocation_losing_open_epoch_cannot_create_a_token(
    state: Literal["QUIESCING", "DRAINING", "CLOSED"],
) -> None:
    command = _allocation()
    snapshot = replace(command.profile.empty(), state=state)
    with pytest.raises(IngressHold):
        prepare_custody(command, None, snapshot)
