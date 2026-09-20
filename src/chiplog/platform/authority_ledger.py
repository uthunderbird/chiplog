"""Durable broker epoch and issued-operation ledger for R7 raw authority."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from chiplog.platform.authority_gate import AuthorityGate, FileIdentity, checked_file_identity

TOKEN_SCHEMA: Final = "chiplog.broker.operation-token.v1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_epochs (
    tenant_id TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    endpoint_id TEXT NOT NULL,
    key_digest TEXT NOT NULL,
    current INTEGER NOT NULL CHECK (current IN (0, 1)),
    PRIMARY KEY (tenant_id, epoch)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_broker_epoch
ON broker_epochs(tenant_id) WHERE current = 1;
CREATE TABLE IF NOT EXISTS issued_operations (
    tenant_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('ONE_SHOT', 'IDEMPOTENT_EXACT')),
    command_fingerprint TEXT NOT NULL,
    nonce TEXT NOT NULL,
    broker_epoch INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    expiry_ns INTEGER NOT NULL,
    response_slot_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ISSUED', 'DEFINITE', 'OUTCOME_UNKNOWN')),
    result_digest TEXT,
    result_bytes BLOB,
    recovery_obligation_id TEXT,
    PRIMARY KEY (tenant_id, operation_id),
    UNIQUE (tenant_id, nonce),
    UNIQUE (tenant_id, response_slot_id)
);
"""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OperationToken(_StrictModel):
    schema_id: Literal["chiplog.broker.operation-token.v1"] = TOKEN_SCHEMA
    mode: Literal["ONE_SHOT", "IDEMPOTENT_EXACT"]
    tenant_id: str
    broker_epoch: int = Field(gt=0)
    owner_id: str
    generation_id: str
    session_id: str
    target_id: str
    capability_id: str
    operation_kind: str
    payload_fingerprint: str
    nonce: str
    expiry_ns: int = Field(gt=0)

    def command_fingerprint(self) -> str:
        value = self.model_dump()
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class OperationDisposition(_StrictModel):
    tenant_id: str
    operation_id: str
    response_slot_id: str
    state: Literal["ISSUED", "DEFINITE", "OUTCOME_UNKNOWN"]
    result_digest: str | None
    result_bytes: bytes | None
    recovery_obligation_id: str | None


class AuthorityLedgerConflict(RuntimeError):
    pass


class AuthorityLedgerDenied(RuntimeError):
    pass


