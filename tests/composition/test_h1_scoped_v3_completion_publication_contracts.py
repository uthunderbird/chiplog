"""Exact V3 scoped owner exchange closure into the inert V2 batch shape."""

from __future__ import annotations

import hashlib

import pytest

from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    completion_terminal_work_source_bytes,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
    run_ref,
)
from chiplog.capabilities.effects.h1_scoped_preparation import prepare_h1_scoped_delivery
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ConversationCompletionEntryV1,
    conversation_complete_owner_commitment,
    conversation_recipient_binding_json,
    conversation_source_heads_json,
    conversation_source_request_fingerprint,
    make_conversation_canonical_member,
)
from chiplog.composition import completion_publication_contracts as publication
from tests.capabilities.effects.test_h1_scoped_preparation import _call
from tests.support.completion_assembly import accepted_completion_fixture


async def _scoped_assembly() -> tuple[
    publication.PrepareCompleteAcceptanceAssemblyV1,
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
]:
    """Adapt real owner output into the other three real completion exchanges."""
    call = await _call()
    result = prepare_h1_scoped_delivery(call)
    assert isinstance(result, PreparedH1ScopedDeliveryV1)
    base = await accepted_completion_fixture("v3", "empty")
    original = call.request.original_completion_request
    prepared = call.request.prepared_completion
    delivery = prepared.delivery.manifest.ordered_deliveries[0]

    source_cut = base.assembly.conversation_request.source_cut.model_copy(
        update={
            "source_schema_id": original.run.schema_id,
            "source_bytes": original.run.canonical_bytes(),
        }
    )
    entry = base.assembly.conversation_request.ordered_assistant_entries[0].entry
    conversation_entry = ConversationCompletionEntryV1(
        delivery_id=delivery.delivery_id,
        recipient_binding=delivery.selection,
        entry=entry,
    )
    conversation_request = base.assembly.conversation_request.model_copy(
        update={
            "original_completion_request_bytes": original.canonical_bytes(),
            "source_cut": source_cut,
            "loop_preparation_bytes": prepared.canonical_bytes(),
            "original_delivery_proposal_bytes": prepared.delivery.canonical_bytes(),
            "proposed_terminal_run": prepared.run,
            "proposed_terminal_run_head": run_ref(prepared.run),
            "proposed_acceptance_head": prepared.delivery.acceptance,
            "proposed_terminal_manifest": prepared.terminal_manifest,
            "proposed_terminal_manifest_head": manifest_ref(prepared.terminal_manifest),
            "proposed_accepted_delivery_manifest_bytes": (
                prepared.delivery.manifest.canonical_bytes()
            ),
            "ordered_assistant_entries": (conversation_entry,),
        }
    )
    conversation_fingerprint = conversation_source_request_fingerprint(conversation_request)
    member = make_conversation_canonical_member(
        source_kind="COMPLETION",
        source_request_fingerprint=conversation_fingerprint,
        entry=entry,
        delivery_id=delivery.delivery_id,
        recipient_binding_json=conversation_recipient_binding_json(delivery.selection),
        source_heads_json=conversation_source_heads_json(
            source_cut.source_selected_decision,
            source_cut.source_physical_record,
            source_cut.expected_previous_entry,
        ),
    )
    conversation_result = base.assembly.conversation_result.model_copy(
        update={
            "source_request_fingerprint": conversation_fingerprint,
            "ordered_members": (member,),
            "complete_owner_commitment": conversation_complete_owner_commitment((member,)),
        }
    )
    work_source = base.assembly.work_source.model_copy(
        update={
            "original_completion_request_bytes": original.canonical_bytes(),
            "prepared_completion_bytes": prepared.canonical_bytes(),
            "terminal_run": prepared.run,
            "terminal_run_head": run_ref(prepared.run),
            "terminal_manifest": prepared.terminal_manifest,
            "terminal_manifest_head": manifest_ref(prepared.terminal_manifest),
            "ordered_open_obligations": (),
        }
    )
    work_request = base.assembly.terminal_work_request.model_copy(
        update={
            "terminal_run": prepared.run,
            "original_terminalization_request": completion_terminal_work_source_bytes(work_source),
            "terminal_manifest": manifest_ref(prepared.terminal_manifest),
            "ordered_open_obligations": (),
        }
    )
    work_result = base.assembly.terminal_work_result.model_copy(
        update={
            "source_request_fingerprint": hashlib.sha256(
                work_request.canonical_bytes()
            ).hexdigest(),
        }
    )
    # The scoped response supplies the single generic effects exchange exactly.
    exchange = publication.DeliveryEffectsExchangeV1(
        delivery_id=result.delivery_id,
        basis=result.basis,
        precursor_request=result.precursor_request,
        precursor_result=result.precursor_result,
        intent_request=result.intent_request,
        intent_result=result.intent_result,
        acquisition_bytes=result.acquisition_bytes,
        mandate_bytes=result.mandate_bytes,
        retained_sources=result.retained_sources,
    )
    return (
        publication.PrepareCompleteAcceptanceAssemblyV1(
            original_completion_request=original,
            prepared_completion=prepared,
            conversation_request=conversation_request,
            conversation_result=conversation_result,
            ordered_effects=(exchange,),
            work_source=work_source,
            terminal_work_request=work_request,
            terminal_work_result=work_result,
        ),
        call,
        result,
    )


