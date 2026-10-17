"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

import hashlib

from tests.support.capabilities.agent_loop.execution_first_path_completion_contracts import (
    request as native_first_path_request,
)
from tests.support.workspace import envelope

from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ConversationCompletionEntryV1,
    ConversationCompletionSourceV1,
    ConversationSourceCutV1,
    PrepareConversationCompletionV1,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry


async def _request() -> PrepareConversationCompletionV1:
    original = await native_first_path_request(canonical_response=True)
    prepared = prepare_first_path_execution_completion(original)
    assert isinstance(prepared, PreparedExecutionCompletion)
    capture = original.source.selected_capture
    row = next(item for item in original.source.complete_sources if item.subject == capture)
    delivery = prepared.delivery.manifest.ordered_deliveries[0]
    entry = ConversationEntry(
        tenant_id=original.run.tenant,
        conversation_id="conversation",
        entry_id="assistant/1",
        sequence=1,
        origin_channel_id="channel",
        visible_channels=("channel",),
        role="assistant",
        accepted_bytes=delivery.rendered_bytes,
        envelope=envelope(delivery.rendered_bytes),
    )
    return PrepareConversationCompletionV1(
        command_id="conversation-complete",
        source_cut=ConversationSourceCutV1(
            tenant_id=original.source.tenant_id,
            database_id=original.source.database_id,
            tenant_commit_sequence=original.source.tenant_commit_sequence,
            materialization_commitment=original.source.materialization_commitment,
            expected_previous_entry=Absent(),
            source_selected_decision=row.selected_decision,
            source_physical_record=row.physical_record,
            source_schema_id=row.schema_id,
            source_bytes=row.canonical_record_bytes,
            source=ConversationCompletionSourceV1(
                captured_run=capture, selected_attempt=original.selected_attempt
            ),
        ),
        original_completion_request_bytes=original.canonical_bytes(),
        loop_preparation_bytes=prepared.canonical_bytes(),
        original_delivery_proposal_bytes=prepared.delivery.canonical_bytes(),
        proposed_terminal_run=prepared.run,
        proposed_terminal_run_head=original.source.current_run.model_copy(
            update={
                "revision": original.source.current_run.revision.model_copy(
                    update={
                        "head": prepared.run.head,
                        "fingerprint": hashlib.sha256(prepared.run.canonical_bytes()).hexdigest(),
                    }
                )
            }
        ),
        proposed_acceptance_head=prepared.delivery.acceptance,
        proposed_terminal_manifest=prepared.terminal_manifest,
        proposed_terminal_manifest_head=manifest_ref(prepared.terminal_manifest),
        proposed_accepted_delivery_manifest_bytes=prepared.delivery.manifest.canonical_bytes(),
        ordered_assistant_entries=(
            ConversationCompletionEntryV1(
                delivery_id=delivery.delivery_id, recipient_binding=delivery.selection, entry=entry
            ),
        ),
    )
