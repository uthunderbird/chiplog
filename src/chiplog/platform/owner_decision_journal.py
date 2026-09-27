"""Strict broker adapter for an independently authenticated decision journal."""

import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

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
from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
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


class _SelectedV2(BrokerDTO):
    kind: Literal["SELECTED"] = "SELECTED"
    schema_id: Literal["chiplog.owner-decision.v2"] = "chiplog.owner-decision.v2"
    request: RegisteredPublication
    issuance_id: Identity
    fence_generation: Identity
    fence_frontier: UInt64
    predecessor_commitment: Digest
    resulting_commitment: Digest
    h1_delivery_binding: H1DeliveryBinding


type _Entry = _Selected | _Materialized | _SelectedV2
_CODECS: dict[tuple[str, str], TypeAdapter[_Entry]] = {
    ("chiplog.owner-decision.v1", "SELECTED"): TypeAdapter(_Selected),
    ("chiplog.owner-decision.v1", "MATERIALIZED"): TypeAdapter(_Materialized),
    ("chiplog.owner-decision.v2", "SELECTED"): TypeAdapter(_SelectedV2),
}

_H1_COMPLETION_ISSUANCE_SCHEMAS = frozenset(
    {
        "chiplog.composition.h1-completion-issuance.v1",
        "chiplog.composition.h1-completion-issuance.v2",
    }
)


def _canonical(entry: _Entry) -> bytes:
    return json.dumps(entry.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def canonical_owner_publication_bytes(request: RegisteredPublication) -> bytes:
    """The closed canonical request preimage used by an H1 V2 binding."""
    return json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(payload: bytes) -> _Entry:
    value = json.loads(payload, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("journal entry is not an object")
    schema_id, kind = value.get("schema_id"), value.get("kind")
    if not isinstance(schema_id, str) or not isinstance(kind, str):
        raise ValueError("journal entry lacks schema or kind")
    codec = _CODECS.get((schema_id, kind))
    if codec is None:
        raise ValueError("unknown journal entry schema/kind")
    # The first parse rejects duplicate keys at every nesting level and fixes
    # dispatch.  The codec then parses the original JSON so Pydantic retains
    # its configured strict base64-bytes and tuple decoding semantics.
    return codec.validate_json(payload)


def _is_h1_delivery_request(request: RegisteredPublication) -> bool:
    return (
        request.kind == "COMPLETE_DELIVERY_ATOMIC_V2"
        and request.authentication.applicability_schema in _H1_COMPLETION_ISSUANCE_SCHEMAS
    )


def _validate_v2_binding(entry: _SelectedV2) -> None:
    request = entry.request
    binding = entry.h1_delivery_binding
    if (
        not _is_h1_delivery_request(request)
        or binding.tenant_id != request.identity.tenant_id
        or binding.command_id != request.identity.command_id
        or binding.command_fingerprint != request.identity.command_fingerprint
        or binding.request_digest
        != hashlib.sha256(canonical_owner_publication_bytes(request)).hexdigest()
    ):
        raise ValueError("V2 selected delivery binding differs from H1 request")


def _validate_selected_envelope(entry: _Selected | _SelectedV2) -> None:
    h1_request = _is_h1_delivery_request(entry.request)
    has_binding = isinstance(entry, _SelectedV2)
    if h1_request != has_binding:
        raise ValueError("H1 selected decision requires its closed delivery binding")
    if isinstance(entry, _SelectedV2):
        _validate_v2_binding(entry)


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
                entry = _decode(payload)
                if _canonical(entry) != payload:
                    raise ValueError("noncanonical journal entry")
                if isinstance(entry, (_Selected, _SelectedV2)):
                    _validate_selected_envelope(entry)
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
                            entry.h1_delivery_binding if isinstance(entry, _SelectedV2) else None,
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
                entry: _Selected | _SelectedV2
                h1_request = _is_h1_delivery_request(request)
                if h1_request != (prepared.h1_delivery_binding is not None):
                    raise ValueError("H1 V2 selected decision requires its closed delivery binding")
                if prepared.h1_delivery_binding is None:
                    entry = _Selected(
                        request=request,
                        issuance_id=prepared.issuance_id,
                        fence_generation=prepared.fence_generation,
                        fence_frontier=prepared.fence_frontier,
                        predecessor_commitment=prepared.predecessor_commitment,
                        resulting_commitment=resulting_commitment,
                    )
                else:
                    entry = _SelectedV2(
                        request=request,
                        issuance_id=prepared.issuance_id,
                        fence_generation=prepared.fence_generation,
                        fence_frontier=prepared.fence_frontier,
                        predecessor_commitment=prepared.predecessor_commitment,
                        resulting_commitment=resulting_commitment,
                        h1_delivery_binding=prepared.h1_delivery_binding,
                    )
                payload = _canonical(entry)
                # Validate reconstructed bytes before any durable append, including
                # nested DTOs built using Pydantic's intentionally unchecked copy API.
                decoded = _decode(payload)
                if not isinstance(decoded, (_Selected, _SelectedV2)):
                    raise ValueError("selected journal entry decoded as a materialization marker")
                _validate_selected_envelope(decoded)
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
