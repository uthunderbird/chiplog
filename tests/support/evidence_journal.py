from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from chiplog.capabilities.evidence_journal.boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    SourceReference,
)
from chiplog.capabilities.evidence_journal.commands import (
    Claim,
    Command,
    ConfirmationIngress,
    Heads,
    Payload,
    Provenance,
    Subject,
    TrustedIngress,
)
from chiplog.composition.r10 import R10Component


def fixture(
    utterance: str = "Я выполнил occurrence:pool 2026-08-18.",
    source: Literal["principal", "model", "provider"] = "principal",
    ingress_id: str = "ingress-1",
) -> tuple[TrustedIngress, Command]:
    provenance = Provenance(
        ingress_id=ingress_id,
        ingress_version=1,
        utterance=utterance,
        digest=hashlib.sha256(utterance.encode()).hexdigest(),
        source=source,
    )
    label = DisclosureLabel(
        lattice_version="chiplog.disclosure.v1", value="UNRESTRICTED", allowed_endpoints=()
    )
    reference = SourceReference(
        tenant_id="tenant",
        owner="conversation",
        record_id=ingress_id,
        record_version="1",
        content_digest=provenance.digest,
        label_head="label-1",
        label=label,
    )
    heads = Heads(
        journal=0,
        policy="policy-1",
        credential="credential-1",
        session="session-1",
        contour="contour-1",
        deletion="deletion-1",
    )
    identity = TrustedIngress(
        tenant="tenant",
        principal="owner",
        heads=heads,
        items=(provenance,),
        sources=(reference,),
        endpoint="cli",
    )
    outcome: Literal["completed", "not_completed"] = (
        "not_completed" if "не ходил" in utterance or "не выполнил" in utterance else "completed"
    )
    raw = {
        "subject": Subject(kind="occurrence", identity="pool").model_dump(),
        "payload": Payload(date="2026-08-18", outcome=outcome).model_dump(),
        "provenance": [provenance.model_dump()],
    }
    digest = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    envelope = DisclosureEnvelope(
        tenant_id="tenant",
        content_digest=digest,
        sources=(reference,),
        label=label,
        policy_head=heads.policy,
        contour_head=heads.contour,
        deletion_fence_head=heads.deletion,
    )
    claim = Claim(
        subject=Subject(kind="occurrence", identity="pool"),
        payload=Payload(date="2026-08-18", outcome=outcome),
        provenance=(provenance,),
        envelope=envelope,
    )
    command = Command(
        command_id="direct-1",
        tenant="tenant",
        principal="owner",
        action="record_fact",
        claim=claim,
        heads=heads,
    )
    return identity, command


def confirm_transport(component: R10Component, command: Command) -> None:
    payload = json.dumps(
        command.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    component.ingress.confirmation_received(
        "peer",
        ConfirmationIngress(
            confirmation_id=command.command_id, binding_digest=hashlib.sha256(payload).hexdigest()
        ),
    )


def rows(database: Path) -> tuple[tuple[object, ...], ...]:
    with closing(sqlite3.connect(database)) as connection:
        return tuple(
            connection.execute(
                "SELECT owner, record_id, canonical_bytes FROM records ORDER BY commit_sequence"
            )
        )
