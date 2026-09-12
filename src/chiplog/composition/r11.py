"""Hermetic R11 component composition; no production runtime or provider exposure."""

from dataclasses import dataclass
from pathlib import Path

from chiplog.adapters.driven.calendar_hermetic import HermeticCalendarProvider
from chiplog.adapters.driven.calendar_reads import CalendarReadBroker
from chiplog.capabilities.calendar_observations.boundary import (
    WorkspaceQueryPort,
    WorkspaceReadContext,
)
from chiplog.capabilities.calendar_observations.contracts import CalendarBatch
from chiplog.platform.calendar_read_ledger import CalendarReadLedger, CalendarReadState


@dataclass(frozen=True)
class R11CalendarSession:
    context: WorkspaceReadContext
    queries: WorkspaceQueryPort


async def open_hermetic_calendar(
    *,
    ledger_path: Path,
    batch: CalendarBatch,
    trusted_state: CalendarReadState,
    authenticated_tenant: str,
    authenticated_principal: str,
    authenticated_channel: str,
    observed_at_ns: int,
) -> R11CalendarSession:
    """State/source-head pins and transport identity are independent trusted inputs.

    This initializer creates a fresh component ledger. Production authentication,
    provider credential management and R12 family composition are deliberately absent.
    """
    ledger = CalendarReadLedger(ledger_path)
    ledger.publish_initial_state(trusted_state)
    broker = CalendarReadBroker(ledger, HermeticCalendarProvider(batch))
    peer = broker.authenticate_transport(
        tenant_id=authenticated_tenant,
        principal_id=authenticated_principal,
        channel_id=authenticated_channel,
    )
    context = await broker.acquire(peer, observed_at_ns=observed_at_ns)
    return R11CalendarSession(context, broker.connect(peer))


__all__ = ["R11CalendarSession", "open_hermetic_calendar"]
