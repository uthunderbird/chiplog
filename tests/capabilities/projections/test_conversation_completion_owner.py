"""H1 accepted native completion produces one non-authoritative conversation row."""

from __future__ import annotations

import hashlib
import time

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_R17_H1_PRODUCTION_MANIFEST
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
from chiplog.capabilities.projections.conversation_completion_owner import (
    ConversationCompletionOwner,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ConversationCompletionEntryV1,
    ConversationCompletionSourceV1,
    ConversationPreparationRejectedV1,
    ConversationSourceCutV1,
    PrepareConversationCompletionV1,
    PreparedConversationCompletionV1,
    decode_conversation_canonical_member,
)
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as native_first_path_request,
)
from tests.support.workspace import envelope


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


async def test_native_h1_completion_derives_one_canonical_conversation_member() -> None:
    result = ConversationCompletionOwner().prepare_completion(await _request())

    assert isinstance(result, PreparedConversationCompletionV1)
    assert len(result.ordered_members) == 1
    decoded = decode_conversation_canonical_member(result.ordered_members[0])
    assert decoded.source_kind == "COMPLETION"
    assert decoded.entry.accepted_bytes.startswith(b"UNVERIFIED MODEL COMMENTARY")


async def test_native_h1_completion_is_reachable_through_projection_owner_mount() -> None:
    request = await _request()
    with AuthorityBrokerRuntime(
        "tenant",
        1,
        "conversation-h1",
        R14_R17_H1_PRODUCTION_MANIFEST,
        b"secret",
        realized_leaves={
            "clock": ProductionClock(),
            "planning_store": ProductionPlanningStore(),
            "model": HermeticModel(),
            "effects_transport": HermeticEffectsProvider(receipt_key=b"fixture", scenarios=()),
        },
    ) as runtime:
        callee = runtime.session("projections")
        caller = BrokerSession(
            tenant_id=callee.tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker",
        )
        reply = await runtime.call(
            PublicPortCall(
                operation_id="projections.prepare_conversation_completion",
                request_id=request.command_id,
                caller=caller,
                callee=callee,
                schema_id="chiplog.conversation.prepare-completion.v1",
                canonical_payload=request.canonical_json_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                ),
            )
        )

    assert isinstance(reply, PublicPortSuccess)
    assert reply.schema_id == "chiplog.conversation.prepared-completion-result.v1"
    result = PreparedConversationCompletionV1.model_validate_json(reply.canonical_payload)
    assert result.canonical_json_bytes() == reply.canonical_payload
    assert len(result.ordered_members) == 1


@pytest.mark.parametrize("mutation", ("physical", "decision", "recipient", "stale_terminal"))
async def test_native_h1_completion_rejects_spliced_or_substituted_inputs(mutation: str) -> None:
    request = await _request()
    if mutation == "physical":
        cut = request.source_cut.model_copy(
            update={"source_physical_record": request.source_cut.source_selected_decision}
        )
        request = request.model_copy(update={"source_cut": cut})
    elif mutation == "decision":
        cut = request.source_cut.model_copy(
            update={"source_selected_decision": request.source_cut.source_physical_record}
        )
        request = request.model_copy(update={"source_cut": cut})
    elif mutation == "recipient":
        item = request.ordered_assistant_entries[0]
        request = request.model_copy(
            update={
                "ordered_assistant_entries": (
                    item.model_copy(update={"delivery_id": item.delivery_id + "/other"}),
                )
            }
        )
    else:
        request = request.model_copy(
            update={"proposed_terminal_run_head": request.source_cut.source_physical_record}
        )

    result = ConversationCompletionOwner().prepare_completion(request)

    assert isinstance(result, ConversationPreparationRejectedV1)
    assert result.code == "INTEGRITY_FAULT"
