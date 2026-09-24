"""Concrete V3 scoped-intent publication inputs shared by codec tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead as LoopHead,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ModelSelection as LoopModelSelection,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ProviderRecipient as LoopRecipient,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
    prepare_completion,
)
from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    DeliverySendBinding,
    ExactHead,
    ModelSelection,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.fences import Absent
from chiplog.capabilities.effects.scoped_intent_contracts import (
    DispatchMandateV3,
    ExternalActionIntentV3,
    PreparedDeliveryAuthority,
    PreparedDeliveryBasisV3,
    PreparedDeliveryOriginV3,
    PreparedScopedIntentPublication,
    PrepareScopedIntentPublication,
    ScopedAuthorityRecord,
    ScopedDispatchAcquisition,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    ScopedIntentRetainedExchangeV3,
    make_scoped_intent_member,
    make_scoped_intent_retained_exchange,
    scoped_intent_complete_owner_commitment,
    scoped_intent_fingerprint,
)
from tests.support.acceptance_v2 import prepared_acceptance
from tests.support.delivery_completion import _captured


def head(name: str) -> ExactHead:
    return reference(name, name.encode())


def source(name: str) -> ScopedAuthorityRecord:
    return ScopedAuthorityRecord(
        owner="effects",
        head=head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=name.encode(),
        selected_decision=head("selected:" + name),
    )


def _effect_head(value: LoopHead) -> ExactHead:
    return ExactHead(subject_id=value.identity, head=value.head, fingerprint=value.fingerprint)


def _effect_recipient(value: LoopRecipient) -> ProviderRecipient:
    return ProviderRecipient(
        provider=value.provider_id,
        account=value.account_id,
        recipient=value.recipient_id,
        endpoint=_effect_head(value.endpoint),
        canonical_address=value.canonical_address,
        credential_binding=_effect_head(value.credential_binding),
    )


@dataclass(frozen=True)
class ScopedIntentPublicationFixture:
    request: PrepareScopedIntentPublication
    result: PreparedScopedIntentPublication
    retained: ScopedIntentRetainedExchangeV3
    intent: ExternalActionIntentV3


async def delivery_publication_fixture(
    delivery_index: int = 1,
) -> ScopedIntentPublicationFixture:
    """Build an actual two-delivery proposal and bind either V3 delivery intent."""
    _, observed = await _captured()
    first = observed.origin.recipient
    second = LoopRecipient(
        provider_id="hermetic-second",
        account_id="account-second",
        recipient_id="recipient-second",
        endpoint=LoopHead(
            identity="second-endpoint",
            head="second-endpoint-head",
            fingerprint=hashlib.sha256(b"second-endpoint").hexdigest(),
        ),
        canonical_address=b"local://second",
        credential_binding=LoopHead(
            identity="second-credential",
            head="second-credential-head",
            fingerprint=hashlib.sha256(b"second-credential").hexdigest(),
        ),
    )
    completion = DeliveryCompletion(
        tenant=observed.tenant,
        run_id=observed.run.identity,
        turn_id=observed.turn_id,
        deliveries=(
            ProposedDelivery(payload=(Commentary(text="first"),)),
            ProposedDelivery(
                selection=LoopModelSelection(recipient=second),
                payload=(Commentary(text="second"),),
            ),
        ),
    )
    observation = observed.model_copy(
        update={"captured_response": completion.canonical_bytes(), "recipients": (first, second)}
    )
    proposal = prepare_completion(completion, observation)
    delivery = proposal.manifest.ordered_deliveries[delivery_index]
    selection: OriginSelection | ModelSelection
    if delivery.selection.kind == "ORIGIN_EXACT":
        selection = OriginSelection(
            kind="ORIGIN_EXACT",
            ingress_binding=_effect_head(delivery.selection.ingress_binding),
            recipient=_effect_recipient(delivery.selection.recipient),
        )
    else:
        selection = ModelSelection(
            kind="MODEL_SELECTED_EXACT", recipient=_effect_recipient(delivery.selection.recipient)
        )
    binding = DeliverySendBinding(
        delivery_id=delivery.delivery_id,
        acceptance=_effect_head(delivery.acceptance),
        render_digest=delivery.render_digest,
        manifest_digest=delivery.manifest_digest,
        selection=selection,
        visibility=tuple(_effect_head(item) for item in delivery.visibility),
        provenance=tuple(_effect_head(item) for item in delivery.provenance),
        disclosure=tuple(_effect_head(item) for item in delivery.disclosure),
        narrowing=tuple(_effect_head(item) for item in delivery.narrowing),
        policy=_effect_head(delivery.policy),
    )
    basis = PreparedDeliveryBasisV3(
        source_cut=head("completion-cut"),
        acceptance=_effect_head(proposal.acceptance),
        completion_command_bytes=completion.canonical_bytes(),
        delivery_observation_bytes=observation.canonical_bytes(),
        loop_proposal_bytes=proposal.canonical_bytes(),
    )
    origin = PreparedDeliveryOriginV3(
        original_run=head("captured-run"),
        captured_attempt=head("captured-attempt"),
        binding=binding,
        preparation_basis=head("basis"),
    )
    baseline = prepared_acceptance().effects_proposal.snapshot.intent.mandate.model_dump()
    baseline.update(
        {
            "schema_id": "chiplog.effects.dispatch-mandate.v3",
            "mandate_id": "delivery-mandate-v3",
            "origin": origin,
            "recipient": _effect_recipient(delivery.selection.recipient),
            "payload": delivery.rendered_bytes,
            "effect_fingerprint": hashlib.sha256(delivery.rendered_bytes).hexdigest(),
        }
    )
    mandate = DispatchMandateV3.model_validate(baseline)
    precursor_request = ScopedPrecursorRequest(
        request_id="delivery-precursor",
        mandate=mandate,
        interpretation_policy=source("policy"),
        preexisting_sources=(source("authority"),),
    )
    precursor_result = ScopedPrecursorResult(
        source_request_fingerprint=hashlib.sha256(precursor_request.canonical_bytes()).hexdigest(),
        mandate_fingerprint=hashlib.sha256(mandate.canonical_bytes()).hexdigest(),
        interpretation_policy=precursor_request.interpretation_policy.head,
        complete_evaluation_evidence=(head("evaluation"),),
    )
    acquisition = ScopedDispatchAcquisition(
        authority=PreparedDeliveryAuthority(
            basis=basis,
            preexisting_communication_authority=source("communication"),
            current_disclosure_authority=source("disclosure"),
            exact_mandate_bytes=mandate.canonical_bytes(),
        ),
        precursor_request=precursor_request,
        precursor_result=precursor_result,
        original_sources=prepared_acceptance().effects_proposal.snapshot.intent.acquisition.original_sources,
    )
    unsigned = ExternalActionIntentV3(
        intent_id=delivery.delivery_id + "/intent",
        fingerprint="0" * 64,
        mandate=mandate,
        acquisition=acquisition,
    )
    intent = unsigned.model_copy(update={"fingerprint": scoped_intent_fingerprint(unsigned)})
    request = PrepareScopedIntentPublication(
        identity=CommandIdentity(
            command_id="publish-delivery-intent", fingerprint="a" * 64, expected_tenant_head=7
        ),
        intent=intent,
        expected_intent=Absent(),
        current=prepared_acceptance().effects_request.current,
        complete_current_origin_sources=(source("current-origin"),),
        fence=prepared_acceptance().effects_request.command.fence,
    )
    retained = make_scoped_intent_retained_exchange(request)
    member = make_scoped_intent_member(intent)
    result = PreparedScopedIntentPublication(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        intent=intent,
        original_references=retained.original_references,
        complete_owner_commitment=scoped_intent_complete_owner_commitment(
            member, retained.original_sources
        ),
    )
    return ScopedIntentPublicationFixture(
        request=request, result=result, retained=retained, intent=intent
    )
