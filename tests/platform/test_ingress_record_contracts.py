"""Bounded C-level ingress record and source interpretation evidence."""

import hashlib

import pytest

from chiplog.platform._ingress_contracts import (
    Head,
    LossSlot,
    ReceiptToken,
    SourceBinding,
    SourceClass,
    UnknownEndpoint,
)
from chiplog.platform.ingress_custody_records import reference
from chiplog.platform.ingress_record_contracts import (
    RECORD_ROWS,
    TokenRecord,
    decode_ingress_member,
    decode_ingress_record,
    encode_ingress_member,
)
from chiplog.platform.ingress_source_contracts import (
    SOURCE_ROWS,
    CliPeerObservation,
    ProviderCallbackObservation,
    ProviderPollObservation,
    ReconciliationObservation,
    TelegramPollObservation,
    TelegramPushObservation,
    ToolResultObservation,
    decode_ingress_source,
)
from chiplog.platform.ingress_transition_contracts import (
    IngressCommandIdentity,
    RetainedIngressSource,
)


def head(name: str) -> Head:
    return reference(name, ("fixture:" + name).encode())


def binding() -> SourceBinding:
    return SourceBinding(
        manifest_row=head("manifest"),
        source_class="TELEGRAM_PUSH",
        tenant_id="tenant",
        database_id="database",
        source_identity="telegram-source",
        endpoint_account_binding=UnknownEndpoint(),
        broker_epoch="epoch",
        broker_session="session",
        admission_epoch=head("epoch"),
        admission_fence=2,
        transport_version="transport-v1",
    )


def token_record() -> TokenRecord:
    source = binding()
    token = ReceiptToken(
        token_id="token-1",
        source=source,
        receive_slot="receive-1",
        maximum_bytes=32,
        predecessor=head("token-predecessor"),
        command_fingerprint=hashlib.sha256(b"command").hexdigest(),
    )
    return TokenRecord(
        record_id="record-token-1",
        tenant_id="tenant",
        database_id="database",
        command=IngressCommandIdentity(
            tenant_id="tenant", database_id="database", command_id="command-1"
        ),
        command_kind="ingress.allocate_receipt_token",
        command_fingerprint=hashlib.sha256(b"command").hexdigest(),
        predecessor=head("predecessor"),
        source_heads=(head("source-observation"),),
        token=token,
        acquisition=LossSlot(token=token),
    )


def test_record_rows_are_closed_and_member_roundtrip_preserves_exact_bytes() -> None:
    assert len(RECORD_ROWS) == 20
    record = token_record()
    member = encode_ingress_member(record)
    decoded = decode_ingress_member(member)
    assert decoded == record
    assert member.canonical_record_bytes == record.canonical_bytes()
    with pytest.raises(ValueError, match="noncanonical"):
        decode_ingress_record(record.record_kind, record.schema_id, b" " + record.canonical_bytes())
    with pytest.raises(ValueError, match="fingerprint"):
        decode_ingress_member(member.model_copy(update={"fingerprint": "0" * 64}))
    with pytest.raises(ValueError, match="unregistered"):
        decode_ingress_record("TOKEN", "chiplog.ingress.unknown.v1", record.canonical_bytes())


@pytest.mark.parametrize("kind,schema,_", RECORD_ROWS)
def test_every_registered_row_rejects_token_bytes_under_another_exact_pair(
    kind: str, schema: str, _: object
) -> None:
    record = token_record()
    if (kind, schema) == (record.record_kind, record.schema_id):
        assert decode_ingress_record(kind, schema, record.canonical_bytes()) == record
    else:
        with pytest.raises(ValueError):
            decode_ingress_record(kind, schema, record.canonical_bytes())


def assert_page_join(
    page_members: tuple[str, ...],
    disposition_members: tuple[str, ...],
    indexes: tuple[int, ...],
    candidate_cursor: bytes,
    applied_cursor: bytes,
) -> None:
    """C-only fixture join: this does not assert live publication atomicity."""
    if (
        page_members != disposition_members
        or len(page_members) != len(set(page_members))
        or indexes != tuple(range(len(page_members)))
        or candidate_cursor != applied_cursor
    ):
        raise ValueError("page/disposition/cursor structural join differs")


def test_nonempty_page_join_rejects_reordered_omitted_duplicate_and_candidate_substitution() -> (
    None
):
    members = ("member-one", "member-two")
    assert_page_join(members, members, (0, 1), b"candidate\x00", b"candidate\x00")
    for dispositions, indexes, cursor in (
        (("member-two", "member-one"), (0, 1), b"candidate\x00"),
        (("member-one",), (0,), b"candidate\x00"),
        (("member-one", "member-one"), (0, 1), b"candidate\x00"),
        (members, (0, 1), b"applied-old"),
    ):
        with pytest.raises(ValueError):
            assert_page_join(members, dispositions, indexes, b"candidate\x00", cursor)


def test_source_decode_binds_exact_reader_class_binding_and_source_head() -> None:
    assert len(SOURCE_ROWS) == 8
    source = binding()
    observation = TelegramPushObservation(
        source_binding=source,
        source_head=head("source-head"),
        original_identity="webhook-1",
        original_bytes=b"\x00page\xff",
        proof_head=head("proof-head"),
    )
    retained = RetainedIngressSource(
        source=observation.source_head,
        reader_id="telegram-push-v1",
        schema_id=observation.schema_id,
        canonical_source_bytes=observation.canonical_bytes(),
    )
    assert (
        decode_ingress_source(
            retained,
            expected_class="TELEGRAM_PUSH",
            expected_binding=source,
            registered_reader_id="telegram-push-v1",
        )
        == observation
    )


@pytest.mark.parametrize(
    "source_class,reader,cls",
    (
        ("TELEGRAM_PUSH", "telegram-push-v1", TelegramPushObservation),
        ("TELEGRAM_POLL", "telegram-poll-v1", TelegramPollObservation),
        ("CLI", "cli-peer-v1", CliPeerObservation),
        ("PROVIDER_CALLBACK", "provider-callback-v1", ProviderCallbackObservation),
        ("PROVIDER_POLL", "provider-poll-v1", ProviderPollObservation),
        ("RECONCILIATION", "reconciliation-v1", ReconciliationObservation),
        ("TOOL_RESULT", "tool-result-v1", ToolResultObservation),
    ),
)
def test_each_live_source_row_decodes_only_its_exact_reader_class_and_binding(
    source_class: SourceClass, reader: str, cls: type[TelegramPushObservation]
) -> None:
    source = binding().model_copy(update={"source_class": source_class})
    observation = cls(
        source_binding=source,
        source_head=head(reader),
        original_identity=reader,
        original_bytes=reader.encode(),
        proof_head=head("proof-" + reader),
    )
    retained = RetainedIngressSource(
        source=observation.source_head,
        reader_id=reader,
        schema_id=observation.schema_id,
        canonical_source_bytes=observation.canonical_bytes(),
    )
    assert (
        decode_ingress_source(
            retained,
            expected_class=source_class,
            expected_binding=source,
            registered_reader_id=reader,
        )
        == observation
    )
    with pytest.raises(ValueError):
        decode_ingress_source(
            retained,
            expected_class=source_class,
            expected_binding=source,
            registered_reader_id="wrong",
        )
