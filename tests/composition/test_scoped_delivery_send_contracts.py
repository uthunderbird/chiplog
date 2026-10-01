"""Consumer checks for the isolated path-scoped V3 SEND result wire."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.capabilities.effects.contracts import ExactHead, TransmissionAttempt
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.fences import Absent, Present
from chiplog.capabilities.effects.lifecycle_transition_contracts import (
    EffectsObligationRevision,
    EffectsReductionConsumable,
    EffectsSemanticReductionRecord,
)
from chiplog.capabilities.effects.scoped_delivery_source_contracts import scoped_first_send_head
from chiplog.capabilities.effects.scoped_dispatch_contracts import (
    AllChildOutcomeParent,
    ScopedFirstSendDecision,
    ScopedFirstSendParent,
)
from chiplog.composition.r16_effects_inputs import canonical
from chiplog.composition.scoped_delivery_atomic_outcome_contracts import (
    ScopedDeliveryAtomicConfirmedV3,
    ScopedDeliveryAtomicUnknownV3,
    SelectedAllChildOutcomeParentSource,
    SelectedEffectsObligationRevisionSource,
    SelectedEffectsSemanticReductionSource,
)
from chiplog.composition.scoped_delivery_send_contracts import (
    SCOPED_DELIVERY_SEND_RESULT_V3,
    SCOPED_V3_REGISTRY,
    ScopedDeliveryIntentSourceV3,
    ScopedDeliverySendCommittedV3,
    ScopedDeliverySendReceiptV3,
    ScopedDeliverySendRejectedV3,
    ScopedDeliverySendReplayedV3,
    ScopedDeliverySendRequestV3,
    ScopedDeliverySendUncertainV3,
    ScopedFirstSendDecisionSourceV3,
    ScopedV3BrokerConsumptionRecord,
    ScopedV3BrokerConsumptionSource,
    ScopedV3ConsumeRequest,
    SelectedScopedFirstSendParentV3,
    SelectedScopedTransmissionAttemptV3,
)
from tests.support.scoped_intent_records import delivery_publication_fixture
from tests.support.scoped_intent_records import head as fixture_head


def _consume_id(tenant: str, transmission: ExactHead) -> str:
    raw = json.dumps(
        [tenant, transmission.model_dump(mode="json")], sort_keys=True, separators=(",", ":")
    ).encode()
    return "consume-send/" + hashlib.sha256(raw).hexdigest()


async def _receipt() -> ScopedDeliverySendReceiptV3:
    fixture = await delivery_publication_fixture()
    intent_raw = fixture.intent.canonical_bytes()
    intent_head = ExactHead(
        subject_id=fixture.intent.intent_id,
        head=fixture.intent.intent_id,
        fingerprint=hashlib.sha256(intent_raw).hexdigest(),
    )
    selected_send = fixture_head("selected-send")
    intent = ScopedDeliveryIntentSourceV3(
        subject=intent_head,
        canonical_record_bytes=intent_raw,
        selected_decision=fixture_head("intent-selected"),
        physical_record=intent_head,
    )
    decision_record = ScopedFirstSendDecision(
        identity=fixture.request.identity,
        source_request_fingerprint="a" * 64,
        original_intent=intent_head,
        authorization=fixture_head("authorization"),
        prior_parent=fixture_head("parent"),
        current_inputs_fingerprint="b" * 64,
        complete_current_origin_sources=fixture.request.complete_current_origin_sources,
        fence=fixture.request.fence,
    )
    decision_head = scoped_first_send_head(decision_record)
    decision = ScopedFirstSendDecisionSourceV3(
        subject=decision_head,
        canonical_record_bytes=decision_record.canonical_bytes(),
        selected_decision=selected_send,
        physical_record=decision_head,
    )
    transmission = fixture_head("transmission")
    mandate = fixture.intent.mandate
    child_record = TransmissionAttempt(
        transmission=transmission,
        intent=intent_head,
        ordinal=0,
        semantics=mandate.semantics,
        dispatch_time_ns=1,
        payload_fingerprint=mandate.effect_fingerprint,
        recipient=mandate.recipient,
        idempotency_fence_key=mandate.idempotency_fence_key,
        send_commit=decision_head,
        coverage_proof=None,
    )
    child_raw = child_record.canonical_bytes()
    child_head = reference("child-row", child_raw)
    child = SelectedScopedTransmissionAttemptV3(
        subject=child_head,
        canonical_record_bytes=child_raw,
        selected_decision=selected_send,
        physical_record=child_head,
        record=child_record,
    )
    parent_record = ScopedFirstSendParent(
        original_intent=intent_head,
        predecessor=fixture_head("parent"),
        send_decision=decision_head,
        first_child=child_head,
    )
    parent_raw = parent_record.canonical_bytes()
    parent_head = reference("first-send-parent", parent_raw)
    parent = SelectedScopedFirstSendParentV3(
        subject=parent_head,
        canonical_record_bytes=parent_raw,
        selected_decision=selected_send,
        physical_record=parent_head,
        record=parent_record,
    )
    command_id = _consume_id(mandate.tenant_id, transmission)
    ticket = IssuedEffectSendTicket(
        tenant_id=mandate.tenant_id,
        issued_operation_id=command_id,
        journal_decision_id=selected_send.subject_id,
        journal_decision_fingerprint=selected_send.fingerprint,
        transmission_id=transmission.subject_id,
        transmission_fingerprint=transmission.fingerprint,
        request_ordinal=0,
        intent_id=fixture.intent.intent_id,
        intent_fingerprint=fixture.intent.fingerprint,
        provider=mandate.recipient.provider,
        account=mandate.recipient.account,
        recipient=mandate.recipient.recipient,
        canonical_address=mandate.recipient.canonical_address,
        endpoint_head=mandate.recipient.endpoint.head,
        credential_binding_head=mandate.recipient.credential_binding.head,
        adapter_contract_version=mandate.semantics.adapter_contract_version,
        payload=mandate.payload,
        payload_fingerprint=mandate.effect_fingerprint,
        idempotency_key=mandate.idempotency_fence_key,
        bundle_members=tuple(x.subject_id for x in mandate.bundle_members),
    )
    ticket_bytes = canonical(asdict(ticket))
    consume = ScopedV3ConsumeRequest(
        tenant_id=mandate.tenant_id,
        command_id=command_id,
        selected_send=selected_send,
        send_record=decision_head,
        intent=intent_head,
        transmission=transmission,
        ticket_bytes=ticket_bytes,
        ticket_digest=hashlib.sha256(ticket_bytes).hexdigest(),
        registry=SCOPED_V3_REGISTRY,
    )
    record = ScopedV3BrokerConsumptionRecord(request=consume)
    raw = record.canonical_bytes()
    record_head = reference(command_id, raw)
    consumption = ScopedV3BrokerConsumptionSource(
        subject=record_head,
        canonical_record_bytes=raw,
        selected_decision=fixture_head("broker-consumption-selected"),
        physical_record=record_head,
        record=record,
    )
    return ScopedDeliverySendReceiptV3(
        request=ScopedDeliverySendRequestV3(
            peer="peer",
            intent_id=fixture.intent.intent_id,
            act_id=fixture.request.identity.command_id,
            worker_run_id="run",
        ),
        selected_intent=intent,
        first_send_decision=decision,
        first_send_child=child,
        first_send_parent=parent,
        consumption=consumption,
        outcome=ScopedDeliveryAtomicUnknownV3(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["COMMITTED", "REPLAYED"])
async def test_round_trip_success_results_use_atomic_unknown_outcome(kind: str) -> None:
    receipt = await _receipt()
    value = (
        ScopedDeliverySendCommittedV3(receipt=receipt)
        if kind == "COMMITTED"
        else ScopedDeliverySendReplayedV3(receipt=receipt)
    )
    assert SCOPED_DELIVERY_SEND_RESULT_V3.validate_json(value.canonical_bytes()) == value


@pytest.mark.asyncio
async def test_rejects_real_child_or_broker_request_substitution() -> None:
    receipt = await _receipt()
    child = receipt.first_send_child.record
    altered_child = child.model_copy(update={"ordinal": 1})
    raw = altered_child.canonical_bytes()
    head = reference("child-row-1", raw)
    with pytest.raises(ValidationError):
        SelectedScopedTransmissionAttemptV3(
            subject=head,
            canonical_record_bytes=raw,
            selected_decision=receipt.consumption.record.request.selected_send,
            physical_record=head,
            record=altered_child,
        )

    wire = receipt.model_dump(mode="json")
    wire["consumption"]["record"]["request"]["registry"] = fixture_head(
        "wrong-registry"
    ).model_dump(mode="json")
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(wire)

    altered_parent = receipt.first_send_parent.record.model_copy(
        update={"first_child": fixture_head("other-child")}
    )
    parent_raw = altered_parent.canonical_bytes()
    parent_head = reference("changed-parent", parent_raw)
    altered_source = SelectedScopedFirstSendParentV3(
        subject=parent_head,
        canonical_record_bytes=parent_raw,
        selected_decision=receipt.first_send_parent.selected_decision,
        physical_record=parent_head,
        record=altered_parent,
    )
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"first_send_parent": altered_source}).model_dump()
        )

    changed = receipt.consumption.record.request.model_copy(
        update={"selected_send": fixture_head("wrong-effects-selected")}
    )
    changed_record = ScopedV3BrokerConsumptionRecord(request=changed)
    changed_raw = changed_record.canonical_bytes()
    changed_head = reference(changed.command_id, changed_raw)
    changed_source = ScopedV3BrokerConsumptionSource(
        subject=changed_head,
        canonical_record_bytes=changed_raw,
        selected_decision=receipt.consumption.selected_decision,
        physical_record=changed_head,
        record=changed_record,
    )
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"consumption": changed_source}).model_dump()
        )


@pytest.mark.asyncio
async def test_rejects_ticket_effect_mutation_even_with_valid_ticket_bytes_and_hash() -> None:
    receipt = await _receipt()
    request = receipt.consumption.record.request
    ticket = json.loads(request.ticket_bytes)
    ticket["recipient"] = "substituted"
    raw = json.dumps(ticket, sort_keys=True, separators=(",", ":")).encode()
    altered_request = request.model_copy(
        update={"ticket_bytes": raw, "ticket_digest": hashlib.sha256(raw).hexdigest()}
    )
    record = ScopedV3BrokerConsumptionRecord(request=altered_request)
    record_raw = record.canonical_bytes()
    head = reference(altered_request.command_id, record_raw)
    source = ScopedV3BrokerConsumptionSource(
        subject=head,
        canonical_record_bytes=record_raw,
        selected_decision=altered_request.selected_send,
        physical_record=head,
        record=record,
    )
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"consumption": source}).model_dump()
        )


@pytest.mark.asyncio
async def test_rejects_parent_predecessor_after_rehash() -> None:
    receipt = await _receipt()
    altered = receipt.first_send_parent.record.model_copy(
        update={"predecessor": fixture_head("wrong-parent")}
    )
    raw = altered.canonical_bytes()
    head = reference("wrong-parent-row", raw)
    source = SelectedScopedFirstSendParentV3(
        subject=head,
        canonical_record_bytes=raw,
        selected_decision=receipt.first_send_parent.selected_decision,
        physical_record=head,
        record=altered,
    )
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"first_send_parent": source}).model_dump()
        )


@pytest.mark.asyncio
async def test_rejects_cross_tenant_consumption_after_rehash() -> None:
    receipt = await _receipt()
    old = receipt.consumption.record.request
    tenant = "another-tenant"
    command_id = _consume_id(tenant, old.transmission)
    ticket = json.loads(old.ticket_bytes)
    ticket["tenant_id"] = tenant
    ticket["issued_operation_id"] = command_id
    ticket_bytes = json.dumps(ticket, sort_keys=True, separators=(",", ":")).encode()
    consume = ScopedV3ConsumeRequest(
        **{
            **old.model_dump(),
            "tenant_id": tenant,
            "command_id": command_id,
            "ticket_bytes": ticket_bytes,
            "ticket_digest": hashlib.sha256(ticket_bytes).hexdigest(),
        }
    )
    record = ScopedV3BrokerConsumptionRecord(request=consume)
    raw = record.canonical_bytes()
    head = reference(command_id, raw)
    source = ScopedV3BrokerConsumptionSource(
        subject=head,
        canonical_record_bytes=raw,
        selected_decision=receipt.consumption.selected_decision,
        physical_record=head,
        record=record,
    )
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"consumption": source}).model_dump()
        )


@pytest.mark.asyncio
async def test_unknown_rejects_foreign_terminal_evidence_after_rehash() -> None:
    receipt = await _receipt()
    parent = AllChildOutcomeParent(
        original_intent=fixture_head("foreign-intent"),
        predecessor=receipt.first_send_parent.subject,
        state="CONFIRMED",
        complete_ordered_children=(receipt.first_send_child.record.transmission,),
        complete_ordered_evidence=(fixture_head("evidence"),),
        original_obligation=Absent(),
    )
    raw = parent.canonical_bytes()
    head = reference("foreign-outcome-parent", raw)
    source = SelectedAllChildOutcomeParentSource(
        subject=head,
        canonical_record_bytes=raw,
        selected_decision=fixture_head("foreign-outcome-selection"),
        physical_record=head,
        record=parent,
    )
    outcome = ScopedDeliveryAtomicUnknownV3(parent=source)
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"outcome": outcome}).model_dump()
        )


def _terminal_outcome(
    receipt: ScopedDeliverySendReceiptV3, children: tuple[ExactHead, ...]
) -> ScopedDeliveryAtomicConfirmedV3:
    decision = fixture_head("terminal-selection")
    stable_obligation = fixture_head("stable-obligation")
    evidence = (fixture_head("provider-evidence"),)
    parent = AllChildOutcomeParent(
        original_intent=receipt.selected_intent.subject,
        predecessor=receipt.first_send_parent.subject,
        state="CONFIRMED",
        complete_ordered_children=children,
        complete_ordered_evidence=evidence,
        original_obligation=stable_obligation,
    )
    parent_raw = parent.canonical_bytes()
    parent_head = reference("terminal-parent", parent_raw)
    parent_source = SelectedAllChildOutcomeParentSource(
        subject=parent_head,
        canonical_record_bytes=parent_raw,
        selected_decision=decision,
        physical_record=parent_head,
        record=parent,
    )
    obligation = EffectsObligationRevision(
        obligation=stable_obligation,
        predecessor=Absent(),
        original_intent=receipt.selected_intent.subject,
        original_send=receipt.first_send_decision.subject,
        complete_ordered_children=children,
        state="CLOSED",
        closure_predicate=fixture_head("closure-predicate"),
        resolver_binding=fixture_head("resolver"),
        closure_evidence=evidence,
    )
    obligation_raw = obligation.canonical_bytes()
    obligation_head = reference("closed-obligation", obligation_raw)
    obligation_source = SelectedEffectsObligationRevisionSource(
        subject=obligation_head,
        canonical_record_bytes=obligation_raw,
        selected_decision=decision,
        physical_record=obligation_head,
        record=obligation,
    )
    reduction = EffectsSemanticReductionRecord(
        stream_id="delivery-stream",
        reduction_id="initial-closure",
        original_intent=receipt.selected_intent.subject,
        predecessor=Absent(),
        reducer=fixture_head("registered-reducer"),
        complete_ordered_children=children,
        complete_ordered_evidence=evidence,
        current_parent=parent_head,
        original_obligation=stable_obligation,
        original_terminal_closure=Present(
            head=obligation_head.head, fingerprint=obligation_head.fingerprint
        ),
        disposition=EffectsReductionConsumable(semantic_class="delivery-terminal"),
    )
    reduction_raw = reduction.canonical_bytes()
    reduction_head = reference("terminal-reduction", reduction_raw)
    reduction_source = SelectedEffectsSemanticReductionSource(
        subject=reduction_head,
        canonical_record_bytes=reduction_raw,
        selected_decision=decision,
        physical_record=reduction_head,
        record=reduction,
    )
    return ScopedDeliveryAtomicConfirmedV3(
        original_intent=receipt.selected_intent.subject,
        original_send=receipt.first_send_decision.subject,
        parent=parent_source,
        obligation=obligation_source,
        reduction=reduction_source,
    )


@pytest.mark.asyncio
async def test_terminal_receipt_requires_its_original_first_transmission() -> None:
    receipt = await _receipt()
    actual = receipt.first_send_child.record.transmission
    valid = _terminal_outcome(receipt, (actual,))
    assert ScopedDeliverySendReceiptV3.model_validate(
        receipt.model_copy(update={"outcome": valid}).model_dump()
    ).outcome.kind == "CONFIRMED"

    other = _terminal_outcome(receipt, (fixture_head("different-child"),))
    with pytest.raises(ValidationError):
        ScopedDeliverySendReceiptV3.model_validate(
            receipt.model_copy(update={"outcome": other}).model_dump()
        )


def test_four_locators_and_closed_failure_results() -> None:
    for field in ("peer", "intent_id", "act_id", "worker_run_id"):
        values: dict[str, object] = {
            "peer": "peer",
            "intent_id": "intent",
            "act_id": "act",
            "worker_run_id": "run",
        }
        values[field] = ""
        with pytest.raises(ValidationError):
            ScopedDeliverySendRequestV3.model_validate(values)
    with pytest.raises(ValidationError):
        SCOPED_DELIVERY_SEND_RESULT_V3.validate_python(
            {"kind": "REJECTED", "intent_id": "i", "act_id": "a", "reason": "freeform"}
        )
    assert (
        ScopedDeliverySendRejectedV3(intent_id="i", act_id="a", reason="DENIED").kind == "REJECTED"
    )
    assert (
        ScopedDeliverySendUncertainV3(
            intent_id="i", act_id="a", reason="CONFLICT", stage="REPLY"
        ).kind
        == "UNCERTAIN"
    )
