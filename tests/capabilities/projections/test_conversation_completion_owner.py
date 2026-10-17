"""H1 accepted native completion produces one non-authoritative conversation row."""

from __future__ import annotations

import time

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_R17_H1_PRODUCTION_MANIFEST
from chiplog.capabilities.projections.conversation_completion_owner import (
    ConversationCompletionOwner,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ConversationPreparationRejectedV1,
    PreparedConversationCompletionV1,
    decode_conversation_canonical_member,
)
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortSuccess,
)
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.capabilities.projections.conversation_completion_owner import (
    _request,
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
