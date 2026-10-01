"""Versioned selected envelopes preserve the old wire and close H1 bindings."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_GENESIS_COMMAND_SCHEMA,
    DELIVERY_WORK_OWNER,
    DELIVERY_WORK_SCHEMA,
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    PrepareDeliveryWorkGenesisV1,
    prepare_delivery_work_genesis,
)
from chiplog.composition.h1_historical_selected_sources import _selected_owner_decision
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    DeliveryWorkGenesisBatchV1,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)
from chiplog.platform._sqlite import EventAppender, FenceAdvanceCommand, SQLiteMaterializer
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.delivery_work_genesis_binding_contracts import DeliveryWorkGenesisBindingV1
from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
    canonical_owner_publication_bytes,
)
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    PreparedOwnerPublication,
)
from tests.platform.test_h1_publication_guard import _Resolver
from tests.platform.test_owner_publications import FixtureAuthority, digest, request


def _binding(batch: CompleteDeliveryBatchV2) -> H1DeliveryBinding:
    return H1DeliveryBinding(
        deployment_id="deployment",
        database_id="database",
        database_genesis_digest=digest(b"genesis"),
        tenant_id=batch.identity.tenant_id,
        journal_instance_id="evidence-journal",
        closure_entry_id=digest(b"closure-entry"),
        closure_payload_digest=digest(b"closure-payload"),
        command_id=batch.identity.command_id,
        command_fingerprint=batch.identity.command_fingerprint,
        request_digest=hashlib.sha256(canonical_owner_publication_bytes(batch)).hexdigest(),
    )


def _h1_batch() -> CompleteDeliveryBatchV2:
    def command(owner: Literal["agent_loop", "conversation", "effects"]) -> OwnerCommandBytes:
        raw = f"{owner}-command".encode()
        return OwnerCommandBytes(
            owner=owner,
            schema_id="test.command.v1",
            canonical_bytes=raw,
            fingerprint=digest(raw),
        )

    record = b"terminal-record"
    return CompleteDeliveryBatchV2(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="h1-command",
            command_fingerprint=digest(b"h1-command"),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="issued",
                issuance_fingerprint=digest(b"issued"),
                broker_epoch="epoch",
                broker_session="session",
                runtime_generation="generation",
                operation_subject="h1-command",
            ),
            applicability_schema="chiplog.composition.h1-completion-issuance.v1",
            applicability_bytes=b"issued-evidence",
            applicability_fingerprint=digest(b"issued-evidence"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=0,
            expected_materialization_commitment=digest(b"before"),
            registry_head="registry",
            registry_fingerprint=digest(b"registry"),
            ordered_heads=(),
            complete_manifest_fingerprint=digest(b"manifest"),
        ),
        loop_command=command("agent_loop"),
        conversation_command=command("conversation"),
        terminal_work_command=command("agent_loop"),
        prepared_effects_commands=(command("effects"),),
        complete_records=(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind="TERMINAL",
                record_id="terminal",
                schema_id="test.terminal.v1",
                canonical_bytes=record,
                fingerprint=digest(record),
            ),
        ),
        complete_batch_fingerprint=digest(b"complete"),
    )


def _journal(tmp_path: Path) -> IndependentOwnerDecisionJournal:
    return IndependentOwnerDecisionJournal(
        IndependentTenantDecisionJournal(tmp_path / "journal"), "tenant"
    )


def _prepared(batch: CompleteDeliveryBatchV2) -> PreparedOwnerPublication:
    return PreparedOwnerPublication(
        batch,
        "issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
        _binding(batch),
    )


def _genesis(prior) -> tuple[DeliveryWorkGenesisBatchV1, DeliveryWorkGenesisBindingV1]:
    prior_batch = prior.prepared.request
    assert type(prior_batch) is CompleteDeliveryBatchV2
    run, manifest, intent = prior_batch.complete_records

    def source_member(record: OwnerRecordBytes) -> DeliveryWorkSourceMemberV1:
        return DeliveryWorkSourceMemberV1(
            owner=record.owner,
            record_kind=record.record_kind,
            record_id=record.record_id,
            schema_id=record.schema_id,
            canonical_record_bytes=record.canonical_bytes,
            fingerprint=record.fingerprint,
        )

    source = DeliveryWorkSourceV1(
        tenant_id="tenant",
        intent_id=intent.record_id,
        selected_decision_id=prior.decision_id,
        selected_decision_fingerprint=prior.decision_fingerprint,
        terminal_run=source_member(run),
        terminal_manifest=source_member(manifest),
        intent=source_member(intent),
    )
    command = PrepareDeliveryWorkGenesisV1(
        selected_decision_id=prior.decision_id,
        selected_decision_fingerprint=prior.decision_fingerprint,
        selected_source=source,
    )
    state = prepare_delivery_work_genesis(source)
    record = OwnerRecordBytes(
        owner=DELIVERY_WORK_OWNER,
        record_kind="DELIVERY_WORK_STATE",
        record_id=state.record_id,
        schema_id=DELIVERY_WORK_SCHEMA,
        canonical_bytes=state.canonical_bytes(),
        fingerprint=digest(state.canonical_bytes()),
    )
    batch = DeliveryWorkGenesisBatchV1(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="delivery-work-genesis",
            command_fingerprint=digest(b"delivery-work-genesis"),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=prior_batch.authentication,
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=1,
            expected_materialization_commitment=prior.resulting_commitment,
            registry_head="registry",
            registry_fingerprint=digest(b"registry"),
            ordered_heads=(),
            complete_manifest_fingerprint=digest(b"manifest"),
        ),
        selected_decision_id=prior.decision_id,
        selected_decision_fingerprint=prior.decision_fingerprint,
        selected_source=source,
        genesis_command=OwnerCommandBytes(
            owner=DELIVERY_WORK_OWNER,
            schema_id=DELIVERY_WORK_GENESIS_COMMAND_SCHEMA,
            canonical_bytes=command.canonical_bytes(),
            fingerprint=digest(command.canonical_bytes()),
        ),
        complete_records=(record,),
        complete_batch_fingerprint=digest(b"delivery-work-complete"),
    )
    binding = DeliveryWorkGenesisBindingV1(
        selected_decision_id=prior.decision_id,
        selected_decision_fingerprint=prior.decision_fingerprint,
        selected_source=source,
        request_digest=hashlib.sha256(canonical_owner_publication_bytes(batch)).hexdigest(),
    )
    return batch, binding


def _h1_batch_with_selected_members(*, applicability_schema: str) -> CompleteDeliveryBatchV2:
    h1_batch = _h1_batch()
    return h1_batch.model_copy(
        update={
            "authentication": h1_batch.authentication.model_copy(
                update={"applicability_schema": applicability_schema}
            ),
            "complete_records": (
                h1_batch.complete_records[0].model_copy(update={"record_kind": "Run"}),
                OwnerRecordBytes(
                    owner="agent_loop",
                    record_kind="TERMINAL_MANIFEST",
                    record_id="manifest",
                    schema_id="test.manifest.v1",
                    canonical_bytes=b"manifest",
                    fingerprint=digest(b"manifest"),
                ),
                OwnerRecordBytes(
                    owner="effects",
                    record_kind="ExternalActionIntent",
                    record_id="intent",
                    schema_id="test.intent.v1",
                    canonical_bytes=b"intent",
                    fingerprint=digest(b"intent"),
                ),
            ),
        }
    )


def _v3_genesis_fixture(tmp_path: Path):
    journal = _journal(tmp_path)
    prior_batch = _h1_batch_with_selected_members(
        applicability_schema="chiplog.composition.h1-completion-issuance.v4"
    )
    prior = journal.select(_prepared(prior_batch), digest(b"after"))
    journal.materialized(prior)
    batch, binding = _genesis(prior)
    return journal, batch, binding


def test_v3_delivery_work_genesis_selected_roundtrips_exact_prior_v4_binding(
    tmp_path: Path,
) -> None:
    journal, batch, binding = _v3_genesis_fixture(tmp_path)
    prepared = PreparedOwnerPublication(
        batch,
        "genesis-issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
        delivery_work_genesis_binding=binding,
    )
    selected = journal.select(prepared, digest(b"after-genesis"))

    assert b'"schema_id":"chiplog.owner-decision.v3"' in journal._raw.entries()[-1][2]
    reopened = _journal(tmp_path).lookup("tenant", batch.identity.command_id)
    assert reopened == selected
    assert reopened is not None
    assert reopened.prepared.delivery_work_genesis_binding == binding


def test_v3_delivery_work_genesis_rejects_selected_non_v4_predecessor(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    prior = journal.select(
        _prepared(
            _h1_batch_with_selected_members(
                applicability_schema="chiplog.composition.h1-completion-issuance.v1"
            )
        ),
        digest(b"after"),
    )
    journal.materialized(prior)
    batch, binding = _genesis(prior)
    prepared = PreparedOwnerPublication(
        batch,
        "genesis-issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
        delivery_work_genesis_binding=binding,
    )

    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(prepared, digest(b"after-genesis"))
    assert len(journal._raw.entries()) == 2


def test_v3_delivery_work_genesis_rejects_swapped_terminal_source_roles(tmp_path: Path) -> None:
    journal, batch, binding = _v3_genesis_fixture(tmp_path)
    source = DeliveryWorkSourceV1.model_validate(
        batch.selected_source.model_dump(mode="python")
        | {
            "terminal_run": batch.selected_source.terminal_manifest,
            "terminal_manifest": batch.selected_source.terminal_run,
        }
    )
    command = PrepareDeliveryWorkGenesisV1(
        selected_decision_id=batch.selected_decision_id,
        selected_decision_fingerprint=batch.selected_decision_fingerprint,
        selected_source=source,
    )
    state = prepare_delivery_work_genesis(source)
    record = batch.complete_records[0].model_copy(
        update={
            "record_id": state.record_id,
            "canonical_bytes": state.canonical_bytes(),
            "fingerprint": digest(state.canonical_bytes()),
        }
    )
    batch = DeliveryWorkGenesisBatchV1.model_validate(
        batch.model_dump(mode="python")
        | {
            "selected_source": source,
            "genesis_command": OwnerCommandBytes(
                owner=DELIVERY_WORK_OWNER,
                schema_id=DELIVERY_WORK_GENESIS_COMMAND_SCHEMA,
                canonical_bytes=command.canonical_bytes(),
                fingerprint=digest(command.canonical_bytes()),
            ),
            "complete_records": (record,),
        }
    )
    binding = binding.model_copy(
        update={
            "selected_source": source,
            "request_digest": hashlib.sha256(canonical_owner_publication_bytes(batch)).hexdigest(),
        }
    )
    prepared = PreparedOwnerPublication(
        batch,
        "genesis-issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
        delivery_work_genesis_binding=binding,
    )

    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(prepared, digest(b"after-genesis"))
    assert len(journal._raw.entries()) == 2


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_source",
        "expected_tenant",
        "source_tenant",
        "source",
        "record",
        "decision",
        "unknown_kind",
    ),
)
def test_v3_delivery_work_genesis_mutations_reject_before_or_on_reopen(
    tmp_path: Path, mutation: str
) -> None:
    journal, batch, binding = _v3_genesis_fixture(tmp_path)
    if mutation == "missing_source":
        raw = batch.model_dump(mode="json")
        del raw["selected_source"]
        with pytest.raises(ValueError):
            DeliveryWorkGenesisBatchV1.model_validate(raw)
        return
    if mutation == "expected_tenant":
        raw = batch.model_dump(mode="json")
        raw["expected"]["tenant_id"] = "other-tenant"
        with pytest.raises(ValueError):
            DeliveryWorkGenesisBatchV1.model_validate(raw)
        return
    if mutation == "source_tenant":
        raw = batch.model_dump(mode="json")
        raw["selected_source"]["tenant_id"] = "other-tenant"
        with pytest.raises(ValueError):
            DeliveryWorkGenesisBatchV1.model_validate(raw)
        return
    if mutation == "source":
        binding = binding.model_copy(
            update={
                "selected_source": binding.selected_source.model_copy(update={"intent_id": "rival"})
            }
        )
    elif mutation == "record":
        raw = batch.model_dump(mode="json")
        raw["complete_records"][0]["record_id"] = "delivery-work:bad:g0"
        with pytest.raises(ValueError):
            DeliveryWorkGenesisBatchV1.model_validate(raw)
        return
    elif mutation == "decision":
        binding = binding.model_copy(update={"selected_decision_id": "rival"})
    else:
        raw = batch.model_dump(mode="json")
        raw["kind"] = "UNKNOWN"
        with pytest.raises(ValueError):
            type(batch).model_validate(raw)
        return
    prepared = PreparedOwnerPublication(
        batch,
        "genesis-issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
        delivery_work_genesis_binding=binding,
    )
    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(prepared, digest(b"after-genesis"))


def test_v1_selected_golden_bytes_are_unchanged(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    before = digest(b"before")
    journal.select(
        PreparedOwnerPublication(request(before), "issued", "fence", 0, before), digest(b"after")
    )

    # Static digest of the full V1 golden entry, generated before V2 existed.
    assert hashlib.sha256(journal._raw.entries()[0][2]).hexdigest() == (
        "64711f15d84ba4ffec0dbd336cc9f479f22334175f1cab2a5193d2c8aebccb2a"
    )
    reopened = _journal(tmp_path).lookup("tenant", "command")
    assert reopened is not None
    assert reopened.prepared.h1_delivery_binding is None
    _journal(tmp_path).materialized(reopened)
    assert b'"schema_id":"chiplog.owner-decision.v1"' in _journal(tmp_path)._raw.entries()[1][2]


def test_v2_selected_roundtrips_and_materialized_retains_closed_binding(tmp_path: Path) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    prepared = _prepared(batch)
    selected = journal.select(prepared, digest(b"after"))

    selected_raw = journal._raw.entries()[0][2]
    assert b'"schema_id":"chiplog.owner-decision.v2"' in selected_raw
    assert selected.decision_fingerprint == hashlib.sha256(selected_raw).hexdigest()
    reopened = _journal(tmp_path).lookup("tenant", "h1-command")
    assert reopened == selected
    assert reopened is not None
    assert reopened.prepared.h1_delivery_binding == prepared.h1_delivery_binding
    journal.materialized(selected)
    marker = journal._raw.entries()[1][2]
    assert b'"schema_id":"chiplog.owner-decision.v1"' in marker
    assert _journal(tmp_path).snapshot().materialized_command_ids == frozenset({"h1-command"})


def _canonical_raw(entry: dict[str, object]) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()


def _open_raw(tmp_path: Path, payload: bytes) -> IndependentOwnerDecisionJournal:
    raw = IndependentTenantDecisionJournal(tmp_path / "raw")
    raw.append(payload, None)
    return IndependentOwnerDecisionJournal(raw, "tenant")


@pytest.mark.parametrize(
    "applicability_schema",
    [
        "chiplog.composition.h1-completion-issuance.v1",
        "chiplog.composition.h1-completion-issuance.v2",
    ],
)
def test_v1_selected_h1_request_without_binding_is_rejected_on_reopen(
    tmp_path: Path, applicability_schema: str
) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    batch = batch.model_copy(
        update={
            "authentication": batch.authentication.model_copy(
                update={"applicability_schema": applicability_schema}
            )
        }
    )
    selected = journal.select(_prepared(batch), digest(b"after"))
    entry = json.loads(journal._raw.entries()[0][2])
    entry["schema_id"] = "chiplog.owner-decision.v1"
    del entry["h1_delivery_binding"]

    with pytest.raises(OwnerJournalIntegrityError):
        _open_raw(tmp_path / applicability_schema, _canonical_raw(entry))
    assert selected.prepared.h1_delivery_binding is not None


@pytest.mark.parametrize("version", ("v2", "v4"))
def test_v2_selected_accepts_registered_h1_applicability_with_exact_binding(
    tmp_path: Path,
    version: str,
) -> None:
    batch = _h1_batch()
    batch = batch.model_copy(
        update={
            "authentication": batch.authentication.model_copy(
                update={
                    "applicability_schema": f"chiplog.composition.h1-completion-issuance.{version}"
                }
            )
        }
    )
    prepared = _prepared(batch)

    selected = _journal(tmp_path).select(prepared, digest(b"after"))

    assert selected.prepared.h1_delivery_binding == prepared.h1_delivery_binding


@pytest.mark.parametrize("version", ("v2", "v4"))
def test_v2_selected_rejects_missing_closed_delivery_binding_before_append(
    tmp_path: Path, version: str
) -> None:
    batch = _h1_batch().model_copy(
        update={
            "authentication": _h1_batch().authentication.model_copy(
                update={
                    "applicability_schema": f"chiplog.composition.h1-completion-issuance.{version}"
                }
            )
        }
    )
    prepared = PreparedOwnerPublication(
        batch,
        "issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
    )
    journal = _journal(tmp_path)

    with pytest.raises(OwnerJournalIntegrityError) as raised:
        journal.select(prepared, digest(b"after"))
    assert str(raised.value.__cause__) == (
        "H1 V2 selected decision requires its closed delivery binding"
    )
    assert journal._raw.entries() == ()


@pytest.mark.asyncio
async def test_v4_broker_commit_rejects_missing_binding_before_publication(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.sqlite"
    with SQLiteMaterializer(path, record_contracts={"agent_loop": "test.terminal.v1"}) as store:
        store._mount_owner_publication_resolver(_Resolver(None))
        async with EventAppender(store, capacity=4) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            batch = _h1_batch()
            batch = batch.model_copy(
                update={
                    "authentication": batch.authentication.model_copy(
                        update={
                            "applicability_schema": "chiplog.composition.h1-completion-issuance.v4"
                        }
                    ),
                    "expected": batch.expected.model_copy(
                        update={
                            "expected_materialization_commitment": (
                                capture_authority_storage_state(path)[0]
                            )
                        }
                    ),
                }
            )
            authority = FixtureAuthority(path)
            authority.authenticate_replay = lambda query: None  # type: ignore[method-assign]
            journal = _journal(tmp_path)
            broker = BrokerPublicationCoordinator(appender, authority, journal)

            with pytest.raises(OwnerJournalIntegrityError, match="select") as rejected:
                await broker.commit(batch)

            assert str(rejected.value.__cause__) == (
                "H1 V2 selected decision requires its closed delivery binding"
            )
            assert journal._raw.entries() == ()
            assert store.durable_records() == ()


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("tenant_id", "other-tenant"),
        ("command_id", "other-command"),
        ("command_fingerprint", digest(b"other-command")),
        ("request_digest", digest(b"other-request")),
    ],
)
def test_v2_selected_rejects_canonical_raw_binding_tamper_on_reopen(
    tmp_path: Path, field: str, replacement: str
) -> None:
    journal = _journal(tmp_path)
    journal.select(_prepared(_h1_batch()), digest(b"after"))
    entry = json.loads(journal._raw.entries()[0][2])
    entry["h1_delivery_binding"][field] = replacement

    with pytest.raises(OwnerJournalIntegrityError):
        _open_raw(tmp_path / field, _canonical_raw(entry))


def test_selected_rejects_cross_version_members_on_reopen(tmp_path: Path) -> None:
    v1 = _journal(tmp_path / "v1")
    before = digest(b"before")
    v1.select(
        PreparedOwnerPublication(request(before), "issued", "fence", 0, before), digest(b"after")
    )
    v1_entry = json.loads(v1._raw.entries()[0][2])
    v1_entry["schema_id"] = "chiplog.owner-decision.v2"

    v2 = _journal(tmp_path / "v2")
    v2.select(_prepared(_h1_batch()), digest(b"after"))
    v2_entry = json.loads(v2._raw.entries()[0][2])
    v2_entry["schema_id"] = "chiplog.owner-decision.v1"

    with pytest.raises(OwnerJournalIntegrityError):
        _open_raw(tmp_path / "v1-to-v2", _canonical_raw(v1_entry))
    with pytest.raises(OwnerJournalIntegrityError):
        _open_raw(tmp_path / "v2-to-v1", _canonical_raw(v2_entry))


def test_public_binding_dto_does_not_create_selected_evidence(tmp_path: Path) -> None:
    batch = _h1_batch()
    prepared = _prepared(batch)
    journal = _journal(tmp_path)

    assert prepared.h1_delivery_binding == _binding(batch)
    assert journal.snapshot().decisions == ()
    assert journal.lookup("tenant", "h1-command") is None
    with pytest.raises(ValueError, match="no unique selected owner decision"):
        _selected_owner_decision(batch, journal)


@pytest.mark.parametrize(
    "payload",
    [
        b'{"kind":"SELECTED","schema_id":"chiplog.owner-decision.v9"}',
        b'{"kind":"OTHER","schema_id":"chiplog.owner-decision.v2"}',
        b'{"kind":"SELECTED","kind":"MATERIALIZED","schema_id":"chiplog.owner-decision.v1"}',
        b'{"kind":"SELECTED","schema_id":"chiplog.owner-decision.v1","request":{"kind":"x","kind":"y"}}',
        b'{"kind":"MATERIALIZED","schema_id":"chiplog.owner-decision.v1","unknown":1}',
    ],
)
def test_journal_rejects_wrong_dispatch_duplicate_or_unknown_fields(
    tmp_path: Path, payload: bytes
) -> None:
    journal = _journal(tmp_path)
    journal._raw.append(payload, None)

    with pytest.raises(OwnerJournalIntegrityError):
        journal.snapshot()


def test_h1_selected_retry_with_changed_binding_is_rejected(tmp_path: Path) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    prepared = _prepared(batch)
    journal.select(prepared, digest(b"after"))
    assert prepared.h1_delivery_binding is not None

    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(
            replace(
                prepared,
                h1_delivery_binding=prepared.h1_delivery_binding.model_copy(
                    update={"closure_payload_digest": digest(b"changed")}
                ),
            ),
            digest(b"after"),
        )


def test_v2_selected_accepts_its_exact_v1_materialized_marker(tmp_path: Path) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    selected = journal.select(_prepared(batch), digest(b"after"))
    marker = {
        "kind": "MATERIALIZED",
        "schema_id": "chiplog.owner-decision.v1",
        "tenant_id": "tenant",
        "command_id": "h1-command",
        "decision_id": selected.decision_id,
        "decision_fingerprint": selected.decision_fingerprint,
        "resulting_commitment": selected.resulting_commitment,
    }
    journal._raw.append(
        json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(), selected.decision_head
    )

    assert journal.snapshot().materialized_command_ids == frozenset({"h1-command"})


def test_v2_materialized_marker_is_not_a_known_envelope(tmp_path: Path) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    selected = journal.select(_prepared(batch), digest(b"after"))
    marker = {
        "kind": "MATERIALIZED",
        "schema_id": "chiplog.owner-decision.v2",
        "tenant_id": "tenant",
        "command_id": "h1-command",
        "decision_id": selected.decision_id,
        "decision_fingerprint": selected.decision_fingerprint,
        "resulting_commitment": selected.resulting_commitment,
    }
    journal._raw.append(
        json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(), selected.decision_head
    )

    with pytest.raises(OwnerJournalIntegrityError):
        journal.snapshot()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("decision_id", "0" * 64),
        ("decision_fingerprint", "1" * 64),
        ("resulting_commitment", "2" * 64),
    ],
)
def test_v2_selected_rejects_changed_v1_materialized_reference(
    tmp_path: Path, field: str, replacement: str
) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    selected = journal.select(_prepared(batch), digest(b"after"))
    marker = {
        "kind": "MATERIALIZED",
        "schema_id": "chiplog.owner-decision.v1",
        "tenant_id": "tenant",
        "command_id": "h1-command",
        "decision_id": selected.decision_id,
        "decision_fingerprint": selected.decision_fingerprint,
        "resulting_commitment": selected.resulting_commitment,
    }
    marker[field] = replacement
    journal._raw.append(
        json.dumps(marker, sort_keys=True, separators=(",", ":")).encode(), selected.decision_head
    )

    with pytest.raises(OwnerJournalIntegrityError):
        journal.snapshot()


@pytest.mark.parametrize("binding", [None, "non_h1"])
def test_v2_does_not_synthesize_or_misapply_h1_binding(tmp_path: Path, binding: str | None) -> None:
    journal, batch = _journal(tmp_path), _h1_batch()
    if binding == "non_h1":
        batch = batch.model_copy(
            update={
                "authentication": batch.authentication.model_copy(
                    update={"applicability_schema": "fixture.v1"}
                )
            }
        )
    prepared = PreparedOwnerPublication(
        batch,
        "issuance",
        "fence",
        0,
        batch.expected.expected_materialization_commitment,
        None if binding is None else _binding(batch),
    )

    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(prepared, digest(b"after"))
    assert journal.snapshot().decisions == ()
