"""Versioned selected envelopes preserve the old wire and close H1 bindings."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)
from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
    canonical_owner_publication_bytes,
)
from chiplog.platform.owner_publications import PreparedOwnerPublication
from tests.platform.test_owner_publications import digest, request


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
    def command(owner: str) -> OwnerCommandBytes:
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
