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
    CompleteDeliveryBatchV2,
    DeliveryWorkClaimBatchV1,
    DeliveryWorkGenesisBatchV1,
    Digest,
    Identity,
    OwnerCommandBytes,
    OwnerRecordBytes,
    RegisteredPublication,
    UInt64,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.delivery_work_claim_binding_contracts import (
    DeliveryWorkClaimBindingV1,
    validate_delivery_work_claim_binding,
)
from chiplog.platform.delivery_work_genesis_binding_contracts import DeliveryWorkGenesisBindingV1
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


class _SelectedV3(BrokerDTO):
    kind: Literal["SELECTED"] = "SELECTED"
    schema_id: Literal["chiplog.owner-decision.v3"] = "chiplog.owner-decision.v3"
    request: RegisteredPublication
    issuance_id: Identity
    fence_generation: Identity
    fence_frontier: UInt64
    predecessor_commitment: Digest
    resulting_commitment: Digest
    delivery_work_genesis_binding: DeliveryWorkGenesisBindingV1


class _SelectedV4(BrokerDTO):
    kind: Literal["SELECTED"] = "SELECTED"
    schema_id: Literal["chiplog.owner-decision.v4"] = "chiplog.owner-decision.v4"
    request: RegisteredPublication
    issuance_id: Identity
    fence_generation: Identity
    fence_frontier: UInt64
    predecessor_commitment: Digest
    resulting_commitment: Digest
    delivery_work_claim_binding: DeliveryWorkClaimBindingV1


type _Entry = _Selected | _Materialized | _SelectedV2 | _SelectedV3 | _SelectedV4
_CODECS: dict[tuple[str, str], TypeAdapter[_Entry]] = {
    ("chiplog.owner-decision.v1", "SELECTED"): TypeAdapter(_Selected),
    ("chiplog.owner-decision.v1", "MATERIALIZED"): TypeAdapter(_Materialized),
    ("chiplog.owner-decision.v2", "SELECTED"): TypeAdapter(_SelectedV2),
    ("chiplog.owner-decision.v3", "SELECTED"): TypeAdapter(_SelectedV3),
    ("chiplog.owner-decision.v4", "SELECTED"): TypeAdapter(_SelectedV4),
}

