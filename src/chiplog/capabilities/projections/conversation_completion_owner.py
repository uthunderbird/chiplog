"""Deterministic H1 owner for one accepted native first-path conversation row.

This owner validates retained bytes and derives a projection member.  It does
not authenticate the broker's selected source or treat its DTO as authority.
"""

from __future__ import annotations

import hashlib
from typing import cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryAcceptanceProposal,
    DeliveryCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    decode_completion_request,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
)

from .conversation_preparation_contracts import (
    ConversationCompletionSourceV1,
    ConversationPreparationRejectedV1,
    PrepareConversationCompletionV1,
    PreparedConversationCompletionV1,
    conversation_complete_owner_commitment,
    conversation_recipient_binding_json,
    conversation_source_heads_json,
    conversation_source_request_fingerprint,
    make_conversation_canonical_member,
)


class ConversationCompletionOwnerViolation(ValueError):
    """The presented inert completion evidence has no single H1 interpretation."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ConversationCompletionOwnerViolation(reason)


def _run_ref(run: object) -> CallSubjectHead:
    raw = run.canonical_bytes()  # type: ignore[attr-defined]
    return CallSubjectHead(
        subject_id=run.run_id,  # type: ignore[attr-defined]
        revision=Present(head=run.head, fingerprint=hashlib.sha256(raw).hexdigest()),  # type: ignore[attr-defined]
    )


def _exact(model_type: type[object], raw: bytes) -> object:
    decoded = model_type.model_validate_json(raw)  # type: ignore[attr-defined]
    _require(decoded.canonical_bytes() == raw, "retained owner bytes are not canonical")
    return decoded


class ConversationCompletionOwner:
    """Prepare exactly one canonical assistant row for H1 accepted completion."""

    def prepare_completion(
        self, request: PrepareConversationCompletionV1
    ) -> PreparedConversationCompletionV1 | ConversationPreparationRejectedV1:
        try:
            return self._prepare(request)
        except (ValueError, TypeError, AttributeError) as error:
            return ConversationPreparationRejectedV1(
                operation="COMPLETION", code="INTEGRITY_FAULT", reason=str(error)
            )

    def _prepare(
        self, request: PrepareConversationCompletionV1
    ) -> PreparedConversationCompletionV1:
        request = PrepareConversationCompletionV1.model_validate_json(
            request.canonical_json_bytes()
        )
        original = decode_completion_request(request.original_completion_request_bytes)
        _require(
            isinstance(original, PrepareExecutionCompletionFirstPathV2),
            "H1 conversation requires native first-path completion",
        )
        original = cast(PrepareExecutionCompletionFirstPathV2, original)
        prepared = _exact(PreparedExecutionCompletion, request.loop_preparation_bytes)
        assert isinstance(prepared, PreparedExecutionCompletion)
        expected = prepare_first_path_execution_completion(original)
        _require(
            isinstance(expected, PreparedExecutionCompletion) and expected == prepared,
            "loop preparation differs from exact native first-path request",
        )
        proposal = _exact(DeliveryAcceptanceProposal, request.original_delivery_proposal_bytes)
        assert isinstance(proposal, DeliveryAcceptanceProposal)
        _require(proposal == prepared.delivery, "delivery proposal differs from loop preparation")
        _require(
            request.proposed_terminal_run == prepared.run,
            "terminal Run differs from loop preparation",
        )
        _require(
            request.proposed_terminal_run_head == _run_ref(prepared.run),
            "terminal Run head differs from terminal Run bytes",
        )
        _require(
            request.proposed_acceptance_head == proposal.acceptance,
            "acceptance head differs from delivery proposal",
        )
        _require(
            request.proposed_terminal_manifest == prepared.terminal_manifest
            and request.proposed_terminal_manifest_head == manifest_ref(prepared.terminal_manifest),
            "terminal manifest differs from loop preparation",
        )
        _require(
            request.proposed_accepted_delivery_manifest_bytes
            == proposal.manifest.canonical_bytes(),
            "accepted delivery manifest bytes differ from proposal",
        )
        _require(
            isinstance(request.source_cut.source, ConversationCompletionSourceV1),
            "wrong source cut",
        )
        source = cast(ConversationCompletionSourceV1, request.source_cut.source)
        capture = original.source.selected_capture
        source_rows = tuple(
            row
            for row in original.source.complete_sources
            if row.subject == capture
            and row.schema_id == request.source_cut.source_schema_id
            and row.canonical_record_bytes == request.source_cut.source_bytes
        )
        _require(len(source_rows) == 1, "conversation source has no unique captured Run row")
        source_row = source_rows[0]
        _require(
            source.captured_run == capture
            and source.selected_attempt == original.selected_attempt
            and request.source_cut.source_bytes
            == original.source.complete_ordered_run_lineage[-2].canonical_bytes()
            and request.source_cut.source_physical_record == source_row.physical_record
            and request.source_cut.source_selected_decision == source_row.selected_decision,
            "conversation cut differs from retained captured Run selection",
        )
        completion = DeliveryCompletion.model_validate_json(original.exact_captured_response)
        _require(
            completion.canonical_bytes() == original.exact_captured_response,
            "captured response is not canonical",
        )
        _require(
            len(completion.deliveries) == len(proposal.manifest.ordered_deliveries) == 1
            and len(request.ordered_assistant_entries) == 1,
            "H1 conversation requires exactly one delivery and assistant entry",
        )
        delivery = proposal.manifest.ordered_deliveries[0]
        source_delivery = completion.deliveries[0]
        _require(
            len(source_delivery.payload) == 1
            and isinstance(source_delivery.payload[0], Commentary),
            "H1 conversation requires one Commentary payload",
        )
        entry = request.ordered_assistant_entries[0]
        _require(
            entry.delivery_id == delivery.delivery_id
            and entry.recipient_binding == delivery.selection
            and entry.entry.accepted_bytes == delivery.rendered_bytes,
            "assistant entry differs from accepted delivery",
        )
        fingerprint = conversation_source_request_fingerprint(request)
        member = make_conversation_canonical_member(
            source_kind="COMPLETION",
            source_request_fingerprint=fingerprint,
            entry=entry.entry,
            delivery_id=entry.delivery_id,
            recipient_binding_json=conversation_recipient_binding_json(entry.recipient_binding),
            source_heads_json=conversation_source_heads_json(
                request.source_cut.source_selected_decision,
                request.source_cut.source_physical_record,
                request.source_cut.expected_previous_entry,
            ),
        )
        members = (member,)
        return PreparedConversationCompletionV1(
            source_request_fingerprint=fingerprint,
            ordered_members=members,
            complete_owner_commitment=conversation_complete_owner_commitment(members),
        )
