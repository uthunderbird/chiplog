"""Broker-only calendar snapshot acquisition and recipient-bound durable release."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass

from chiplog.capabilities.calendar_observations import (
    CalendarBatch,
    CalendarProviderPort,
    prepare_rows,
    source_heads_digest,
)
from chiplog.capabilities.calendar_observations.boundary import (
    WorkspaceQueryPort,
    WorkspaceReadContext,
    WorkspaceReadRequest,
    WorkspaceReadResult,
    WorkspaceRow,
)
from chiplog.platform.calendar_read_ledger import (
    CALENDAR_QUERY_MANIFEST,
    CALENDAR_REGISTRY_DIGEST,
    CalendarReadLedger,
    CalendarReadOperation,
    CalendarReadState,
)
from chiplog.platform.read_ledger import ReadLedgerConflict


class CalendarReadFailure(RuntimeError):
    """Indeterminate acquisition; no session has been issued."""


class CalendarResponseIntegrityError(RuntimeError):
    """Durable response bytes differ from the broker's exact prepared result."""


class AuthenticatedCalendarPeer:
    """Opaque transport-authentication handle; DTO identity cannot construct one."""


@dataclass(frozen=True)
class _Snapshot:
    context: WorkspaceReadContext
    state: CalendarReadState
    peer: AuthenticatedCalendarPeer
    rows: tuple[WorkspaceRow, ...]
    freshness: str


class _BoundCalendarPort:
    def __init__(
        self,
        broker: CalendarReadBroker,
        peer: AuthenticatedCalendarPeer,
    ) -> None:
        self._broker = broker
        self._peer = peer

    async def read(self, request: WorkspaceReadRequest) -> WorkspaceReadResult:
        return self._broker._read(self._peer, request)


