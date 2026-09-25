"""Strict broker adapter for an independently authenticated decision journal."""

import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform._owner_publication_contracts import (
    BrokerDTO,
    Digest,
    Identity,
    OwnerCommandBytes,
    OwnerRecordBytes,
    RegisteredPublication,
    UInt64,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.owner_publications import (
    OwnerPublicationPending,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
    source_commands,
)


class OwnerJournalIntegrityError(RuntimeError):
    def __init__(self, operation: str, tenant: str, record: str) -> None:
        super().__init__(
            f"owner journal integrity: operation={operation} tenant={tenant} record={record}"
        )


class _Selected(BrokerDTO):
    kind: Literal["SELECTED"] = "SELECTED"
    schema_id: Literal["chiplog.owner-decision.v1"] = "chiplog.owner-decision.v1"
    request: RegisteredPublication
    issuance_id: Identity
    fence_generation: Identity
    fence_frontier: UInt64
    predecessor_commitment: Digest
    resulting_commitment: Digest


class _Materialized(BrokerDTO):
    kind: Literal["MATERIALIZED"] = "MATERIALIZED"
    schema_id: Literal["chiplog.owner-decision.v1"] = "chiplog.owner-decision.v1"
    tenant_id: Identity
    command_id: Identity
    decision_id: Digest
    decision_fingerprint: Digest
    resulting_commitment: Digest


_Entry = Annotated[_Selected | _Materialized, Field(discriminator="kind")]
_CODEC: TypeAdapter[_Entry] = TypeAdapter(_Entry)


def _canonical(entry: _Entry) -> bytes:
    return json.dumps(entry.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def _validate_bytes(request: RegisteredPublication) -> None:
    items: tuple[OwnerRecordBytes | OwnerCommandBytes, ...] = (
        *request.complete_records,
        *source_commands(request),
    )
    for item in items:
        if hashlib.sha256(item.canonical_bytes).hexdigest() != item.fingerprint:
            raise ValueError("selected owner bytes do not match their fingerprint")
    if len({row.record_id for row in request.complete_records}) != len(request.complete_records):
        raise ValueError("duplicate selected record identity")


def _validate_successor(
    request: RegisteredPublication,
    selected: dict[str, SelectedOwnerDecision],
    materialized: set[str] | frozenset[str],
) -> None:
    if selected.keys() - materialized:
        raise OwnerPublicationPending("selected publication remains unmaterialized")
    if selected:
        previous = next(reversed(selected.values()))
        frontier = request.expected.tenant_frontier
        if frontier < previous.tenant_commit_sequence or (
            frontier == previous.tenant_commit_sequence
            and request.expected.expected_materialization_commitment
            != previous.resulting_commitment
        ):
            raise ValueError("selected predecessor forks an earlier decision")


@dataclass(frozen=True)
class _Scan:
    head: str | None
    selected: dict[str, SelectedOwnerDecision]
    materialized: frozenset[str]


@dataclass(frozen=True)
class OwnerJournalSnapshot:
    """Complete authenticated journal cut, including pending selected decisions.

    This is not an atomic cut with SQLite. A consumer must bind the independent
    head and physical materialization under its registered admission protocol.
    """

    tenant_id: str
    head: str | None
    decisions: tuple[SelectedOwnerDecision, ...]
    materialized_command_ids: frozenset[str]


class IndependentOwnerDecisionJournal:
    """Dedicated tenant journal; caller supplies the independently rooted raw journal.

    This adapter stores already-authorized decisions. It issues no authority and
    cannot turn an untrusted request into a prepared broker publication.
    """

    def __init__(self, raw: IndependentTenantDecisionJournal, tenant_id: str) -> None:
        self._raw = raw
        self._tenant = tenant_id
        self._scan("open", "journal")

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._raw.authority_gate

    def _authority_scope(self) -> AbstractContextManager[None]:
        gate = self.authority_gate
        return nullcontext() if gate is None else gate.hold()

    def _scan(self, operation: str, record: str) -> _Scan:
        try:
            entries = self._raw.entries()
            return self._scan_entries(entries, operation, record)
        except OwnerJournalIntegrityError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            raise OwnerJournalIntegrityError(operation, self._tenant, record) from error

    def _scan_entries(
        self,
        entries: tuple[tuple[str, str | None, bytes], ...],
        operation: str,
        record: str,
    ) -> _Scan:
        try:
            selected: dict[str, SelectedOwnerDecision] = {}
            materialized: set[str] = set()
            for record, _, payload in entries:
                entry = _CODEC.validate_json(payload)
                if _canonical(entry) != payload:
                    raise ValueError("noncanonical journal entry")
                if isinstance(entry, _Selected):
                    request = entry.request
                    _validate_bytes(request)
                    _validate_successor(request, selected, materialized)
                    command = request.identity.command_id
                    if request.identity.tenant_id != self._tenant or command in selected:
                        raise ValueError("foreign tenant or duplicate selected command")
                    if (
                        request.expected.tenant_id != self._tenant
                        or entry.predecessor_commitment
                        != request.expected.expected_materialization_commitment
                        or request.expected.tenant_frontier == 2**64 - 1
                    ):
                        raise ValueError("inconsistent selected predecessor or exhausted frontier")
                    selected[command] = SelectedOwnerDecision(
                        PreparedOwnerPublication(
                            request,
                            entry.issuance_id,
                            entry.fence_generation,
                            entry.fence_frontier,
                            entry.predecessor_commitment,
                        ),
                        record,
                        record,
                        hashlib.sha256(payload).hexdigest(),
                        entry.resulting_commitment,
                        request.expected.tenant_frontier + 1,
                    )
                else:
                    decision = selected.get(entry.command_id)
                    if (
                        entry.tenant_id != self._tenant
                        or decision is None
                        or entry.command_id in materialized
                        or entry.decision_id != decision.decision_id
                        or entry.decision_fingerprint != decision.decision_fingerprint
                        or entry.resulting_commitment != decision.resulting_commitment
                    ):
                        raise ValueError("orphan, rival or duplicate materialization marker")
                    materialized.add(entry.command_id)
            return _Scan(entries[-1][0] if entries else None, selected, frozenset(materialized))
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            raise OwnerJournalIntegrityError(operation, self._tenant, record) from error

    def lookup(self, tenant_id: str, command_id: str) -> SelectedOwnerDecision | None:
        if tenant_id != self._tenant:
            raise OwnerJournalIntegrityError("lookup", tenant_id, command_id) from ValueError(
                "journal tenant differs"
            )
        return self._scan("lookup", command_id).selected.get(command_id)

    def snapshot(self) -> OwnerJournalSnapshot:
        scan = self._scan("snapshot", "journal")
        return OwnerJournalSnapshot(
            self._tenant, scan.head, tuple(scan.selected.values()), scan.materialized
        )

    def snapshot_at(self, head: str | None) -> OwnerJournalSnapshot:
        """Return the exact, strictly decoded journal prefix ending at ``head``.

        The complete journal is decoded before selecting a prefix, so a damaged
        current tail cannot be hidden behind an old otherwise-valid head. ``None``
        explicitly selects the empty prefix.
        """
        record = head if head is not None else "empty"
        try:
            entries = self._raw.entries()
            self._scan_entries(entries, "snapshot_at", record)
            prefix: tuple[tuple[str, str | None, bytes], ...]
            if head is None:
                prefix = ()
            else:
                matches = [index for index, entry in enumerate(entries) if entry[0] == head]
                if len(matches) != 1:
                    raise ValueError("requested owner journal head is absent or ambiguous")
                prefix = entries[: matches[0] + 1]
            scan = self._scan_entries(prefix, "snapshot_at", record)
            return OwnerJournalSnapshot(
                self._tenant, scan.head, tuple(scan.selected.values()), scan.materialized
            )
        except OwnerJournalIntegrityError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            raise OwnerJournalIntegrityError("snapshot_at", self._tenant, record) from error

    def select(
        self, prepared: PreparedOwnerPublication, resulting_commitment: str
    ) -> SelectedOwnerDecision:
        request = prepared.request
        command = request.identity.command_id
        try:
            with self._authority_scope():
                entry = _Selected(
                    request=request,
                    issuance_id=prepared.issuance_id,
                    fence_generation=prepared.fence_generation,
                    fence_frontier=prepared.fence_frontier,
                    predecessor_commitment=prepared.predecessor_commitment,
                    resulting_commitment=resulting_commitment,
                )
                payload = _canonical(entry)
                # Validate reconstructed bytes before any durable append, including
                # nested DTOs built using Pydantic's intentionally unchecked copy API.
                _CODEC.validate_json(payload)
                _validate_bytes(request)
                if (
                    request.identity.tenant_id != self._tenant
                    or request.expected.tenant_id != self._tenant
                    or prepared.predecessor_commitment
                    != request.expected.expected_materialization_commitment
                    or request.expected.tenant_frontier == 2**64 - 1
                ):
                    raise ValueError("invalid tenant, predecessor or exhausted frontier")
                scan = self._scan("select", command)
                historical = scan.selected.get(command)
                if historical is not None:
                    if (
                        historical.prepared != prepared
                        or historical.resulting_commitment != resulting_commitment
                    ):
                        raise ValueError("changed selected decision")
                    return historical
                _validate_successor(request, scan.selected, scan.materialized)
                self._raw.append(payload, scan.head)
                result = self._scan("select", command).selected.get(command)
                if result is None:
                    raise ValueError("selected decision absent after append")
                return result
        except OwnerJournalIntegrityError, OwnerPublicationPending:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise OwnerJournalIntegrityError("select", self._tenant, command) from error

    def materialized(self, decision: SelectedOwnerDecision) -> None:
        command = decision.prepared.request.identity.command_id
        try:
            with self._authority_scope():
                scan = self._scan("materialized", command)
                if scan.selected.get(command) != decision:
                    raise ValueError("materialization does not bind exact selected decision")
                if command in scan.materialized:
                    return
                marker = _Materialized(
                    tenant_id=self._tenant,
                    command_id=command,
                    decision_id=decision.decision_id,
                    decision_fingerprint=decision.decision_fingerprint,
                    resulting_commitment=decision.resulting_commitment,
                )
                self._raw.append(_canonical(marker), scan.head)
                self._scan("materialized", command)
        except OwnerJournalIntegrityError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as error:
            raise OwnerJournalIntegrityError("materialized", self._tenant, command) from error
