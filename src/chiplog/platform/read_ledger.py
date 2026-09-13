"""Durable total order for R7 read invalidators and response-slot release."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_SCHEMA = """
CREATE TABLE IF NOT EXISTS authority_read_state (
    tenant_id TEXT PRIMARY KEY,
    canonical_state BLOB NOT NULL,
    state_fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS authority_read_releases (
    tenant_id TEXT NOT NULL,
    read_attempt_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    initial_state_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'RELEASED', 'STALE_OR_INDETERMINATE_READ')),
    result_digest TEXT,
    result_bytes BLOB,
    recipient_fingerprint TEXT NOT NULL,
    proof_fingerprint TEXT,
    enqueued INTEGER NOT NULL CHECK (enqueued IN (0, 1)),
    dequeued INTEGER NOT NULL CHECK (dequeued IN (0, 1)),
    PRIMARY KEY (tenant_id, read_attempt_id)
);
"""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BrokerReadState(_StrictModel):
    tenant_id: str
    broker_epoch: int = Field(gt=0)
    credential_session_head: str
    endpoint_channel_head: str
    file_wal_observation: str
    journal_head: str
    materialization_commitment: str
    owner_generation: str
    owner_draining: bool
    principal_contour_head: str
    storage_mutation_generation: int = Field(ge=0)
    trust_transition_head: str
    authority_surface_digest: str
    amr_fingerprint: str

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class ReadOperation(_StrictModel):
    variant: Literal["PLANNING_PUBLICATIONS", "PLANNING_PUBLICATIONS_R13"]
    request_id: str
    read_attempt_id: str
    tenant_id: str
    broker_epoch: int = Field(gt=0)
    owner_id: str
    generation_id: str
    session_id: str
    capability_id: Literal["planning.read"]
    target_id: Literal["planning-store"]
    after_commit_sequence: int = Field(default=0, ge=0)
    after_operation_kind: str = ""
    after_idempotency_key: str = ""
    max_rows: int = Field(gt=0, le=1000)
    response_slot_id: str

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def recipient_fingerprint(self) -> str:
        payload = json.dumps(
            {
                "broker_epoch": self.broker_epoch,
                "generation_id": self.generation_id,
                "owner_id": self.owner_id,
                "response_slot_id": self.response_slot_id,
                "session_id": self.session_id,
                "tenant_id": self.tenant_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()


class ReadRelease(_StrictModel):
    tenant_id: str
    read_attempt_id: str
    state: Literal["PENDING", "RELEASED", "STALE_OR_INDETERMINATE_READ"]
    result_digest: str | None
    recipient_fingerprint: str
    proof_fingerprint: str | None
    enqueued: bool
    dequeued: bool


class ReadLedgerConflict(RuntimeError):
    pass


class BrokerReadLedger:
    def __init__(self, path: Path) -> None:
        self._path = path
        with sqlite3.connect(path) as connection:
            connection.executescript(_SCHEMA)

    def publish_initial_state(self, state: BrokerReadState) -> None:
        with sqlite3.connect(self._path) as connection:
            try:
                connection.execute(
                    "INSERT INTO authority_read_state VALUES (?, ?, ?)",
                    (state.tenant_id, state.canonical_bytes(), state.fingerprint()),
                )
            except sqlite3.IntegrityError as error:
                raise ReadLedgerConflict("authority read state already exists") from error

    def reconcile_generation(self, state: BrokerReadState) -> None:
        """Fence every old read attempt before publishing a restarted generation."""
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE authority_read_releases
                   SET state = 'STALE_OR_INDETERMINATE_READ', result_digest = NULL,
                       result_bytes = NULL, enqueued = 0
                   WHERE tenant_id = ? AND state = 'PENDING'""",
                (state.tenant_id,),
            )
            connection.execute(
                """INSERT INTO authority_read_state VALUES (?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                     canonical_state = excluded.canonical_state,
                     state_fingerprint = excluded.state_fingerprint""",
                (state.tenant_id, state.canonical_bytes(), state.fingerprint()),
            )

    def current_state(self, tenant_id: str) -> BrokerReadState:
        with sqlite3.connect(self._path) as connection:
            row = connection.execute(
                "SELECT canonical_state, state_fingerprint FROM authority_read_state "
                "WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        if row is None:
            raise ReadLedgerConflict("authority read state is absent")
        state = BrokerReadState.model_validate_json(bytes(row[0]))
        if state.fingerprint() != str(row[1]):
            raise ReadLedgerConflict("authority read state fingerprint mismatch")
        return state

    def invalidate_storage_mutation(
        self, tenant_id: str, expected_fingerprint: str, commitment: str, observation: str
    ) -> BrokerReadState:
        state = self.current_state(tenant_id)
        return self._replace_state(
            state,
            expected_fingerprint,
            {
                "file_wal_observation": observation,
                "materialization_commitment": commitment,
                "storage_mutation_generation": state.storage_mutation_generation + 1,
            },
        )

    def invalidate_owner_generation(
        self, tenant_id: str, expected_fingerprint: str, generation: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id),
            expected_fingerprint,
            {"owner_draining": False, "owner_generation": generation},
        )

    def start_owner_drain(self, tenant_id: str, expected_fingerprint: str) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"owner_draining": True}
        )

    def invalidate_broker_epoch(
        self, tenant_id: str, expected_fingerprint: str, epoch: int
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"broker_epoch": epoch}
        )

    def invalidate_credential_session(
        self, tenant_id: str, expected_fingerprint: str, head: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"credential_session_head": head}
        )

    def invalidate_endpoint_channel(
        self, tenant_id: str, expected_fingerprint: str, head: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"endpoint_channel_head": head}
        )

    def invalidate_file_wal(
        self, tenant_id: str, expected_fingerprint: str, observation: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id),
            expected_fingerprint,
            {"file_wal_observation": observation},
        )

    def invalidate_journal_decision(
        self, tenant_id: str, expected_fingerprint: str, head: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"journal_head": head}
        )

    def invalidate_materialization(
        self, tenant_id: str, expected_fingerprint: str, commitment: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id),
            expected_fingerprint,
            {"materialization_commitment": commitment},
        )

    def invalidate_principal_contour(
        self, tenant_id: str, expected_fingerprint: str, head: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"principal_contour_head": head}
        )

    def invalidate_trust_transition(
        self, tenant_id: str, expected_fingerprint: str, head: str
    ) -> BrokerReadState:
        return self._replace_state(
            self.current_state(tenant_id), expected_fingerprint, {"trust_transition_head": head}
        )

    def _replace_state(
        self, state: BrokerReadState, expected_fingerprint: str, changes: dict[str, object]
    ) -> BrokerReadState:
        if state.fingerprint() != expected_fingerprint:
            raise ReadLedgerConflict("stale authority read state predecessor")
        successor = state.model_copy(update=changes)
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                """UPDATE authority_read_state SET canonical_state = ?, state_fingerprint = ?
                   WHERE tenant_id = ? AND state_fingerprint = ?""",
                (
                    successor.canonical_bytes(),
                    successor.fingerprint(),
                    state.tenant_id,
                    expected_fingerprint,
                ),
            ).rowcount
            if changed != 1:
                raise ReadLedgerConflict("rival authority read invalidator won")
        return successor

    def begin(self, operation: ReadOperation, state: BrokerReadState) -> ReadRelease:
        if operation.tenant_id != state.tenant_id:
            raise ReadLedgerConflict("read tenant differs from authority state")
        with sqlite3.connect(self._path) as connection:
            try:
                connection.execute(
                    """INSERT INTO authority_read_releases VALUES
                       (?, ?, ?, ?, 'PENDING', NULL, NULL, ?, NULL, 0, 0)""",
                    (
                        operation.tenant_id,
                        operation.read_attempt_id,
                        operation.fingerprint(),
                        state.fingerprint(),
                        operation.recipient_fingerprint(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ReadLedgerConflict("read attempt identity already exists") from error
        return ReadRelease(
            tenant_id=operation.tenant_id,
            read_attempt_id=operation.read_attempt_id,
            state="PENDING",
            result_digest=None,
            recipient_fingerprint=operation.recipient_fingerprint(),
            proof_fingerprint=None,
            enqueued=False,
            dequeued=False,
        )

    def release(
        self,
        operation: ReadOperation,
        expected_state: BrokerReadState,
        proof_fingerprint: str,
        result_bytes: bytes,
    ) -> ReadRelease:
        result_digest = hashlib.sha256(result_bytes).hexdigest()
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state_fingerprint FROM authority_read_state WHERE tenant_id = ?",
                (operation.tenant_id,),
            ).fetchone()
            release = connection.execute(
                """SELECT request_fingerprint, initial_state_fingerprint, state,
                          result_digest, recipient_fingerprint, proof_fingerprint,
                          enqueued, dequeued
                   FROM authority_read_releases
                   WHERE tenant_id = ? AND read_attempt_id = ?""",
                (operation.tenant_id, operation.read_attempt_id),
            ).fetchone()
            if release is None:
                raise ReadLedgerConflict("read attempt was not prepared")
            if str(release[0]) != operation.fingerprint():
                raise ReadLedgerConflict("changed read attempt replay")
            if str(release[2]) != "PENDING":
                return self._release_from_row(operation, release)
            current_matches = (
                current is not None
                and str(current[0]) == expected_state.fingerprint()
                and str(release[1]) == expected_state.fingerprint()
                and not expected_state.owner_draining
                and operation.broker_epoch == expected_state.broker_epoch
                and operation.generation_id == expected_state.owner_generation
            )
            if current_matches:
                state = "RELEASED"
                connection.execute(
                    """UPDATE authority_read_releases SET state = 'RELEASED',
                       result_digest = ?, result_bytes = ?, proof_fingerprint = ?, enqueued = 1
                       WHERE tenant_id = ? AND read_attempt_id = ? AND state = 'PENDING'""",
                    (
                        result_digest,
                        result_bytes,
                        proof_fingerprint,
                        operation.tenant_id,
                        operation.read_attempt_id,
                    ),
                )
            else:
                state = "STALE_OR_INDETERMINATE_READ"
                connection.execute(
                    """UPDATE authority_read_releases
                       SET state = 'STALE_OR_INDETERMINATE_READ', proof_fingerprint = ?
                       WHERE tenant_id = ? AND read_attempt_id = ? AND state = 'PENDING'""",
                    (proof_fingerprint, operation.tenant_id, operation.read_attempt_id),
                )
        return ReadRelease(
            tenant_id=operation.tenant_id,
            read_attempt_id=operation.read_attempt_id,
            state=state,  # type: ignore[arg-type]
            result_digest=result_digest if state == "RELEASED" else None,
            recipient_fingerprint=operation.recipient_fingerprint(),
            proof_fingerprint=proof_fingerprint,
            enqueued=state == "RELEASED",
            dequeued=False,
        )

    def dequeue(self, operation: ReadOperation) -> bytes | None:
        with sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT result_bytes, recipient_fingerprint, state, enqueued, dequeued
                   FROM authority_read_releases
                   WHERE tenant_id = ? AND read_attempt_id = ?""",
                (operation.tenant_id, operation.read_attempt_id),
            ).fetchone()
            if (
                row is None
                or str(row[1]) != operation.recipient_fingerprint()
                or str(row[2]) != "RELEASED"
                or int(row[3]) != 1
                or int(row[4]) != 0
            ):
                return None
            connection.execute(
                """UPDATE authority_read_releases SET dequeued = 1
                   WHERE tenant_id = ? AND read_attempt_id = ? AND dequeued = 0""",
                (operation.tenant_id, operation.read_attempt_id),
            )
            return bytes(row[0])

    @staticmethod
    def _release_from_row(operation: ReadOperation, row: tuple[object, ...]) -> ReadRelease:
        return ReadRelease(
            tenant_id=operation.tenant_id,
            read_attempt_id=operation.read_attempt_id,
            state=str(row[2]),  # type: ignore[arg-type]
            result_digest=None if row[3] is None else str(row[3]),
            recipient_fingerprint=str(row[4]),
            proof_fingerprint=None if row[5] is None else str(row[5]),
            enqueued=bool(row[6]),
            dequeued=bool(row[7]),
        )


__all__ = [
    "BrokerReadLedger",
    "BrokerReadState",
    "ReadLedgerConflict",
    "ReadOperation",
    "ReadRelease",
]
