"""Fail-closed H1 conversation historical-source boundary.

The conversation owner accepts a shaped ``ConversationSourceCutV1`` but does
not authenticate it.  A positive capture therefore needs an issuer-held
first-path provenance token, raw selected journals, exact SQL publication
readback, and a source-backed entry/disclosure policy.  Those ports are not
mounted yet.  This module deliberately exposes the future composition seam
while refusing every caller-supplied capture, rather than turning a canonical
DTO or fixture policy into historical authority.

V1 conversation history is intentionally untouched.  Its decoder cannot be
widened to establish the required mixed legacy/V2 inventory.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    decode_completion_request,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
    ConversationCanonicalMemberV2,
    ConversationCompletionEntryV1,
    ConversationCompletionSourceV1,
    ConversationSourceCutV1,
    PrepareConversationCompletionV1,
    decode_conversation_canonical_member,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import DisclosureLabel
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_conversation_policy import (
    H1RegisteredConversationPolicy,
    _CaptureMaterial,
    _issue_for_authenticated_port,
    derive_entry,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
    complete_acceptance_command,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.publication_readback import inspect_publication


class H1ConversationSourceUnavailable(ValueError):
    """Raised when H1 conversation provenance cannot be authenticated."""


def _require_registered_port(runtime: object) -> None:
    """Require the single issuer-held source capability, never DTO provenance.

    The port must verify first-path object ownership and the retained completion
    wire itself, then supply one gate-held selected/physical inventory to
    :func:`decode_authenticated_conversation_history`.  A public dataclass or
    Pydantic exchange cannot satisfy either requirement by shape alone.
    """
    port = getattr(runtime, "_h1_conversation_source_port", None)
    if port is None:
        raise H1ConversationSourceUnavailable(
            "H1 conversation lacks a registered issuer-held source port"
        )


@dataclass(frozen=True, slots=True)
class H1ConversationPhysicalMember:
    """One raw SQL row retained by the caller-owned authenticated read cut."""

    record_id: str
    owner: str
    schema_id: str
    canonical_bytes: bytes
    commit_sequence: int


@dataclass(frozen=True, slots=True)
class H1SelectedConversationPublication:
    """One raw selected command, with V2's retained reconstruction evidence."""

    decision_id: str
    decision_bytes: bytes
    command: PhysicalPublicationCommand
    complete_acceptance: RetainedCompleteAcceptanceExchangeV1 | None


@dataclass(frozen=True, slots=True)
class H1AuthenticatedConversationHistory:
    """Strict mixed-schema entry inventory and its private predecessor observation."""

    entries: tuple[ConversationEntry, ...]
    expected_previous_entry: Absent | Present


@dataclass(frozen=True, slots=True)
class _AuthenticatedConversationInventory:
    """One gate-held, complete selected/physical tenant inventory.

    This carrier is deliberately private.  It is source material for A's
    eventual request issuer, never a caller-supplied history shortcut.
    """

    selected: tuple[H1SelectedConversationPublication, ...]
    physical: tuple[H1ConversationPhysicalMember, ...]
    commitment: str
    tenant_sequence: int
    database_identity: tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class _IssuedConversationCapture:
    """A's exact replay material, retained outside its opaque capability."""

    capture: H1ConversationCapture
    first_path: H1FirstPathCapture
    completion_exchange: H1CompletionOwnerExchangeV1
    inventory: _AuthenticatedConversationInventory
    history: H1AuthenticatedConversationHistory
    policy_capture: object
    request: PrepareConversationCompletionV1


def _require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError("H1 conversation history " + reason)


