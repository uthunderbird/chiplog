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
from dataclasses import dataclass

from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
    ConversationCanonicalMemberV2,
    decode_conversation_canonical_member,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
    complete_acceptance_command,
)
from chiplog.platform._sqlite import PhysicalPublicationCommand


class H1ConversationSourceUnavailable(ValueError):
    """Raised when H1 conversation provenance cannot be authenticated."""


def _require_registered_ports(runtime: object) -> None:
    """Require the future issuer-held source capability, never DTO provenance.

    The port must verify first-path object ownership and the retained completion
    wire itself, then supply one gate-held selected/physical inventory to
    :func:`decode_authenticated_conversation_history`.  A public dataclass or
    Pydantic exchange cannot satisfy either requirement by shape alone.
    """
    factory = getattr(runtime, "_h1_conversation_source_ports", None)
    if not callable(factory):
        raise H1ConversationSourceUnavailable(
            "H1 conversation lacks registered issuer-held source ports"
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


def _require(value: bool, reason: str) -> None:
    if not value:
        raise ValueError("H1 conversation history " + reason)


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


@dataclass(frozen=True, slots=True)
class H1ConversationCapture:
    """Reserved opaque result of a future raw selected/physical source read.

    This is deliberately not a wire DTO.  A future issuer must retain the raw
    selected envelope inventory, exact physical publication inventory, database
    identity, and policy-derived request behind this private value.
    """

    _issuer_id: int


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

    def capture_current(
        self,
        *,
        first_path: H1FirstPathCapture,
        completion_exchange: H1CompletionOwnerExchangeV1,
    ) -> H1ConversationCapture:
        """Refuse unissued evidence until raw source and policy ports are mounted.

        In particular, this must not consume a caller-provided first-path DTO,
        completion exchange, previous-entry claim, or disclosure envelope.  A
        future implementation may return only after proving that the source is
        the selected native captured Run at lineage ``[-2]`` and after joining
        every conversation member to both raw selection and SQL materialization.
        """
        if type(first_path) is not H1FirstPathCapture:
            raise TypeError("H1 conversation sources require an exact first-path capture")
        if type(completion_exchange) is not H1CompletionOwnerExchangeV1:
            raise TypeError("H1 conversation sources require an exact retained completion exchange")
        _require_registered_ports(self._runtime)
        raise H1ConversationSourceUnavailable(
            "H1 conversation raw selected/physical reader and entry disclosure policy "
            "are not mounted"
        )

    def check_current(self, capture: object) -> bool:
        """Never accept a copied or unissued capture as a current source cut."""
        return False
