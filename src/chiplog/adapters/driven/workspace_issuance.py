"""Independent, exact issuance records for already verified workspace states.

Only the composition path after owner-row and disclosure verification may call
select_verified. Cache writes and cache reads never create an issuance. This
adapter authenticates retained bytes, not the truth of a caller's source claims.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal, Self

from pydantic import Field

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.workspace_sqlite import SQLiteWorkspaceStore
from chiplog.capabilities.projections.r9_boundary import (
    Frozen,
    WorkspaceIntegrityError,
    WorkspaceRejected,
    WorkspaceState,
)
from chiplog.platform.authority_gate import AuthorityGate


class _Issuance(Frozen):
    schema_id: Literal["chiplog.workspace-issuance.v1"] = "chiplog.workspace-issuance.v1"
    expected_sequence: int = Field(ge=0, lt=2**64 - 1)
    state: WorkspaceState


class WorkspaceIssuanceJournal:
    @classmethod
    def open(cls, path: Path, gate: AuthorityGate, tenant: str) -> Self:
        try:
            raw = IndependentTenantDecisionJournal.for_authority_bundle(path, authority_gate=gate)
            return cls(raw, tenant)
        except (ValueError, RuntimeError, OSError) as error:
            raise WorkspaceIntegrityError("workspace.issuance_open", tenant, str(path)) from error

    def __init__(self, journal: IndependentTenantDecisionJournal, tenant: str) -> None:
        if journal.authority_gate is None:
            raise ValueError("workspace issuance requires the canonical authority gate")
        if not tenant:
            raise ValueError("workspace issuance requires a tenant")
        self._journal, self._tenant = journal, tenant
        self._gate = journal.authority_gate
        with self._gate.hold():
            self._scan("open", "journal")

    def _scan(
        self, operation: str, record: str
    ) -> tuple[str | None, dict[tuple[str, int], WorkspaceState]]:
        try:
            entries = self._journal.entries()
            states: dict[tuple[str, int], WorkspaceState] = {}
            latest: dict[str, int] = {}
            for _, _, payload in entries:
                issued = _Issuance.model_validate_json(payload)
                state = issued.state
                if issued.model_dump_json().encode() != payload:
                    raise ValueError("noncanonical workspace issuance")
                if (
                    state.tenant_id != self._tenant
                    or not state.channel_id
                    or state.sequence != issued.expected_sequence + 1
                    or latest.get(state.channel_id, 0) != issued.expected_sequence
                    or any(
                        screen.ref.tenant_id != self._tenant
                        or screen.ref.context.tenant_id != self._tenant
                        or screen.ref.context.channel_id != state.channel_id
                        for screen in state.screens
                    )
                ):
                    raise ValueError("workspace issuance identity or sequence differs")
                states[(state.channel_id, state.sequence)] = state
                latest[state.channel_id] = state.sequence
            return entries[-1][0] if entries else None, states
        except (ValueError, RuntimeError, OSError) as error:
            raise WorkspaceIntegrityError(operation, self._tenant, record) from error

    def select_verified(self, state: WorkspaceState, expected_sequence: int) -> None:
        """Select exact verified bytes before materializing the derivative cache.

        Replaying the same original selection is idempotent; a different state at
        that sequence cannot replace it. This method does not renew read contexts.
        """
        identity = f"{state.channel_id}:{state.sequence}"
        with self._gate.hold():
            head, states = self._scan("workspace.issue", identity)
            issued = _Issuance(expected_sequence=expected_sequence, state=state)
            payload = issued.model_dump_json().encode()
            # Nested model instances can come from model_copy; validate the exact
            # bytes before allowing an invalid entry to poison the durable stream.
            _Issuance.model_validate_json(payload)
            if (
                state.tenant_id != self._tenant
                or not state.channel_id
                or state.sequence != expected_sequence + 1
                or any(
                    screen.ref.tenant_id != self._tenant
                    or screen.ref.context.tenant_id != self._tenant
                    or screen.ref.context.channel_id != state.channel_id
                    for screen in state.screens
                )
            ):
                raise WorkspaceRejected("workspace issuance identity/sequence mismatch")
            existing = states.get((state.channel_id, state.sequence))
            if existing is not None:
                if existing.model_dump_json() != state.model_dump_json():
                    raise WorkspaceRejected("workspace sequence already issued different bytes")
                return
            latest = max(
                (sequence for channel, sequence in states if channel == state.channel_id),
                default=0,
            )
            if latest != expected_sequence:
                raise WorkspaceRejected("workspace issuance predecessor conflict")
            try:
                self._journal.append(payload, head)
            except (ValueError, RuntimeError, OSError) as error:
                raise WorkspaceIntegrityError("workspace.issue", self._tenant, identity) from error

    def load(self, channel: str, sequence: int) -> WorkspaceState:
        identity = f"{channel}:{sequence}"
        with self._gate.hold():
            _, states = self._scan("workspace.issued_read", identity)
            try:
                return states[(channel, sequence)]
            except KeyError as error:
                raise WorkspaceIntegrityError(
                    "workspace.issued_read", self._tenant, identity
                ) from error

    def verify(self, state: WorkspaceState) -> None:
        original = self.load(state.channel_id, state.sequence)
        if original.model_dump_json() != state.model_dump_json():
            error = ValueError("derivative bytes differ from independently issued state")
            raise WorkspaceIntegrityError(
                "workspace.issued_verify", self._tenant, f"{state.channel_id}:{state.sequence}"
            ) from error

    def recover_cache(self, cache: SQLiteWorkspaceStore, channel: str) -> WorkspaceState | None:
        """Materialize only a missing suffix of original, fully selected states.

        Existing but changed bytes fail closed. A hole within the cache is not an
        interrupted append and is rejected. Recovery never reruns owner queries.
        """
        with self._gate.hold():
            _, states = self._scan("workspace.cache_recovery", channel)
            try:
                with closing(sqlite3.connect(cache.path)) as connection:
                    sequences = tuple(
                        row[0]
                        for row in connection.execute(
                            "SELECT seq FROM workspace WHERE tenant=? AND channel=? ORDER BY seq",
                            (self._tenant, channel),
                        )
                    )
                if sequences != tuple(range(1, len(sequences) + 1)):
                    raise ValueError("workspace cache has a non-append gap")
                for sequence in sequences:
                    cached = cache.load(self._tenant, channel, sequence)
                    original = states.get((channel, sequence))
                    if original is None or cached.model_dump_json() != original.model_dump_json():
                        raise ValueError("workspace cache differs from its original issuance")
                selected = tuple(
                    state
                    for (owner_channel, _), state in states.items()
                    if owner_channel == channel
                )
                for state in selected[len(sequences) :]:
                    cache.save(state, state.sequence - 1)
                return selected[-1] if selected else None
            except (ValueError, sqlite3.Error) as error:
                raise WorkspaceIntegrityError(
                    "workspace.cache_recovery", self._tenant, channel
                ) from error