_H1_COMPLETION_ISSUANCE_SCHEMAS = frozenset(
    {
        "chiplog.composition.h1-completion-issuance.v1",
        "chiplog.composition.h1-completion-issuance.v2",
        "chiplog.composition.h1-completion-issuance.v3",
        "chiplog.composition.h1-completion-issuance.v4",
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


def _is_delivery_work_genesis(request: RegisteredPublication) -> bool:
    return request.kind == "DELIVERY_WORK_GENESIS_V1"


def _is_delivery_work_claim(request: RegisteredPublication) -> bool:
    return request.kind == "DELIVERY_WORK_CLAIM_V1"


def _validate_v3_binding(entry: _SelectedV3) -> None:
    request = entry.request
    binding = entry.delivery_work_genesis_binding
    if not isinstance(request, DeliveryWorkGenesisBatchV1):
        raise ValueError("V3 selected delivery-work genesis request differs")
    if (
        not _is_delivery_work_genesis(request)
        or binding.selected_decision_id != request.selected_decision_id
        or binding.selected_decision_fingerprint != request.selected_decision_fingerprint
        or binding.selected_source != request.selected_source
        or binding.request_digest
        != hashlib.sha256(canonical_owner_publication_bytes(request)).hexdigest()
    ):
        raise ValueError("V3 selected delivery-work genesis binding differs from request")


def _validate_v3_selected_predecessor(
    binding: DeliveryWorkGenesisBindingV1, selected: dict[str, SelectedOwnerDecision]
) -> None:
    matches = [
        decision
        for decision in selected.values()
        if decision.decision_id == binding.selected_decision_id
        and decision.decision_fingerprint == binding.selected_decision_fingerprint
    ]
    if (
        len(matches) != 1
        or type(matches[0].prepared.request) is not CompleteDeliveryBatchV2
        or matches[0].prepared.request.authentication.applicability_schema
        != "chiplog.composition.h1-completion-issuance.v4"
    ):
        raise ValueError("genesis binding selected V4 decision is absent or foreign")
    prior = matches[0].prepared.request
    if prior.identity.tenant_id != binding.selected_source.tenant_id:
        raise ValueError("genesis binding selected predecessor tenant differs")
    source_roles = (
        (binding.selected_source.terminal_run, "agent_loop", "Run"),
        (binding.selected_source.terminal_manifest, "agent_loop", "TERMINAL_MANIFEST"),
        (binding.selected_source.intent, "effects", "ExternalActionIntent"),
    )
    for member, owner, record_kind in source_roles:
        candidates = tuple(
            record
            for record in prior.complete_records
            if record.owner == owner and record.record_kind == record_kind
        )
        if len(candidates) != 1 or (
            member.owner,
            member.record_kind,
            member.record_id,
            member.schema_id,
            member.canonical_record_bytes,
            member.fingerprint,
        ) != (
            candidates[0].owner,
            candidates[0].record_kind,
            candidates[0].record_id,
            candidates[0].schema_id,
            candidates[0].canonical_bytes,
            candidates[0].fingerprint,
        ):
            raise ValueError("genesis binding selected source role differs")


def _validate_v4_binding(entry: _SelectedV4) -> None:
    if not isinstance(entry.request, DeliveryWorkClaimBatchV1):
        raise ValueError("V4 selected delivery-work claim request differs")
    validate_delivery_work_claim_binding(entry.request, entry.delivery_work_claim_binding)


def _validate_v4_selected_predecessor(
    claim: DeliveryWorkClaimBatchV1,
    binding: DeliveryWorkClaimBindingV1,
    selected: dict[str, SelectedOwnerDecision],
    materialized: set[str] | frozenset[str],
) -> None:
    matches = tuple(
        decision
        for decision in selected.values()
        if decision.decision_id == binding.genesis_decision_id
        and decision.decision_fingerprint == binding.genesis_decision_fingerprint
    )
    if len(matches) != 1:
        raise ValueError("claim binding selected genesis decision is absent or ambiguous")
    genesis = matches[0]
    genesis_request = genesis.prepared.request
    genesis_binding = genesis.prepared.delivery_work_genesis_binding
    if (
        not _is_delivery_work_genesis(genesis_request)
        or genesis_binding is None
        or genesis_request.identity.command_id not in materialized
        or genesis_binding.selected_source != binding.selected_source
        or len(genesis_request.complete_records) != 1
    ):
        raise ValueError("claim binding selected materialized genesis differs")
    predecessor = binding.claim_projection.predecessor
    record = genesis_request.complete_records[0]
    if (
        predecessor.selected_source != binding.selected_source
        or record.canonical_bytes != predecessor.canonical_bytes()
        or record.fingerprint != hashlib.sha256(record.canonical_bytes).hexdigest()
    ):
        raise ValueError("claim binding exact genesis state differs")
    expected_heads = tuple(
        observed
        for observed in claim.expected.ordered_heads
        if observed.kind == "PRESENT"
        and observed.head.owner == record.owner
        and observed.head.record_kind == record.record_kind
        and observed.head.subject_id == predecessor.subject_id
        and observed.head.record_id == record.record_id
        and observed.head.fingerprint == record.fingerprint
    )
    if len(expected_heads) != 1:
        raise ValueError("claim binding lacks exact current genesis head CAS")


def _validate_selected_envelope(
    entry: _Selected | _SelectedV2 | _SelectedV3 | _SelectedV4,
) -> None:
    h1_request = _is_h1_delivery_request(entry.request)
    genesis_request = _is_delivery_work_genesis(entry.request)
    claim_request = _is_delivery_work_claim(entry.request)
    has_h1_binding = isinstance(entry, _SelectedV2)
    has_genesis_binding = isinstance(entry, _SelectedV3)
    has_claim_binding = isinstance(entry, _SelectedV4)
    if (
        h1_request != has_h1_binding
        or genesis_request != has_genesis_binding
        or claim_request != has_claim_binding
    ):
        raise ValueError("H1 selected decision requires its closed delivery binding")
    if isinstance(entry, _SelectedV2):
        _validate_v2_binding(entry)
    if isinstance(entry, _SelectedV3):
        _validate_v3_binding(entry)
    if isinstance(entry, _SelectedV4):
        _validate_v4_binding(entry)


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


def _scan_entries(
    entries: tuple[tuple[str, str | None, bytes], ...],
    tenant: str,
    operation: str,
    record: str,
    capture_head: str | None = None,
) -> _Scan:
    """Strictly decode one already captured tenant journal image.

    When ``capture_head`` is supplied, retain an immutable cut after that
    entry while continuing to validate the complete image.  This preserves the
    current-tail check performed by :meth:`snapshot_at` without decoding its
    valid prefix for a second time.
    """
    diagnostic_record = record
    try:
        selected: dict[str, SelectedOwnerDecision] = {}
        materialized: set[str] = set()
        predecessor: str | None = None
        captured: _Scan | None = None
        matches = 0
        for entry_id, entry_predecessor, payload in entries:
            diagnostic_record = entry_id
            if (
                not isinstance(entry_id, str)
                or (entry_predecessor is not None and not isinstance(entry_predecessor, str))
                or not isinstance(payload, bytes)
                or entry_predecessor != predecessor
                or entry_id
                != hashlib.sha256(
                    (predecessor or "GENESIS").encode() + b"\x00" + payload
                ).hexdigest()
            ):
                raise ValueError("journal prefix or predecessor differs")
            entry = _decode(payload)
            if _canonical(entry) != payload:
                raise ValueError("noncanonical journal entry")
            if isinstance(entry, (_Selected, _SelectedV2, _SelectedV3, _SelectedV4)):
                _validate_selected_envelope(entry)
                request = entry.request
                if isinstance(entry, _SelectedV3):
                    _validate_v3_selected_predecessor(entry.delivery_work_genesis_binding, selected)
                if isinstance(entry, _SelectedV4):
                    if not isinstance(entry.request, DeliveryWorkClaimBatchV1):
                        raise ValueError("V4 selected delivery-work claim request differs")
                    _validate_v4_selected_predecessor(
                        entry.request,
                        entry.delivery_work_claim_binding,
                        selected,
                        materialized,
                    )
                _validate_bytes(request)
                _validate_successor(request, selected, materialized)
                command = request.identity.command_id
                if request.identity.tenant_id != tenant or command in selected:
                    raise ValueError("foreign tenant or duplicate selected command")
                if (
                    request.expected.tenant_id != tenant
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
                        entry.delivery_work_genesis_binding
                        if isinstance(entry, _SelectedV3)
                        else None,
                        entry.delivery_work_claim_binding
                        if isinstance(entry, _SelectedV4)
                        else None,
                    ),
                    entry_id,
                    entry_id,
                    hashlib.sha256(payload).hexdigest(),
                    entry.resulting_commitment,
                    request.expected.tenant_frontier + 1,
                )
            else:
                decision = selected.get(entry.command_id)
                if (
                    entry.tenant_id != tenant
                    or decision is None
                    or entry.command_id in materialized
                    or entry.decision_id != decision.decision_id
                    or entry.decision_fingerprint != decision.decision_fingerprint
                    or entry.resulting_commitment != decision.resulting_commitment
                ):
                    raise ValueError("orphan, rival or duplicate materialization marker")
                materialized.add(entry.command_id)
            predecessor = entry_id
            if capture_head == entry_id:
                matches += 1
                captured = _Scan(entry_id, selected.copy(), frozenset(materialized))
        diagnostic_record = record
        if capture_head is not None:
            if matches != 1 or captured is None:
                raise ValueError("requested owner journal head is absent or ambiguous")
            return captured
        return _Scan(entries[-1][0] if entries else None, selected, frozenset(materialized))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        raise OwnerJournalIntegrityError(operation, tenant, diagnostic_record) from error


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


def snapshot_from_captured_entries(
    entries: tuple[tuple[str, str | None, bytes], ...], tenant_id: str
) -> OwnerJournalSnapshot:
    """Return the strict snapshot of immutable, caller-captured journal entries."""
    scan = _scan_entries(entries, tenant_id, "captured_snapshot", "entries")
    return OwnerJournalSnapshot(
        tenant_id, scan.head, tuple(scan.selected.values()), scan.materialized
    )


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
            return _scan_entries(entries, self._tenant, operation, record)
        except OwnerJournalIntegrityError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            raise OwnerJournalIntegrityError(operation, self._tenant, record) from error

    def _scan_entries(
        self,
        entries: tuple[tuple[str, str | None, bytes], ...],
        operation: str,
        record: str,
        capture_head: str | None = None,
    ) -> _Scan:
        return _scan_entries(entries, self._tenant, operation, record, capture_head)

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
            if head is None:
                self._scan_entries(entries, "snapshot_at", record)
                scan = _Scan(None, {}, frozenset())
            else:
                scan = self._scan_entries(entries, "snapshot_at", record, capture_head=head)
            return OwnerJournalSnapshot(
                self._tenant, scan.head, tuple(scan.selected.values()), scan.materialized
            )
        except OwnerJournalIntegrityError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
            raise OwnerJournalIntegrityError("snapshot_at", self._tenant, record) from error

    def predecessor_snapshot_for_selected(
        self, decision: SelectedOwnerDecision
    ) -> OwnerJournalSnapshot:
        """Return the exact journal prefix immediately before one selected decision.

        A selected decision's materialization commitment is deliberately not a
        journal locator.  This method therefore derives the prefix only from
        the validated raw H1-completion entry that physically carries it.
        """
        if not isinstance(decision, SelectedOwnerDecision):
            raise TypeError("selected decision is invalid")
        with self._authority_scope():
            try:
                entries = self._raw.entries()
                self._scan_entries(entries, "selected_predecessor", decision.decision_id)
                matches = [
                    (index, entry)
                    for index, entry in enumerate(entries)
                    if entry[0] == decision.decision_id
                ]
                if len(matches) != 1:
                    raise ValueError("selected decision is absent or ambiguous")
                index, (entry_id, predecessor, raw) = matches[0]
                entry = _decode(raw)
                if (
                    entry_id != decision.decision_head
                    or hashlib.sha256(raw).hexdigest() != decision.decision_fingerprint
                    or not isinstance(entry, _SelectedV2)
                    or type(entry.request) is not CompleteDeliveryBatchV2
                    or entry.request.authentication.applicability_schema
                    != "chiplog.composition.h1-completion-issuance.v4"
                ):
                    raise ValueError("selected decision differs from raw journal entry")
                from chiplog.composition.h1_completion_issuance import (
                    H1CompletionIssuanceV4,
                    decode_h1_completion_issuance,
                )

                if type(decode_h1_completion_issuance(entry.request)) is not H1CompletionIssuanceV4:
                    raise ValueError("selected H1 V4 issuance differs from raw journal entry")
                scan = self._scan_entries(entries[: index + 1], "selected_predecessor", entry_id)
                reconstructed = scan.selected.get(entry.request.identity.command_id)
                if (
                    reconstructed != decision
                    or reconstructed is None
                    or reconstructed.prepared.h1_delivery_binding is None
                ):
                    raise ValueError("selected H1 V4 decision differs from raw journal entry")
                return self.snapshot_at(predecessor)
            except OwnerJournalIntegrityError:
                raise
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
                raise OwnerJournalIntegrityError(
                    "selected_predecessor", self._tenant, decision.decision_id
                ) from error

    def select(
        self, prepared: PreparedOwnerPublication, resulting_commitment: str
    ) -> SelectedOwnerDecision:
        request = prepared.request
        command = request.identity.command_id
        try:
            with self._authority_scope():
                entry: _Selected | _SelectedV2 | _SelectedV3 | _SelectedV4
                h1_request = _is_h1_delivery_request(request)
                genesis_request = _is_delivery_work_genesis(request)
                claim_request = _is_delivery_work_claim(request)
                if (
                    h1_request != (prepared.h1_delivery_binding is not None)
                    or genesis_request != (prepared.delivery_work_genesis_binding is not None)
                    or claim_request != (prepared.delivery_work_claim_binding is not None)
                ):
                    raise ValueError("H1 V2 selected decision requires its closed delivery binding")
                if prepared.delivery_work_claim_binding is not None:
                    entry = _SelectedV4(
                        request=request,
                        issuance_id=prepared.issuance_id,
                        fence_generation=prepared.fence_generation,
                        fence_frontier=prepared.fence_frontier,
                        predecessor_commitment=prepared.predecessor_commitment,
                        resulting_commitment=resulting_commitment,
                        delivery_work_claim_binding=prepared.delivery_work_claim_binding,
                    )
                elif prepared.delivery_work_genesis_binding is not None:
                    entry = _SelectedV3(
                        request=request,
                        issuance_id=prepared.issuance_id,
                        fence_generation=prepared.fence_generation,
                        fence_frontier=prepared.fence_frontier,
                        predecessor_commitment=prepared.predecessor_commitment,
                        resulting_commitment=resulting_commitment,
                        delivery_work_genesis_binding=prepared.delivery_work_genesis_binding,
                    )
                elif prepared.h1_delivery_binding is None:
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
                if not isinstance(decoded, (_Selected, _SelectedV2, _SelectedV3, _SelectedV4)):
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
                if isinstance(entry, _SelectedV3):
                    _validate_v3_selected_predecessor(
                        entry.delivery_work_genesis_binding, scan.selected
                    )
                if isinstance(entry, _SelectedV4):
                    if not isinstance(entry.request, DeliveryWorkClaimBatchV1):
                        raise ValueError("V4 selected delivery-work claim request differs")
                    _validate_v4_selected_predecessor(
                        entry.request,
                        entry.delivery_work_claim_binding,
                        scan.selected,
                        scan.materialized,
                    )
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