class BrokerAuthorityLedger:
    def __init__(self, path: Path, *, authority_gate: AuthorityGate | None = None) -> None:
        self._authority_gate = authority_gate
        self._identity: FileIdentity | None = None
        if authority_gate is not None and path.is_symlink():
            raise AuthorityLedgerConflict("canonical authority ledger cannot be a symbolic link")
        self._path = path.resolve(strict=False) if authority_gate is not None else path
        with self._authority_scope():
            prior = (
                checked_file_identity(self._path)
                if authority_gate is not None and self._path.exists()
                else None
            )
            with sqlite3.connect(self._path) as connection:
                connection.executescript(_SCHEMA)
            if authority_gate is not None:
                self._identity = checked_file_identity(self._path, prior)

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._authority_gate

    @contextlib.contextmanager
    def _authority_scope(self) -> Iterator[None]:
        gate = (
            contextlib.nullcontext()
            if self._authority_gate is None
            else self._authority_gate.hold()
        )
        with gate:
            if self._identity is not None:
                checked_file_identity(self._path, self._identity)
            try:
                yield
            finally:
                if self._identity is not None:
                    checked_file_identity(self._path, self._identity)

    def allocate_epoch(self, tenant_id: str, endpoint_id: str, key_digest: str) -> int:
        with self._authority_scope(), sqlite3.connect(self._path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT COALESCE(MAX(epoch), 0) FROM broker_epochs WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            epoch = int(previous[0]) + 1
            connection.execute(
                "UPDATE broker_epochs SET current = 0 WHERE tenant_id = ? AND current = 1",
                (tenant_id,),
            )
            connection.execute(
                "INSERT INTO broker_epochs VALUES (?, ?, ?, ?, 1)",
                (tenant_id, epoch, endpoint_id, key_digest),
            )
            return epoch

    def current_epoch(self, tenant_id: str) -> int | None:
        with self._authority_scope():
            with sqlite3.connect(self._path) as connection:
                row = connection.execute(
                    "SELECT epoch FROM broker_epochs WHERE tenant_id = ? AND current = 1",
                    (tenant_id,),
                ).fetchone()
            return None if row is None else int(row[0])

    def admit(
        self,
        operation_id: str,
        response_slot_id: str,
        token: OperationToken,
        now_ns: int,
    ) -> OperationDisposition:
        with self._authority_scope():
            return self.admit_with_novelty(operation_id, response_slot_id, token, now_ns)[0]

    def admit_with_novelty(
        self,
        operation_id: str,
        response_slot_id: str,
        token: OperationToken,
        now_ns: int,
    ) -> tuple[OperationDisposition, bool]:
        with self._authority_scope():
            if token.expiry_ns <= now_ns:
                raise AuthorityLedgerDenied("operation token expired before durable admission")
            fingerprint = token.command_fingerprint()
            with sqlite3.connect(self._path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT epoch FROM broker_epochs WHERE tenant_id = ? AND current = 1",
                    (token.tenant_id,),
                ).fetchone()
                if current is None or int(current[0]) != token.broker_epoch:
                    raise AuthorityLedgerDenied("operation token broker epoch is stale")
                existing = connection.execute(
                    """SELECT command_fingerprint, mode, response_slot_id, state, result_digest,
                              result_bytes, recovery_obligation_id
                       FROM issued_operations WHERE tenant_id = ? AND operation_id = ?""",
                    (token.tenant_id, operation_id),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing[0]) != fingerprint
                        or str(existing[1]) != token.mode
                        or str(existing[2]) != response_slot_id
                    ):
                        raise AuthorityLedgerConflict("changed replay of issued operation")
                    return (
                        OperationDisposition(
                            tenant_id=token.tenant_id,
                            operation_id=operation_id,
                            response_slot_id=str(existing[2]),
                            state=str(existing[3]),  # type: ignore[arg-type]
                            result_digest=None if existing[4] is None else str(existing[4]),
                            result_bytes=None if existing[5] is None else bytes(existing[5]),
                            recovery_obligation_id=None
                            if existing[6] is None
                            else str(existing[6]),
                        ),
                        False,
                    )
                try:
                    connection.execute(
                        """INSERT INTO issued_operations VALUES
                           (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            'ISSUED', NULL, NULL, NULL)""",
                        (
                            token.tenant_id,
                            operation_id,
                            token.mode,
                            fingerprint,
                            token.nonce,
                            token.broker_epoch,
                            token.owner_id,
                            token.generation_id,
                            token.session_id,
                            token.target_id,
                            token.capability_id,
                            token.operation_kind,
                            token.payload_fingerprint,
                            token.expiry_ns,
                            response_slot_id,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise AuthorityLedgerConflict(
                        "nonce or response slot already consumed"
                    ) from error
            return (
                OperationDisposition(
                    tenant_id=token.tenant_id,
                    operation_id=operation_id,
                    response_slot_id=response_slot_id,
                    state="ISSUED",
                    result_digest=None,
                    result_bytes=None,
                    recovery_obligation_id=None,
                ),
                True,
            )

    def finish(self, disposition: OperationDisposition) -> OperationDisposition:
        with self._authority_scope():
            if disposition.state == "ISSUED":
                raise ValueError("finish requires a terminal disposition")
            if disposition.state == "DEFINITE":
                if (
                    disposition.result_bytes is None
                    or disposition.recovery_obligation_id is not None
                ):
                    raise ValueError(
                        "definite disposition requires bytes and no recovery obligation"
                    )
                digest = hashlib.sha256(disposition.result_bytes).hexdigest()
                if disposition.result_digest != digest:
                    raise ValueError("definite result digest mismatch")
            elif disposition.recovery_obligation_id is None or disposition.result_bytes is not None:
                raise ValueError("unknown disposition requires only a recovery obligation")
            with sqlite3.connect(self._path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT response_slot_id, state, result_digest, result_bytes,
                              recovery_obligation_id FROM issued_operations
                       WHERE tenant_id = ? AND operation_id = ?""",
                    (disposition.tenant_id, disposition.operation_id),
                ).fetchone()
                if row is None or str(row[0]) != disposition.response_slot_id:
                    raise AuthorityLedgerConflict("issued operation or response slot is absent")
                if str(row[1]) != "ISSUED":
                    recorded = OperationDisposition(
                        tenant_id=disposition.tenant_id,
                        operation_id=disposition.operation_id,
                        response_slot_id=str(row[0]),
                        state=str(row[1]),  # type: ignore[arg-type]
                        result_digest=None if row[2] is None else str(row[2]),
                        result_bytes=None if row[3] is None else bytes(row[3]),
                        recovery_obligation_id=None if row[4] is None else str(row[4]),
                    )
                    if recorded != disposition:
                        raise AuthorityLedgerConflict("rival terminal operation disposition")
                    return recorded
                connection.execute(
                    """UPDATE issued_operations
                       SET state = ?, result_digest = ?, result_bytes = ?,
                           recovery_obligation_id = ?
                       WHERE tenant_id = ? AND operation_id = ? AND state = 'ISSUED'""",
                    (
                        disposition.state,
                        disposition.result_digest,
                        disposition.result_bytes,
                        disposition.recovery_obligation_id,
                        disposition.tenant_id,
                        disposition.operation_id,
                    ),
                )
            return disposition

    def pending(self, tenant_id: str) -> tuple[OperationDisposition, ...]:
        with self._authority_scope():
            with sqlite3.connect(self._path) as connection:
                rows = connection.execute(
                    """SELECT operation_id, response_slot_id, state, result_digest, result_bytes,
                              recovery_obligation_id FROM issued_operations
                       WHERE tenant_id = ? AND state = 'ISSUED' ORDER BY operation_id""",
                    (tenant_id,),
                ).fetchall()
            return tuple(
                OperationDisposition(
                    tenant_id=tenant_id,
                    operation_id=str(row[0]),
                    response_slot_id=str(row[1]),
                    state="ISSUED",
                    result_digest=None,
                    result_bytes=None,
                    recovery_obligation_id=None,
                )
                for row in rows
            )

    def lookup(self, tenant_id: str, operation_id: str) -> OperationDisposition | None:
        with self._authority_scope():
            with sqlite3.connect(self._path) as connection:
                row = connection.execute(
                    """SELECT response_slot_id, state, result_digest, result_bytes,
                              recovery_obligation_id FROM issued_operations
                       WHERE tenant_id = ? AND operation_id = ?""",
                    (tenant_id, operation_id),
                ).fetchone()
            if row is None:
                return None
            return OperationDisposition(
                tenant_id=tenant_id,
                operation_id=operation_id,
                response_slot_id=str(row[0]),
                state=str(row[1]),  # type: ignore[arg-type]
                result_digest=None if row[2] is None else str(row[2]),
                result_bytes=None if row[3] is None else bytes(row[3]),
                recovery_obligation_id=None if row[4] is None else str(row[4]),
            )

    def reconcile_pending(self, tenant_id: str, broker_epoch: int) -> tuple[str, ...]:
        with self._authority_scope():
            reconciled: list[str] = []
            for disposition in self.pending(tenant_id):
                unknown = disposition.model_copy(
                    update={
                        "state": "OUTCOME_UNKNOWN",
                        "recovery_obligation_id": (
                            f"broker-restart:{broker_epoch}:{disposition.operation_id}"
                        ),
                    }
                )
                self.finish(unknown)
                reconciled.append(disposition.operation_id)
            return tuple(reconciled)


__all__ = [
    "AuthorityLedgerConflict",
    "AuthorityLedgerDenied",
    "BrokerAuthorityLedger",
    "OperationDisposition",
    "OperationToken",
]
