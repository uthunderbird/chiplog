"""Concrete owner transitions and wire routes, before broker publication wiring."""

import hashlib

from chiplog.adapters.driven.loop_prompts import (
    render_delivery_prompt,
    render_prompt,
)
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    EndpointSelection,
    ModelAttempt,
    RunRecord,
    ToolSpec,
    Turn,
    VisibilityManifest,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DELIVERY_TOOLS,
    Commentary,
    DeliveryCompletion,
    DeliveryObservation,
    HistoricalEnvelope,
    ProposedDelivery,
)


def _head(identity: str) -> ExactHead:
    return ExactHead(
        identity=identity, head=identity, fingerprint=hashlib.sha256(identity.encode()).hexdigest()
    )


def _run() -> RunRecord:
    return RunRecord(
        tenant="t",
        principal="p",
        run_id="r",
        state="CREATED",
        head="head",
        predecessor=None,
        prompt="prompt",
        policy=BudgetPolicy(),
        origin=EndpointSelection(
            kind="ORIGIN_EXACT",
            ingress_binding_head="i",
            endpoint_head="e",
            endpoint_id="local",
            provider="hermetic-local",
            recipient="p",
            canonical_address="local://p",
            credential_binding_head="c",
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        event="RunCreated",
    )


async def _captured(
    *,
    legacy: bool = False,
    tools: tuple[ToolSpec, ...] = DELIVERY_TOOLS,
) -> tuple[RunRecord, DeliveryObservation]:
    artifact = await (
        render_prompt("fixture") if legacy else render_delivery_prompt("fixture", tools)
    )
    response = DeliveryCompletion(
        tenant="t",
        run_id="r",
        turn_id="turn",
        deliveries=(ProposedDelivery(payload=(Commentary(text="hello"),)),),
    )
    label = DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())
    member = VisibilityMember(
        record_id="content",
        revision_head="revision",
        content="fixture",
        provenance_head="provenance",
        label_head="label",
        label=label,
        producer="fixture",
        surface="context",
    )
    manifest = VisibilityManifest(
        tenant="t",
        principal="p",
        contour_head="contour",
        run_id="r",
        turn_id="turn",
        generation=0,
        worker_session="worker",
        members=(member,),
        joined_label=label,
        artifact=artifact,
    )
    attempt = ModelAttempt(
        attempt_id="attempt",
        lineage_id="lineage",
        generation=0,
        state="EMITTED_OUTCOME_UNKNOWN",
        head="attempt-head",
        manifest=manifest,
        request="request",
        worker_session="worker",
    )
    turn = Turn(
        turn_id="turn",
        ordinal=1,
        head="turn-head",
        state="PREPARING",
        attempts=(attempt,),
        selector=0,
    )
    emitted = _run().model_copy(update={"state": "ACTIVE", "turns": (turn,)})
    captured = domain.capture(emitted, response.canonical_bytes(), "receipt")
    recipient = ProviderRecipient(
        provider_id="hermetic-local",
        account_id="account",
        recipient_id="p",
        endpoint=_head("local"),
        canonical_address=b"local://p",
        credential_binding=_head("c"),
    )
    digest = manifest.digest()
    observation = DeliveryObservation(
        tenant="t",
        run=ExactHead(identity="r", head=captured.head, fingerprint=captured.digest()),
        turn_id="turn",
        captured_response=response.canonical_bytes(),
        source_frontier=0,
        origin=OriginSelection(ingress_binding=_head("i"), recipient=recipient),
        recipients=(recipient,),
        history=(
            HistoricalEnvelope(
                content=ExactHead(
                    identity=member.record_id,
                    head=member.revision_head,
                    fingerprint=member.digest(),
                ),
                visibility=ExactHead(identity="attempt", head=digest, fingerprint=digest),
                provenance=_head("provenance"),
                disclosure=_head("label"),
                label=label,
                narrowing=(),
            ),
        ),
        queries=(),
        policy=_head("policy"),
        worker_fence=_head("worker"),
    )
    return captured, observation
