"""Durable canonical conversation and immutable workspace replay storage."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from chiplog.capabilities.projections.r9_boundary import (
    WorkspaceIntegrityError,
    WorkspaceRejected,
    WorkspaceState,
)


class SQLiteWorkspaceStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS workspace (
                    tenant TEXT NOT NULL, channel TEXT NOT NULL, seq INTEGER NOT NULL,
                    bytes BLOB NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY (tenant,channel,seq));
            """)

    @staticmethod
    def _verified(row: tuple[bytes, str]) -> bytes:
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise ValueError("durable digest mismatch")
        return row[0]

    def save(self, state: WorkspaceState, expected_sequence: int) -> None:
        encoded = state.model_dump_json().encode()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM workspace WHERE tenant=? AND channel=?",
                (state.tenant_id, state.channel_id),
            ).fetchone()[0]
            if latest != expected_sequence or state.sequence != latest + 1:
                raise WorkspaceRejected("workspace sequence conflict")
            connection.execute(
                "INSERT INTO workspace VALUES (?,?,?,?,?)",
                (
                    state.tenant_id,
                    state.channel_id,
                    state.sequence,
                    encoded,
                    hashlib.sha256(encoded).hexdigest(),
                ),
            )

    def load(self, tenant_id: str, channel_id: str, sequence: int) -> WorkspaceState:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT bytes,digest FROM workspace WHERE tenant=? AND channel=? AND seq=?",
                (tenant_id, channel_id, sequence),
            ).fetchone()
        try:
            if row is None:
                raise ValueError("missing workspace snapshot")
            state = WorkspaceState.model_validate_json(self._verified(row))
            if (state.tenant_id, state.channel_id, state.sequence) != (
                tenant_id,
                channel_id,
                sequence,
            ):
                raise ValueError("physical/logical workspace identity mismatch")
            return state
        except ValueError as exc:
            raise WorkspaceIntegrityError(
                "workspace.replay", tenant_id, f"{channel_id}:{sequence}"
            ) from exc
