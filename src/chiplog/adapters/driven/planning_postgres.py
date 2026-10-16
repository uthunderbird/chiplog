"""Existing planning command semantics on a locked PostgreSQL transaction."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from chiplog.adapters.driven.planning_sqlite import _publication
from chiplog.capabilities.planning._planning import _PlanningPublication, _PlanningState
from chiplog.domain_primitives import TenantId
from chiplog.platform.postgres import Connection, Record, Snapshot, UserDatabase

_T = TypeVar("_T")
OPERATION = "planning.create_intention_line"


class PostgresPlanningRepository:
    def __init__(self, database: UserDatabase, tenant: TenantId, connection: Connection) -> None:
        self.database, self.tenant, self.connection = database, tenant, connection

    def _publications(self, snapshot: Snapshot) -> tuple[_PlanningPublication, ...]:
        return tuple(
            _publication(
                self.tenant,
                batch.idempotency_key,
                batch.fingerprint,
                batch.sequence,
                tuple((record.record_id, record.canonical_bytes) for record in batch.records),
            )
            for batch in snapshot.batches
            if batch.operation == OPERATION
        )

    def committed_publications(self, tenant_id: TenantId) -> tuple[_PlanningPublication, ...]:
        if tenant_id != self.tenant:
            raise PermissionError("foreign tenant")
        return self._publications(self.database._snapshot(self.connection))

    def transact(self, tenant_id: TenantId, body: Callable[[_PlanningState], _T]) -> _T:
        if tenant_id != self.tenant:
            raise PermissionError("foreign tenant")
        snapshot = self.database._snapshot(self.connection)
        previous = self._publications(snapshot)
        state = _PlanningState(tenant_id)
        state.head = state.version = snapshot.head
        state.publications.extend(previous)
        state.by_command.update((item.command_id.value, item) for item in previous)
        state.record_ids.update(
            record.record_id for batch in snapshot.batches for record in batch.records
        )
        result = body(state)
        if len(state.publications) == len(previous):
            return result
        if len(state.publications) != len(previous) + 1:
            raise ValueError("planning transaction must append exactly one batch")
        publication = state.publications[-1]
        self.database.append(
            self.connection,
            OPERATION,
            publication.command_id.value,
            publication.request_fingerprint,
            tuple(
                Record(
                    item.record_id.value,
                    "planning",
                    "chiplog.planning.record.v1",
                    item.canonical_bytes,
                )
                for item in publication.records
            ),
            snapshot.head,
        )
        return result