class CalendarReadBroker:
    def __init__(
        self,
        ledger: CalendarReadLedger,
        provider: CalendarProviderPort,
    ) -> None:
        self._ledger = ledger
        self._identity = self._physical_identity()
        self._provider = provider
        self._peers: dict[AuthenticatedCalendarPeer, tuple[str, str, str]] = {}
        self._snapshots: dict[str, _Snapshot] = {}
        self._cursors: dict[str, tuple[str, bytes, str]] = {}
        self._slots: set[tuple[str, str, str]] = set()

    def _physical_identity(self) -> tuple[str, int, int]:
        return self._ledger.physical_identity()

    def authenticate_transport(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        channel_id: str,
    ) -> AuthenticatedCalendarPeer:
        """Trusted composition entry; arguments come from transport, never request DTOs."""
        peer = AuthenticatedCalendarPeer()
        self._peers[peer] = tenant_id, principal_id, channel_id
        return peer

    def connect(self, peer: AuthenticatedCalendarPeer) -> WorkspaceQueryPort:
        return _BoundCalendarPort(self, peer)

    async def acquire(
        self,
        peer: AuthenticatedCalendarPeer,
        *,
        observed_at_ns: int,
    ) -> WorkspaceReadContext:
        identity = self._peers.get(peer)
        if identity is None:
            raise CalendarReadFailure("transport peer is not authenticated")
        tenant, principal, channel = identity
        try:
            state = self._ledger.current_state(tenant)
            if (
                self._physical_identity() != self._identity
                or state.owner_draining
                or state.principal_id != principal
                or state.channel_id != channel
            ):
                raise ValueError("physical identity or authenticated recipient changed")
            observed = await self._provider.observe()
            batch = CalendarBatch.model_validate_json(observed.model_dump_json())
            if (
                batch.revision != state.provider_revision
                or source_heads_digest(batch) != state.source_heads_digest
            ):
                raise ValueError("provider revision or current source heads differ")
            rows = prepare_rows(
                batch,
                tenant,
                state.policy_head,
                state.principal_contour_head,
                state.deletion_fence_head,
                channel,
            )
            if any(o.observed_at_ns > observed_at_ns for o in batch.observations):
                raise ValueError("provider observation is from the future")
            if self._ledger.current_state(tenant) != state:
                raise ValueError("invalidator changed during acquisition")
            snapshot_id = secrets.token_hex(24)
            context = WorkspaceReadContext(
                tenant_id=tenant,
                database_instance_id=state.database_instance_id,
                principal_id=principal,
                principal_contour_head=state.principal_contour_head,
                channel_id=channel,
                endpoint_binding_head=state.endpoint_channel_head,
                broker_epoch=state.broker_epoch,
                owner_id="calendar_observations",
                generation_id=state.owner_generation,
                session_id=secrets.token_hex(24),
                snapshot_id=snapshot_id,
                snapshot_frontier=state.storage_mutation_generation,
                verified_snapshot_bytes=secrets.token_bytes(32),
                invalidator_registry_digest=CALENDAR_REGISTRY_DIGEST,
                policy_head=state.policy_head,
                deletion_fence_head=state.deletion_fence_head,
                observed_at_ns=observed_at_ns,
            )
            freshnesses = {o.freshness for o in batch.observations}
            freshness = (
                "UNKNOWN"
                if "UNKNOWN" in freshnesses
                else "LAGGING"
                if "LAGGING" in freshnesses
                else "CURRENT"
            )
            self._snapshots[snapshot_id] = _Snapshot(context, state, peer, rows, freshness)
            return context
        except (ValueError, OSError, ReadLedgerConflict) as error:
            raise CalendarReadFailure(f"calendar acquire tenant={tenant}: {error}") from error

    @staticmethod
    def _query_binding(request: WorkspaceReadRequest) -> bytes:
        return json.dumps(
            [
                request.query,
                request.detail_id,
                request.range_start_ns,
                request.range_end_ns,
            ]
        ).encode()

    @staticmethod
    def _result(
        request: WorkspaceReadRequest,
        disposition: str,
        *,
        rows: tuple[WorkspaceRow, ...] = (),
        next_cursor: str | None = None,
        reason: str | None = None,
    ) -> WorkspaceReadResult:
        return WorkspaceReadResult.model_validate(
            {
                "disposition": disposition,
                "context": request.context,
                "request_id": request.request_id,
                "read_attempt_id": request.read_attempt_id,
                "rows": rows,
                "next_cursor": next_cursor,
                "reason": reason,
            }
        )

    def _read(
        self,
        peer: AuthenticatedCalendarPeer,
        request: WorkspaceReadRequest,
    ) -> WorkspaceReadResult:
        # Revalidate even callers using Pydantic model_copy/model_construct locally.
        try:
            request = WorkspaceReadRequest.model_validate_json(request.model_dump_json())
        except ValueError:
            return self._result(request, "DENIED", reason="invalid wire request")
        snapshot = self._snapshots.get(request.context.snapshot_id)
        if (
            snapshot is None
            or snapshot.peer is not peer
            or request.context != snapshot.context
            or self._peers.get(peer)
            != (
                request.context.tenant_id,
                request.context.principal_id,
                request.context.channel_id,
            )
        ):
            return self._result(request, "DENIED", reason="unissued or foreign snapshot/context")
        context = snapshot.context
        if (
            request.query not in {"CALENDAR_AGENDA", "CALENDAR_DETAIL"}
            or (request.query, context.owner_id, "calendar.read", "calendar-observations")
            not in CALENDAR_QUERY_MANIFEST
        ):
            return self._result(request, "DENIED", reason="query owner manifest mismatch")
        try:
            if (
                self._physical_identity() != self._identity
                or self._ledger.current_state(context.tenant_id) != snapshot.state
                or snapshot.freshness == "UNKNOWN"
            ):
                return self._result(request, "STALE_OR_INDETERMINATE_READ", reason="stale heads")
            rows = self._select(snapshot, request)
            slot = context.tenant_id, context.session_id, request.response_slot_id
            if slot in self._slots:
                raise ValueError("response slot was already consumed")
            self._slots.add(slot)
            operation = CalendarReadOperation(
                variant=request.query,
                owner_id="calendar_observations",
                request_id=request.request_id,
                read_attempt_id=request.read_attempt_id,
                tenant_id=context.tenant_id,
                broker_epoch=context.broker_epoch,
                generation_id=context.generation_id,
                session_id=context.session_id,
                response_slot_id=request.response_slot_id,
                request_bytes=request.model_dump_json().encode(),
            )
            releases = self._ledger.release_port()
            page = rows[: request.max_rows]
            # Each row's full envelope is inseparable; bound includes source references.
            if sum(len(row.envelope.sources) for row in page) > request.max_rows:
                raise ValueError("complete source closure cannot fit requested bound")
            releases.begin(operation, snapshot.state)
            cursor = None
            if len(rows) > request.max_rows:
                cursor = secrets.token_hex(24)
                self._cursors[cursor] = (
                    context.snapshot_id,
                    self._query_binding(request),
                    page[-1].order_key,
                )
            result = self._result(
                request,
                snapshot.freshness if page or request.query == "CALENDAR_AGENDA" else "NOT_FOUND",
                rows=page,
                next_cursor=cursor,
            )
            if self._physical_identity() != self._identity:
                raise ValueError("physical identity changed before release")
            proof = hashlib.sha256(
                context.verified_snapshot_bytes
                + snapshot.state.canonical_bytes()
                + operation.fingerprint().encode()
            ).hexdigest()
            prepared_bytes = result.model_dump_json().encode()
            release = releases.release(
                operation,
                snapshot.state,
                proof,
                prepared_bytes,
            )
            if self._physical_identity() != self._identity:
                raise ValueError("physical ledger changed during release")
            if release.state != "RELEASED":
                return self._result(request, "STALE_OR_INDETERMINATE_READ", reason="release fenced")
            emitted = releases.dequeue(operation)
            if self._physical_identity() != self._identity:
                raise ValueError("physical ledger changed during response dequeue")
            if emitted is None:
                raise ValueError("recipient release slot unavailable")
            if emitted != prepared_bytes:
                raise CalendarResponseIntegrityError(
                    f"operation=calendar.dequeue tenant={context.tenant_id} "
                    f"record=authority_read_releases/{request.read_attempt_id}: integrity failure"
                ) from ValueError("durable response differs from exact prepared bytes")
            return WorkspaceReadResult.model_validate_json(emitted)
        except (ValueError, OSError, ReadLedgerConflict) as error:
            return self._result(request, "STALE_OR_INDETERMINATE_READ", reason=str(error))

    def _select(
        self,
        snapshot: _Snapshot,
        request: WorkspaceReadRequest,
    ) -> tuple[WorkspaceRow, ...]:
        if request.query == "CALENDAR_DETAIL":
            if (
                not request.detail_id
                or request.after_cursor is not None
                or request.range_start_ns is not None
                or request.range_end_ns is not None
            ):
                raise ValueError("detail requires only exact detail identity")
            return tuple(row for row in snapshot.rows if row.row_id == request.detail_id)
        if (
            request.detail_id is not None
            or request.range_start_ns is None
            or request.range_end_ns is None
            or request.range_start_ns < 0
            or request.range_start_ns >= request.range_end_ns
        ):
            raise ValueError("agenda requires an ordered nonempty range")
        after = ""
        if request.after_cursor is not None:
            cursor = self._cursors.get(request.after_cursor)
            if cursor is None or cursor[:2] != (
                snapshot.context.snapshot_id,
                self._query_binding(request),
            ):
                raise ValueError("unknown, foreign or changed-query cursor")
            after = cursor[2]
        result = []
        for row in snapshot.rows:
            event = json.loads(row.canonical_payload)
            if (
                row.order_key > after
                and event["start_ns"] < request.range_end_ns
                and event["end_ns"] > request.range_start_ns
            ):
                result.append(row)
        return tuple(result)


__all__ = [
    "AuthenticatedCalendarPeer",
    "CalendarReadBroker",
    "CalendarReadFailure",
    "CalendarResponseIntegrityError",
]
