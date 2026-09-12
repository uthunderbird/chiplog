"""Hermetic R10 component wiring; production broker integration belongs to R12."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from chiplog.adapters.driven.journal_sqlite import OWNER, SCHEMA, SQLiteJournal
from chiplog.capabilities.evidence_journal.commands import (
    ConfirmationIngress,
    JournalPort,
    JournalQueryPort,
    TrustedIngress,
)
from chiplog.capabilities.evidence_journal.journal import Journal
from chiplog.platform._sqlite import EventAppender, FenceAdvanceCommand, SQLiteMaterializer


class HermeticIngressRegistry:
    """Transport-owned provisioning, separate from application command payloads."""

    def __init__(self, peers: Mapping[str, TrustedIngress], store: SQLiteJournal) -> None:
        self._peers = dict(peers)
        self._store = store

    def confirmation_received(self, peer: str, confirmation: ConfirmationIngress) -> None:
        """Hermetic transport event injection, deliberately absent from JournalPort."""
        identity = self._peers[peer]
        self._peers[peer] = identity.model_copy(
            update={"confirmations": (*identity.confirmations, confirmation)}
        )

    def revoke(self, peer: str) -> None:
        self._peers.pop(peer, None)

    def authenticate(self, peer: str) -> TrustedIngress | None:
        identity = self._peers.get(peer)
        if identity is None:
            return None
        head = self._store.snapshot(identity.tenant).head
        return identity.model_copy(
            update={"heads": identity.heads.model_copy(update={"journal": head})}
        )


@dataclass(frozen=True)
class R10Component:
    journal: JournalPort
    storage: SQLiteJournal
    queries: JournalQueryPort
    ingress: HermeticIngressRegistry


@asynccontextmanager
async def r10_component(
    database: Path, peers: Mapping[str, TrustedIngress]
) -> AsyncIterator[R10Component]:
    materializer = SQLiteMaterializer(
        database, record_contracts={OWNER: SCHEMA, "planning": "chiplog.planning.record.v1"}
    )
    async with EventAppender(materializer, capacity=32) as appender:
        for tenant in sorted({identity.tenant for identity in peers.values()}):
            await appender.advance_fence(
                FenceAdvanceCommand(tenant, "r10", 0, allow_exact_replay=True)
            )
        storage = SQLiteJournal(database, appender, fence_generation="r10")
        ingress = HermeticIngressRegistry(peers, storage)
        journal = Journal(storage, ingress)
        yield R10Component(journal, storage, journal, ingress)
