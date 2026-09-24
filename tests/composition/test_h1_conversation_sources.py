"""Fail-closed boundary for H1 conversation-source preparation."""

from __future__ import annotations

import hashlib
import json
from typing import cast

import pytest

from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    SourceReference,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_conversation_sources import (
    H1ConversationPhysicalMember,
    H1ConversationSources,
    H1SelectedConversationPublication,
    decode_authenticated_conversation_history,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord


def test_reader_rejects_a_noncanonical_runtime() -> None:
    with pytest.raises(TypeError, match="canonical common CLI runtime"):
        H1ConversationSources(cast("CommonCliExecutionRuntime", object()))


@pytest.mark.parametrize(
    ("first_path", "completion_exchange"),
    [
        (object(), object()),
        (cast("H1FirstPathCapture", object()), object()),
        (object(), cast("H1CompletionOwnerExchangeV1", object())),
    ],
)
def test_capture_refuses_unissued_or_dto_shaped_inputs_before_conversation_ipc(
    first_path: object, completion_exchange: object
) -> None:
    reader = object.__new__(H1ConversationSources)

    with pytest.raises((TypeError, ValueError), match="H1 conversation"):
        reader.capture_current(
            first_path=cast("H1FirstPathCapture", first_path),
            completion_exchange=cast("H1CompletionOwnerExchangeV1", completion_exchange),
        )


def test_recheck_never_treats_unissued_capture_as_current() -> None:
    reader = object.__new__(H1ConversationSources)

    assert reader.check_current(object()) is False


def _entry(sequence: int = 1) -> ConversationEntry:
    source = SourceReference(
        tenant_id="tenant",
        owner="agent_loop",
        record_id="source",
        record_version="1:digest",
        content_digest="digest",
        label_head="label",
        label=DisclosureLabel(
            lattice_version="chiplog.disclosure.v1",
            value="ENDPOINT_RESTRICTED",
            allowed_endpoints=("channel",),
        ),
    )
    return ConversationEntry(
        tenant_id="tenant",
        conversation_id="conversation:tenant",
        entry_id=f"assistant/{sequence}",
        sequence=sequence,
        origin_channel_id="channel",
        visible_channels=("channel",),
        role="assistant",
        accepted_bytes=b"accepted",
        envelope=DisclosureEnvelope(
            tenant_id="tenant",
            content_digest=hashlib.sha256(b"accepted").hexdigest(),
            sources=(source,),
            label=source.label,
            policy_head="policy",
            contour_head="contour",
            deletion_fence_head="r6",
        ),
    )


def _legacy_publication(
    entry: ConversationEntry,
) -> tuple[H1SelectedConversationPublication, H1ConversationPhysicalMember]:
    entry_json = entry.model_dump_json()
    raw = json.dumps(
        {"entry_json": entry_json, "fingerprint": hashlib.sha256(entry_json.encode()).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    record = PhysicalRecord(
        entry.entry_id,
        CONVERSATION_OWNER,
        CONVERSATION_SCHEMA,
        raw,
        hashlib.sha256(raw).hexdigest(),
    )
    command = PhysicalPublicationCommand(
        "tenant", "conversation.accept", entry.entry_id, "request", 0, "r6", 0, 0, (record,)
    )
    return (
        H1SelectedConversationPublication("decision", b"selected", command, None),
        H1ConversationPhysicalMember(
            entry.entry_id, CONVERSATION_OWNER, CONVERSATION_SCHEMA, raw, 1
        ),
    )


def test_mixed_history_derives_predecessor_only_from_exact_selected_physical_legacy_row() -> None:
    selected, physical = _legacy_publication(_entry())

    history = decode_authenticated_conversation_history("tenant", (selected,), (physical,))

    assert history.entries == (_entry(),)
    assert isinstance(history.expected_previous_entry, Present)
    assert history.expected_previous_entry.head == "assistant/1"
    assert (
        history.expected_previous_entry.fingerprint
        == hashlib.sha256(physical.canonical_bytes).hexdigest()
    )


def test_mixed_history_rejects_unselected_hidden_or_unproven_v2_rows() -> None:
    selected, physical = _legacy_publication(_entry())
    hidden = H1ConversationPhysicalMember(
        "assistant/2", CONVERSATION_OWNER, CONVERSATION_SCHEMA, physical.canonical_bytes, 2
    )
    with pytest.raises(ValueError, match="physical conversation"):
        decode_authenticated_conversation_history("tenant", (selected,), (physical, hidden))

    extra_same_publication = H1ConversationPhysicalMember(
        "companion",
        "agent_loop",
        "chiplog.agent-loop.execution-record.v3",
        b"extra",
        1,
    )
    with pytest.raises(ValueError, match="member count"):
        decode_authenticated_conversation_history(
            "tenant", (selected,), (physical, extra_same_publication)
        )

    v2_raw = b"{}"
    v2 = PhysicalRecord(
        "assistant/1",
        CONVERSATION_OWNER,
        ACCEPTED_ENTRY_SCHEMA,
        v2_raw,
        hashlib.sha256(v2_raw).hexdigest(),
    )
    command = PhysicalPublicationCommand(
        "tenant",
        "agent_loop.complete_acceptance.v2",
        "id",
        "request",
        0,
        "r6",
        0,
        0,
        (v2,),
    )
    selected_v2 = H1SelectedConversationPublication("decision", b"selected", command, None)
    physical_v2 = H1ConversationPhysicalMember(
        v2.record_id, v2.owner, v2.schema_id, v2.canonical_bytes, 1
    )
    with pytest.raises(ValueError, match="retained complete-acceptance"):
        decode_authenticated_conversation_history("tenant", (selected_v2,), (physical_v2,))