@pytest.mark.asyncio
async def test_scoped_v3_builder_reproduces_the_complete_v2_batch() -> None:
    assembly, call, result = await _scoped_assembly()
    source = await accepted_completion_fixture("v3", "empty")

    batch = publication.build_h1_scoped_v3_complete_acceptance_batch(
        assembly,
        call,
        result,
        identity=source.batch.identity,
        authentication=source.batch.authentication,
        expected=source.batch.expected,
    )

    assert batch.prepared_effects_commands == (
        publication.h1_scoped_v3_acceptance_commands(assembly, call, result)[2],
    )
    effects_command = batch.prepared_effects_commands[0]
    assert effects_command.schema_id == call.schema_id
    assert effects_command.canonical_bytes == call.canonical_bytes()
    assert effects_command.fingerprint == hashlib.sha256(call.canonical_bytes()).hexdigest()
    assert batch.complete_records == publication.expected_h1_scoped_v3_acceptance_records(
        assembly, call, result
    )
    assert publication.validate_h1_scoped_v3_complete_acceptance_batch(
        assembly, call, result, batch
    ) is None


@pytest.mark.asyncio
async def test_scoped_v3_validator_rejects_result_command_source_and_record_mutations() -> None:
    assembly, call, result = await _scoped_assembly()
    source = await accepted_completion_fixture("v3", "empty")
    batch = publication.build_h1_scoped_v3_complete_acceptance_batch(
        assembly,
        call,
        result,
        identity=source.batch.identity,
        authentication=source.batch.authentication,
        expected=source.batch.expected,
    )
    wrong_result = result.model_copy(update={"retained_sources": ()})
    failure = publication.validate_h1_scoped_v3_complete_acceptance_batch(
        assembly, call, wrong_result, batch
    )
    assert failure is not None and failure.code == "EXCHANGE"
    wrong_evidence = result.authority_evidence.model_copy(
        update={"canonical_grant_bytes": b"not-the-pinned-grant"}
    )
    evidence_mutant = result.model_copy(update={"authority_evidence": wrong_evidence})
    failure = publication.validate_h1_scoped_v3_complete_acceptance_batch(
        assembly, call, evidence_mutant, batch
    )
    assert failure is not None and failure.code == "EXCHANGE"
    wrong_command = batch.model_copy(update={"prepared_effects_commands": (batch.loop_command,)})
    failure = publication.validate_h1_scoped_v3_complete_acceptance_batch(
        assembly, call, result, wrong_command
    )
    assert failure is not None and failure.code == "ROLE"
    wrong_sources = assembly.ordered_effects[0].model_copy(update={"retained_sources": ()})
    wrong_assembly = assembly.model_copy(update={"ordered_effects": (wrong_sources,)})
    failure = publication.validate_h1_scoped_v3_complete_acceptance_batch(
        wrong_assembly, call, result, batch
    )
    assert failure is not None and failure.code == "EXCHANGE"
    wrong_work_request = assembly.terminal_work_request.model_copy(
        update={"original_terminalization_request": b"substituted-source"}
    )
    source_mutant = assembly.model_copy(update={"terminal_work_request": wrong_work_request})
    failure = publication.validate_h1_scoped_v3_complete_acceptance_batch(
        source_mutant, call, result, batch
    )
    assert failure is not None and failure.code == "SOURCE"
    wrong_records = batch.model_copy(update={"complete_records": batch.complete_records[:-1]})
    failure = publication.validate_h1_scoped_v3_complete_acceptance_batch(
        assembly, call, result, wrong_records
    )
    assert failure is not None and failure.code == "RECORDS"
