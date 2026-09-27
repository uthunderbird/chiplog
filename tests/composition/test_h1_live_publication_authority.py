"""Fail-closed admission boundary for the live H1 writer authority.

These are deliberately only the pre-B negative witnesses.  A schema-valid
``CompleteDeliveryBatchV2`` is not a substitute for the nonserializable
capability issued by the live preparation session.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, cast

import pytest

import chiplog.composition.h1_completion_issuance as historical_issuance
import chiplog.composition.h1_historical_selected_sources as historical_sources
from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    WorkerAuthentication,
)
from chiplog.platform._sqlite import StoreAdmissionError
from chiplog.platform.owner_publications import BrokerPublicationCoordinator, OwnerDecisionJournal


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unissued_batch() -> CompleteDeliveryBatchV2:
    def command(
        owner: Literal[
            "agent_loop",
            "effects",
            "planning",
            "broker_ingress",
            "broker_dispatch",
            "conversation",
        ],
    ) -> OwnerCommandBytes:
        raw = f"{owner}-command".encode()
        return OwnerCommandBytes(
            owner=owner,
            schema_id="test.command.v1",
            canonical_bytes=raw,
            fingerprint=_digest(raw),
        )

    record = b"terminal-record"
    return CompleteDeliveryBatchV2(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="h1-command",
            command_fingerprint=_digest(b"h1-command"),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="unissued",
                issuance_fingerprint=_digest(b"unissued"),
                broker_epoch="epoch",
                broker_session="session",
                runtime_generation="generation",
                operation_subject="h1-command",
            ),
            applicability_schema="chiplog.composition.h1-completion-issuance.v1",
            applicability_bytes=b"forged",
            applicability_fingerprint=_digest(b"forged"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=0,
            expected_materialization_commitment="0" * 64,
            registry_head="registry",
            registry_fingerprint="0" * 64,
            ordered_heads=(),
            complete_manifest_fingerprint="0" * 64,
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
                fingerprint=_digest(record),
            ),
        ),
        complete_batch_fingerprint="0" * 64,
    )


class _UnexpectedAppender:
    async def submit(self, _: object) -> object:  # pragma: no cover - must not run
        raise AssertionError("unissued H1 batch reached physical writer admission")


@dataclass
class _Journal:
    selected: list[object] = field(default_factory=list)

    def lookup(self, _: str, __: str) -> None:
        return None

    def select(self, prepared: object, _: str) -> object:  # pragma: no cover - must not run
        self.selected.append(prepared)
        raise AssertionError("unissued H1 batch reached owner decision selection")

    def materialized(self, _: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("unissued H1 batch was marked materialized")


@pytest.mark.asyncio
async def test_unissued_h1_batch_is_rejected_before_historical_validation_or_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DTO forged from the public wire cannot bootstrap first selection."""
    monkeypatch.setattr(
        historical_issuance,
        "validate_h1_completion_issuance",
        lambda *_: pytest.fail("live authority used historical issuance validation"),
    )
    monkeypatch.setattr(
        historical_sources,
        "bind_selected_h1_completion",
        lambda *_: pytest.fail("live authority bound selected H1 before selection"),
    )
    journal = _Journal()
    coordinator = BrokerPublicationCoordinator(
        _UnexpectedAppender(),  # type: ignore[arg-type]
        H1LivePublicationAuthority(),
        cast(OwnerDecisionJournal, journal),
    )

    result = await coordinator.commit(_unissued_batch())

    assert isinstance(result, PublicationRejected)
    assert result.kind == "DENIED"
    assert journal.selected == []


def test_non_h1_prepared_object_is_denied_without_a_current_check() -> None:
    authority = H1LivePublicationAuthority()

    assert authority.check_prepared(object()) == "DENIED"  # type: ignore[arg-type]


def test_unissued_h1_physical_resolver_fails_closed() -> None:
    authority = H1LivePublicationAuthority()

    with pytest.raises(StoreAdmissionError, match="capability is not mounted"):
        authority.verify(object(), "ABSENT")  # type: ignore[arg-type]
