"""Calendar specialization of the unchanged R7 durable release algorithm.

R7 persists operation fingerprints and recipients, not their Python nominal type.
The structural view below explicitly supplies that algorithm's required contract.
No calendar request is converted to a planning query or planning capability.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from chiplog.platform.read_ledger import (
    BrokerReadLedger,
    BrokerReadState,
    ReadLedgerConflict,
    ReadRelease,
)

CALENDAR_QUERY_MANIFEST = (
    ("CALENDAR_AGENDA", "calendar_observations", "calendar.read", "calendar-observations"),
    ("CALENDAR_DETAIL", "calendar_observations", "calendar.read", "calendar-observations"),
)
CALENDAR_INVALIDATORS = (
    *tuple(BrokerReadState.model_fields),
    "database_instance_id",
    "principal_id",
    "channel_id",
    "policy_head",
    "deletion_fence_head",
    "provider_revision",
    "source_heads_digest",
)
CALENDAR_REGISTRY_DIGEST = hashlib.sha256(
    json.dumps((CALENDAR_QUERY_MANIFEST, CALENDAR_INVALIDATORS)).encode()
).hexdigest()


class CalendarReadState(BrokerReadState):
    database_instance_id: str
    principal_id: str
    channel_id: str
    policy_head: str
    deletion_fence_head: str
    provider_revision: str
    source_heads_digest: str


class CalendarReadOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    variant: Literal["CALENDAR_AGENDA", "CALENDAR_DETAIL"]
    owner_id: Literal["calendar_observations"]
    capability_id: Literal["calendar.read"] = "calendar.read"
    target_id: Literal["calendar-observations"] = "calendar-observations"
    request_id: str
    read_attempt_id: str
    tenant_id: str
    broker_epoch: int
    generation_id: str
    session_id: str
    response_slot_id: str
    request_bytes: bytes

    def fingerprint(self) -> str:
        # JSON request binds every query/context/slot/limit/cursor field, not just recipient.
        fields = self.model_dump(exclude={"request_bytes"})
        fields["request_hex"] = self.request_bytes.hex()
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    def recipient_fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    self.tenant_id,
                    self.broker_epoch,
                    self.owner_id,
                    self.generation_id,
                    self.session_id,
                    self.response_slot_id,
                ]
            ).encode()
        ).hexdigest()


class CalendarReleasePort(Protocol):
    def begin(self, operation: CalendarReadOperation, state: BrokerReadState) -> ReadRelease: ...
    def release(
        self,
        operation: CalendarReadOperation,
        expected_state: BrokerReadState,
        proof_fingerprint: str,
        result_bytes: bytes,
    ) -> ReadRelease: ...
    def dequeue(self, operation: CalendarReadOperation) -> bytes | None: ...


class CalendarReadIntegrityError(RuntimeError):
    """Authoritative durable read failure, distinct from normal invalidation."""

    def __init__(self, tenant_id: str) -> None:
        super().__init__(
            f"operation=calendar.current_state tenant={tenant_id} "
            f"record=authority_read_state/{tenant_id}: integrity failure"
        )


class CalendarReadLedger(BrokerReadLedger):
    """Dedicated component ledger; R7 authority DB/schema is untouched."""

    def release_port(self) -> CalendarReleasePort:
        return cast(CalendarReleasePort, self)

    def physical_identity(self) -> tuple[str, int, int]:
        stat = self._path.stat()
        return str(self._path.resolve()), stat.st_dev, stat.st_ino

    def current_state(self, tenant_id: str) -> CalendarReadState:
        try:
            with closing(sqlite3.connect(self._path)) as connection:
                row = connection.execute(
                    "SELECT canonical_state, state_fingerprint FROM authority_read_state "
                    "WHERE tenant_id = ?",
                    (tenant_id,),
                ).fetchone()
            if row is None:
                raise ReadLedgerConflict("calendar read state is absent")
            state = CalendarReadState.model_validate_json(bytes(row[0]))
            if state.fingerprint() != str(row[1]) or state.tenant_id != tenant_id:
                raise ValueError("calendar state fingerprint or tenant mismatch")
            return state
        except (ValueError, sqlite3.Error) as error:
            raise CalendarReadIntegrityError(tenant_id) from error

    def invalidate(self, expected: CalendarReadState, **heads: object) -> CalendarReadState:
        if not heads or not set(heads) <= set(CALENDAR_INVALIDATORS) - {"tenant_id"}:
            raise ReadLedgerConflict("unknown calendar invalidator")
        checked = CalendarReadState.model_validate({**expected.model_dump(), **heads})
        self._replace_state(expected, expected.fingerprint(), checked.model_dump())
        return self.current_state(expected.tenant_id)


__all__ = [
    "CALENDAR_INVALIDATORS",
    "CALENDAR_QUERY_MANIFEST",
    "CALENDAR_REGISTRY_DIGEST",
    "CalendarReadIntegrityError",
    "CalendarReadLedger",
    "CalendarReadOperation",
    "CalendarReadState",
]