def _run_ref(run: object) -> CallSubjectHead:
    value = cast(Any, run)
    raw = value.canonical_bytes()
    return CallSubjectHead(
        subject_id=value.run_id,
        revision=Present(
            head=value.head,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
    )


def authenticated_request_selected_attempt(authenticated: object) -> CallSubjectHead:
    """Decode the registry-held original request; never accept a caller attempt."""
    request = decode_completion_request(authenticated.request_bytes)  # type: ignore[attr-defined]
    if type(request) is not PrepareExecutionCompletionFirstPathV2:
        raise H1ConversationSourceUnavailable(
            "H1 conversation registry request is not native first-path completion"
        )
    return request.selected_attempt


def _legacy_entry(row: H1ConversationPhysicalMember, tenant_id: str) -> ConversationEntry:
    _require(row.schema_id == CONVERSATION_SCHEMA, "legacy schema differs")
    try:
        value = json.loads(row.canonical_bytes)
        _require(isinstance(value, dict), "legacy row is not an object")
        _require(set(value) == {"entry_json", "fingerprint"}, "legacy row keys differ")
        entry_json = value["entry_json"]
        _require(isinstance(entry_json, str), "legacy entry JSON is absent")
        _require(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            == row.canonical_bytes,
            "legacy row is noncanonical",
        )
        _require(
            value["fingerprint"] == hashlib.sha256(entry_json.encode()).hexdigest(),
            "legacy entry fingerprint differs",
        )
        entry = ConversationEntry.model_validate_json(entry_json)
        _require(entry.model_dump_json() == entry_json, "legacy entry JSON is noncanonical")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("H1 conversation history malformed legacy row") from error
    _require(
        entry.tenant_id == tenant_id and entry.entry_id == row.record_id,
        "legacy entry identity differs",
    )
    return entry


def _v2_entry(row: H1ConversationPhysicalMember, tenant_id: str) -> ConversationEntry:
    _require(row.schema_id == ACCEPTED_ENTRY_SCHEMA, "v2 schema differs")
    member = ConversationCanonicalMemberV2(
        record_id=row.record_id,
        canonical_bytes=row.canonical_bytes,
        fingerprint=hashlib.sha256(row.canonical_bytes).hexdigest(),
    )
    decoded = decode_conversation_canonical_member(member)
    _require(decoded.entry.tenant_id == tenant_id, "v2 entry tenant differs")
    return decoded.entry


def decode_authenticated_conversation_history(
    tenant_id: str,
    selected: tuple[H1SelectedConversationPublication, ...],
    physical: tuple[H1ConversationPhysicalMember, ...],
) -> H1AuthenticatedConversationHistory:
    """Join complete raw selections to the entire physical mixed-schema slice.

    The caller must obtain both inventories within one authority-gate-held SQL
    transaction.  This function refuses partial command membership, an
    unselected physical conversation record, unknown schemas, and sequence or
    identity ambiguity.  V2 members additionally require the retained owner
    exchange that reconstructs their exact complete-acceptance command.
    """
    _require(isinstance(tenant_id, str) and bool(tenant_id), "tenant is invalid")
    _require(
        len({item.decision_id for item in selected}) == len(selected),
        "selected decisions duplicate",
    )
    _require(
        len({item.command.expected_head for item in selected}) == len(selected),
        "selected commands share a predecessor",
    )
    by_record = {item.record_id: item for item in physical}
    _require(len(by_record) == len(physical), "physical records duplicate")
    expected_members: dict[str, tuple[PhysicalRecord, int]] = {}
    for selected_item in selected:
        command = selected_item.command
        expected_sequence = command.expected_head + 1
        for record in command.records:
            _require(
                record.record_id not in expected_members,
                "selected physical member is duplicated",
            )
            expected_members[record.record_id] = (record, expected_sequence)
    matched_conversation_ids: set[str] = set()
    entries: list[tuple[ConversationEntry, H1ConversationPhysicalMember]] = []
    for selected_item in selected:
        command = selected_item.command
        _require(command.tenant_id == tenant_id, "selected command crosses tenants")
        _require(bool(selected_item.decision_bytes), "selected decision bytes are absent")
        _require(bool(command.records), "selected command has no members")
        _require(
            len({record.record_id for record in command.records}) == len(command.records),
            "selected command member identities duplicate",
        )
        expected_sequence = command.expected_head + 1
        at_sequence = tuple(row for row in physical if row.commit_sequence == expected_sequence)
        _require(
            len(at_sequence) == len(command.records),
            "physical publication member count differs",
        )
        _require(
            {row.record_id for row in at_sequence}
            == {record.record_id for record in command.records},
            "physical publication membership differs",
        )
        if any(record.schema_id == ACCEPTED_ENTRY_SCHEMA for record in command.records):
            _require(
                selected_item.complete_acceptance is not None,
                "v2 row lacks retained complete-acceptance evidence",
            )
            evidence = selected_item.complete_acceptance
            assert evidence is not None
            _require(
                complete_acceptance_command(evidence) == command,
                "v2 selected command differs from retained complete-acceptance evidence",
            )
        for expected in command.records:
            actual = by_record.get(expected.record_id)
            _require(actual is not None, "selected physical member is absent")
            assert actual is not None
            _require(
                (
                    actual.owner,
                    actual.schema_id,
                    actual.canonical_bytes,
                    actual.commit_sequence,
                )
                == (
                    expected.owner,
                    expected.schema_id,
                    expected.canonical_bytes,
                    expected_sequence,
                ),
                "selected physical member differs",
            )
            if actual.owner != CONVERSATION_OWNER:
                continue
            _require(
                actual.record_id not in matched_conversation_ids,
                "conversation member duplicates",
            )
            matched_conversation_ids.add(actual.record_id)
            if actual.schema_id == CONVERSATION_SCHEMA:
                entry = _legacy_entry(actual, tenant_id)
            elif actual.schema_id == ACCEPTED_ENTRY_SCHEMA:
                entry = _v2_entry(actual, tenant_id)
            else:
                raise ValueError("H1 conversation history has an unsupported conversation schema")
            entries.append((entry, actual))
    for row in physical:
        if row.owner == CONVERSATION_OWNER:
            _require(
                row.record_id in matched_conversation_ids,
                "physical conversation row is unselected",
            )
    _require(
        set(by_record) == set(expected_members),
        "physical member is unselected or selected member is absent",
    )
    for record_id, actual in by_record.items():
        expected, expected_sequence = expected_members[record_id]
        _require(
            (
                actual.owner,
                actual.schema_id,
                actual.canonical_bytes,
                actual.commit_sequence,
            )
            == (
                expected.owner,
                expected.schema_id,
                expected.canonical_bytes,
                expected_sequence,
            ),
            "physical member differs from selected publication",
        )
    entries.sort(key=lambda item: item[0].sequence)
    decoded = tuple(item[0] for item in entries)
    _require(
        tuple(entry.sequence for entry in decoded) == tuple(range(1, len(decoded) + 1)),
        "sequence is gapped or duplicated",
    )
    _require(
        len({entry.entry_id for entry in decoded}) == len(decoded),
        "entry identity duplicates",
    )
    _require(
        len({entry.conversation_id for entry in decoded}) <= 1,
        "conversation identities compete",
    )
    if not entries:
        predecessor: Absent | Present = Absent()
    else:
        latest, physical_latest = entries[-1]
        predecessor = Present(
            head=latest.entry_id,
            fingerprint=hashlib.sha256(physical_latest.canonical_bytes).hexdigest(),
        )
    return H1AuthenticatedConversationHistory(decoded, predecessor)


class H1ConversationCapture:
    """Opaque result of an A-held raw selected/physical source read.

    This is deliberately not a wire DTO.  Its material remains in A's strong
    identity table so a reconstructed value, serialization, or a capture from
    another runtime cannot become current by matching fields.
    """

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 conversation captures are issuer-held capabilities")

    def __copy__(self) -> H1ConversationCapture:
        raise TypeError("H1 conversation capture cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ConversationCapture:
        del memo
        raise TypeError("H1 conversation capture cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 conversation capture cannot be serialized")


class H1ConversationSources:
    """Private source reader for the H1 conversation preparation issuer.

    There is currently no authenticated implementation of the full source
    inventory or of the assistant entry/disclosure policy.  ``capture_current``
    therefore has a bounded fail-closed result; ``check_current`` cannot bless
    an unissued value.
    """

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 conversation sources require the canonical common CLI runtime")
        self._runtime = runtime
        self._gate = runtime._authority_gate()
        self._issued: dict[int, _IssuedConversationCapture] = {}
        self._closed = False

    def _revoke_all(self) -> None:
        """Revoke every A capture before the installed runtime tears down."""
        self._issued.clear()
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise H1ConversationSourceUnavailable("H1 conversation source owner is closed")

    def _read_complete_current_inventory(
        self, first_path: H1FirstPathCapture
    ) -> _AuthenticatedConversationInventory:
        """Read every selected command and every physical member at one live cut.

        The loop journal and the protected owner journal are both read while
        the canonical gate is held.  Every selected command is then inspected
        against the same read-only SQLite transaction and the result must
        account for *all* tenant records.  This deliberately returns no
        partial conversation-only subset: a new sibling, an owner selection
        we cannot authenticate, or an unselected record makes the cut stale.

        It is intentionally not an authority issuer.  B's past-exchange
        registry and P's policy projection must still join this inventory
        before ``capture_current`` can issue a request.
        """
        if type(first_path) is not H1FirstPathCapture:
            raise TypeError("H1 conversation inventory requires an exact first-path capture")
        self._require_open()
        self._gate.require_held()
        runtime = self._runtime
        source_owner = getattr(runtime, "_h1_first_path_sources", None)
        if source_owner is None or getattr(source_owner, "_runtime", None) is not runtime:
            raise H1ConversationSourceUnavailable(
                "H1 conversation lacks its mounted first-path source owner"
            )
        replay = getattr(source_owner, "replay_current_native_cut", None)
        if not callable(replay):
            raise H1ConversationSourceUnavailable(
                "H1 conversation first-path source owner cannot replay its current cut"
            )
        native = replay(first_path)
        if (
            native.source != first_path.source
            or native.source.tenant_id != runtime._tenant_id
            or native.source.materialization_commitment != native.commitment
        ):
            raise H1ConversationSourceUnavailable("H1 conversation native cut differs")
        for name in ("_pending", "_pending_owners", "_pending_gate_publications"):
            pending = getattr(runtime, name, None)
            if callable(pending) and pending():
                raise H1ConversationSourceUnavailable(
                    "H1 conversation inventory has a pending selected publication"
                )
        runtime._check_database_identity()
        database = Path(runtime._database).resolve(strict=True)
        before = database.stat()
        selected = self._selected_publications()
        if not selected:
            raise H1ConversationSourceUnavailable("H1 conversation selected inventory is empty")
        if len({item.decision_id for item in selected}) != len(selected):
            raise H1ConversationSourceUnavailable("H1 conversation selected decisions collide")
        if len({item.command.expected_head for item in selected}) != len(selected):
            raise H1ConversationSourceUnavailable(
                "H1 conversation selected publications have competing predecessors"
            )
        selected = tuple(sorted(selected, key=lambda item: item.command.expected_head))
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN")
            commitment = capture_authority_snapshot_commitment(connection, runtime._tenant_id)
            if commitment != runtime._commitment_journal.load(runtime._tenant_id):
                raise H1ConversationSourceUnavailable(
                    "H1 conversation inventory differs from its commitment anchor"
                )
            head = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?", (runtime._tenant_id,)
            ).fetchone()
            tenant_sequence = 0 if head is None else int(head[0])
            if (
                tenant_sequence != native.source.tenant_commit_sequence
                or commitment != native.commitment
            ):
                raise H1ConversationSourceUnavailable(
                    "H1 conversation inventory differs from the native source cut"
                )
            for item in selected:
                if item.command.tenant_id != runtime._tenant_id or (
                    inspect_publication(connection, item.command, item.command.expected_head + 1)
                    != "COMPLETE"
                ):
                    raise H1ConversationSourceUnavailable(
                        "H1 conversation selected publication is not exactly materialized"
                    )
            rows = tuple(
                connection.execute(
                    "SELECT record_id,owner,schema_id,canonical_bytes,commit_sequence "
                    "FROM records WHERE tenant_id=? ORDER BY commit_sequence,record_id",
                    (runtime._tenant_id,),
                )
            )
        runtime._check_database_identity()
        after = database.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise H1ConversationSourceUnavailable(
                "H1 conversation database identity changed during inventory read"
            )
        physical = tuple(
            H1ConversationPhysicalMember(str(record_id), str(owner), str(schema_id), raw, sequence)
            for record_id, owner, schema_id, raw, sequence in rows
            if type(raw) is bytes and type(sequence) is int
        )
        if len(physical) != len(rows):
            raise H1ConversationSourceUnavailable(
                "H1 conversation physical inventory has invalid rows"
            )
        return _AuthenticatedConversationInventory(
            selected,
            physical,
            commitment,
            tenant_sequence,
            (str(database), before.st_dev, before.st_ino),
        )

    def _selected_publications(self) -> tuple[H1SelectedConversationPublication, ...]:
        """Authenticate loop and protected-owner selections before SQL readback."""
        self._require_open()
        self._gate.require_held()
        selected: list[H1SelectedConversationPublication] = []
        try:
            loop_entries = self._runtime._loop_decisions().entries()
            for decision_id, _predecessor, raw in loop_entries:
                entry = json.loads(raw)
                if entry.get("kind") != "DECIDED":
                    continue
                command = self._runtime._publication(entry)
                if command.tenant_id != self._runtime._tenant_id:
                    raise ValueError("cross-tenant loop selection")
                selected.append(H1SelectedConversationPublication(decision_id, raw, command, None))
            owner_factory = getattr(self._runtime, "_owner_decisions", None)
            if not callable(owner_factory):
                raise ValueError("protected owner journal is absent")
            owner_journal = owner_factory()
            raw_entries = owner_journal._raw.entries()
            snapshot = owner_journal.snapshot()
            if owner_journal._raw.entries() != raw_entries:
                raise ValueError("protected owner journal changed during inventory read")
            raw_by_id = {decision_id: raw for decision_id, _previous, raw in raw_entries}
            if len(raw_by_id) != len(raw_entries):
                raise ValueError("protected owner journal decision identities duplicate")
            for decision in snapshot.decisions:
                command = self._runtime._owner_command(decision)
                if command.tenant_id != self._runtime._tenant_id:
                    continue
                request = decision.prepared.request
                owner_raw: object = raw_by_id.get(decision.decision_id)
                if (
                    not isinstance(owner_raw, bytes)
                    or hashlib.sha256(owner_raw).hexdigest() != decision.decision_fingerprint
                ):
                    raise ValueError("protected owner selection raw decision differs")
                evidence = self._complete_acceptance_evidence(request, command)
                selected.append(
                    H1SelectedConversationPublication(
                        decision.decision_id, owner_raw, command, evidence
                    )
                )
        except (
            AttributeError,
            KeyError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            raise H1ConversationSourceUnavailable(
                "H1 conversation selected journal is not authentically readable"
            ) from error
        return tuple(selected)

    @staticmethod
    def _complete_acceptance_evidence(
        request: object, command: PhysicalPublicationCommand
    ) -> RetainedCompleteAcceptanceExchangeV1 | None:
        """Reconstruct V2 evidence only from the protected selected H1 batch."""
        if not any(record.schema_id == ACCEPTED_ENTRY_SCHEMA for record in command.records):
            return None
        try:
            from chiplog.composition.h1_completion_issuance import h1_completion_exchange
            from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2

            if type(request) is not CompleteDeliveryBatchV2:
                raise ValueError("V2 conversation record lacks a complete delivery batch")
            evidence = h1_completion_exchange(request)
            if complete_acceptance_command(evidence) != command:
                raise ValueError("V2 selected command differs from retained owner exchange")
            return evidence
        except (ImportError, TypeError, ValueError) as error:
            raise H1ConversationSourceUnavailable(
                "H1 conversation V2 record lacks authenticated complete-acceptance evidence"
            ) from error

    def capture_current(
        self,
        *,
        first_path: H1FirstPathCapture,
        completion_exchange: H1CompletionOwnerExchangeV1,
    ) -> H1ConversationCapture:
        """Capture one exact B/P/native/history join while the gate is held."""
        if type(first_path) is not H1FirstPathCapture:
            raise TypeError("H1 conversation sources require an exact first-path capture")
        if type(completion_exchange) is not H1CompletionOwnerExchangeV1:
            raise TypeError("H1 conversation sources require an exact retained completion exchange")
        self._require_open()
        if getattr(self._runtime, "_h1_conversation_source_port", None) is not self:
            raise H1ConversationSourceUnavailable(
                "H1 conversation source owner is not the exact mounted owner"
            )
        try:
            from chiplog.composition.h1_completion_exchange_registry import (
                H1CompletionExchangeRegistry,
            )
            from chiplog.composition.h1_runtime_preissuance_port import (
                _H1RuntimePreissuancePort,
            )

            registry = getattr(self._runtime, "_h1_completion_exchange_registry", None)
            policy_port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
            if (
                type(registry) is not H1CompletionExchangeRegistry
                or type(policy_port) is not _H1RuntimePreissuancePort
            ):
                raise H1ConversationSourceUnavailable(
                    "H1 conversation lacks its installed B/P source owners"
                )
            authenticated = registry._replay_completion_exchange(first_path, completion_exchange)
            if authenticated.first_path is not first_path:
                raise H1ConversationSourceUnavailable(
                    "H1 conversation B registry first-path identity differs"
                )
            policy = policy_port._replay_conversation_policy(
                authenticated.scope_cap, authenticated.native_cap
            )
            inventory = self._read_complete_current_inventory(first_path)
            history = decode_authenticated_conversation_history(
                self._runtime._tenant_id, inventory.selected, inventory.physical
            )
            request, policy_capture = self._build_request(
                first_path, authenticated, policy, inventory, history
            )
        except (AttributeError, TypeError, ValueError) as error:
            if isinstance(error, H1ConversationSourceUnavailable):
                raise
            raise H1ConversationSourceUnavailable(
                "H1 conversation source inputs do not authenticate one current cut"
            ) from error
        capture = object.__new__(H1ConversationCapture)
        self._issued[id(capture)] = _IssuedConversationCapture(
            capture,
            first_path,
            completion_exchange,
            inventory,
            history,
            policy_capture,
            request,
        )
        return capture

    def _build_request(
        self,
        first_path: H1FirstPathCapture,
        authenticated: object,
        policy: object,
        inventory: _AuthenticatedConversationInventory,
        history: H1AuthenticatedConversationHistory,
    ) -> tuple[PrepareConversationCompletionV1, object]:
        """Derive the public request solely from B/P-issued inert evidence."""
        native_cap = authenticated.native_cap  # type: ignore[attr-defined]
        native = native_cap._native
        source = native.source
        lineage = source.complete_ordered_run_lineage
        if len(lineage) < 2:
            raise H1ConversationSourceUnavailable("H1 conversation native lineage is incomplete")
        captured = lineage[-2]
        if captured.schema_id not in {
            "chiplog.agent-loop.execution-record.v2",
            "chiplog.agent-loop.execution-record.v3",
        }:
            raise H1ConversationSourceUnavailable(
                "H1 conversation captured Run schema is unsupported"
            )
        source_rows = tuple(
            row
            for row in source.complete_sources
            if row.subject == source.selected_capture
            and row.schema_id == captured.schema_id
            and row.canonical_record_bytes == captured.canonical_bytes()
        )
        if len(source_rows) != 1:
            raise H1ConversationSourceUnavailable(
                "H1 conversation captured Run source provenance differs"
            )
        source_row = source_rows[0]
        raw_decisions = tuple(
            decision
            for decision in native.lineage
            if decision.decision_id == source_row.selected_decision.subject_id
            and decision.decision_fingerprint == source_row.selected_decision.revision.fingerprint
        )
        matching_members = tuple(
            member
            for member in native.physical_members
            if member.decision_id == source_row.selected_decision.subject_id
            and member.record_id == source_row.physical_record.subject_id
            and member.schema_id == captured.schema_id
            and member.canonical_bytes == captured.canonical_bytes()
        )
        if len(raw_decisions) != 1 or len(matching_members) != 1:
            raise H1ConversationSourceUnavailable(
                "H1 conversation captured Run has no exact selected physical member"
            )
        raw_decision = raw_decisions[0]
        physical = matching_members[0]
        source_selected = CallSubjectHead(
            subject_id=raw_decision.decision_id,
            revision=Present(
                head=raw_decision.decision_id, fingerprint=raw_decision.decision_fingerprint
            ),
        )
        source_physical = CallSubjectHead(
            subject_id=physical.record_id,
            revision=Present(
                head="record:" + hashlib.sha256(physical.canonical_bytes).hexdigest(),
                fingerprint=hashlib.sha256(physical.canonical_bytes).hexdigest(),
            ),
        )
        if (
            source_row.selected_decision != source_selected
            or source_row.physical_record != source_physical
        ):
            raise H1ConversationSourceUnavailable(
                "H1 conversation captured Run selected source differs"
            )
        result = PreparedExecutionCompletion.model_validate_json(
            authenticated.result_bytes  # type: ignore[attr-defined]
        )
        if result.canonical_bytes() != authenticated.result_bytes:  # type: ignore[attr-defined]
            raise H1ConversationSourceUnavailable(
                "H1 conversation completion result is noncanonical"
            )
        if len(result.delivery.manifest.ordered_deliveries) != 1:
            raise H1ConversationSourceUnavailable("H1 conversation completion has not one delivery")
        delivery = result.delivery.manifest.ordered_deliveries[0]
        workspace = policy.workspace_policy  # type: ignore[attr-defined]
        if (
            workspace.tenant != source.tenant_id
            or workspace.principal != captured.principal
            or policy.recipient != captured.origin.recipient  # type: ignore[attr-defined]
            or delivery.selection != captured.origin
            or delivery.policy != policy.scope_policy_ref  # type: ignore[attr-defined]
        ):
            raise H1ConversationSourceUnavailable("H1 conversation P/native/delivery join differs")
        registration = H1RegisteredConversationPolicy(
            tenant_id=workspace.tenant,
            principal_id=workspace.principal,
            origin_recipient_id=policy.recipient.recipient_id,  # type: ignore[attr-defined]
            accepted_policy=policy.scope_policy_ref,  # type: ignore[attr-defined]
            conversation_id=workspace.registration.conversation_id,
            origin_channel_id=workspace.channel,
            visible_channels=workspace.registration.visible_channels,
            workspace_policy_head=workspace.heads.policy,
            contour_head=workspace.heads.contour,
            deletion_fence_head=workspace.heads.deletion,
            disclosure_endpoint=workspace.endpoint,
        )
        policy_capture = _issue_for_authenticated_port(
            _CaptureMaterial(
                registration=registration,
                run=captured,
                selected_run_contour_head=source.selected_admitted_input.contour_head,
                accepted_delivery=delivery,
                authenticated_history=history.entries,
                narrowing=DisclosureLabel(
                    lattice_version="chiplog.disclosure.v1",
                    value="ENDPOINT_RESTRICTED",
                    allowed_endpoints=(workspace.endpoint,),
                ),
            ),
            issuer=self,
        )
        entry = derive_entry(policy_capture)
        command_seed = (
            first_path.source.canonical_bytes()
            + authenticated.request_bytes  # type: ignore[attr-defined]
            + authenticated.result_bytes  # type: ignore[attr-defined]
        )
        request = PrepareConversationCompletionV1(
            command_id="h1-conversation:" + hashlib.sha256(command_seed).hexdigest(),
            source_cut=ConversationSourceCutV1(
                tenant_id=source.tenant_id,
                database_id=source.database_id,
                tenant_commit_sequence=inventory.tenant_sequence,
                materialization_commitment=inventory.commitment,
                expected_previous_entry=history.expected_previous_entry,
                source_selected_decision=source_selected,
                source_physical_record=source_physical,
                source_schema_id=captured.schema_id,
                source_bytes=captured.canonical_bytes(),
                source=ConversationCompletionSourceV1(
                    captured_run=source.selected_capture,
                    selected_attempt=authenticated_request_selected_attempt(authenticated),
                ),
            ),
            original_completion_request_bytes=authenticated.request_bytes,  # type: ignore[attr-defined]
            loop_preparation_bytes=authenticated.result_bytes,  # type: ignore[attr-defined]
            original_delivery_proposal_bytes=result.delivery.canonical_bytes(),
            proposed_terminal_run=result.run,
            proposed_terminal_run_head=_run_ref(result.run),
            proposed_acceptance_head=result.delivery.acceptance,
            proposed_terminal_manifest=result.terminal_manifest,
            proposed_terminal_manifest_head=manifest_ref(result.terminal_manifest),
            proposed_accepted_delivery_manifest_bytes=result.delivery.manifest.canonical_bytes(),
            ordered_assistant_entries=(
                ConversationCompletionEntryV1(
                    delivery_id=delivery.delivery_id,
                    recipient_binding=delivery.selection,
                    entry=entry,
                ),
            ),
        )
        return (
            PrepareConversationCompletionV1.model_validate_json(request.canonical_json_bytes()),
            policy_capture,
        )

    def _prepare_conversation_completion_request(
        self, capture: object
    ) -> PrepareConversationCompletionV1:
        """Release the exact canonical request held by one issuer-owned capture."""
        self._require_open()
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ConversationCapture
            or issued is None
            or issued.capture is not capture
        ):
            raise H1ConversationSourceUnavailable("H1 conversation capture is not A-issued")
        return issued.request

    def check_current(self, capture: object) -> bool:
        """Re-open every source owner and compare the exact retained cut."""
        if not hasattr(self, "_closed") or self._closed:
            return False
        if getattr(self._runtime, "_h1_conversation_source_port", None) is not self:
            return False
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ConversationCapture
            or issued is None
            or issued.capture is not capture
        ):
            return False
        try:
            with self._gate.hold():
                inventory = self._read_complete_current_inventory(issued.first_path)
                history = decode_authenticated_conversation_history(
                    self._runtime._tenant_id, inventory.selected, inventory.physical
                )
                rebuilt = self.capture_current(
                    first_path=issued.first_path,
                    completion_exchange=issued.completion_exchange,
                )
                current = self._issued.get(id(rebuilt))
                return (
                    current is not None
                    and current.inventory == issued.inventory == inventory
                    and current.history == issued.history == history
                    and current.request == issued.request
                )
        except AttributeError, TypeError, ValueError:
            return False
