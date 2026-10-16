"""Current-authorized native Complete acceptance and durable self delivery."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, TypedDict

from chiplog.adapters.driven.effects_hermetic import (
    HermeticResponseLost,
    IssuedEffectSendTicket,
    verify_hermetic_receipt,
)
from chiplog.capabilities.agent_loop.completion_client_contracts import (
    NativeCompletionAcceptanceV1,
    NativeCompletionDTO,
    NativeDeliveryClaimV1,
    NativeDeliveryIntentV1,
    NativeDeliveryReceiptV1,
    NativeDeliverySendV1,
)
from chiplog.capabilities.agent_loop.contracts import (
    DeliveryAcceptanceReference,
    LoopHold,
    LoopRejected,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    ProviderRecipient,
    delivery_manifest_head,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryCompletion,
    DeliveryObservation,
    HistoricalEnvelope,
    ProviderQuery,
    prepare_completion,
)
from chiplog.capabilities.agent_loop.execution_client_contracts import ConversationEntryV1
from chiplog.capabilities.agent_loop.execution_client_transitions import (
    ExecutionRun,
    advance,
    headed,
)
from chiplog.capabilities.effects.client_effect_contracts import (
    NativeEffectClosureV1,
    NativeEffectEvidenceV1,
    NativeEffectIntentV1,
    NativeEffectReductionV1,
)
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
    current_effect_result,
    decode_ticket,
    digest,
)
from chiplog.composition.postgres_effects import (
    rows as effect_rows,
)
from chiplog.platform.deployment_gate import DeploymentGateRequest
from chiplog.platform.postgres import PostgresIntegrityError, Record, Snapshot
from chiplog.platform.postgres_gate import PostgresDeploymentGate, canonical

if TYPE_CHECKING:
    from chiplog.composition.postgres_execution import PostgresExecutionRuntime


def ref(identity: str, head: str, raw: bytes) -> ExactHead:
    return ExactHead(identity=identity, head=head, fingerprint=digest(raw))


def recipient(
    runtime: PostgresExecutionRuntime, binding: PostgresEffectsBinding
) -> ProviderRecipient:
    target = binding.target(runtime)
    return ProviderRecipient(
        provider_id=target.provider,
        account_id=target.account,
        recipient_id=target.recipient,
        canonical_address=target.canonical_address,
        endpoint=ref(target.endpoint.subject_id, target.endpoint.head, b"hermetic-effects.v1"),
        credential_binding=ExactHead(
            identity=target.credential_binding.subject_id,
            head=target.credential_binding.head,
            fingerprint=target.credential_binding.fingerprint,
        ),
    )


def binding(runtime: PostgresExecutionRuntime) -> PostgresEffectsBinding:
    if runtime.effects_binding is None:
        raise LoopHold("registered durable delivery transport unavailable")
    return runtime.effects_binding


class Common(TypedDict):
    head: str
    tenant_id: str
    principal_id: str


def common(runtime: PostgresExecutionRuntime) -> Common:
    return dict(head="pending", tenant_id=runtime.tenant_id, principal_id=runtime.principal_id)


def record(value: NativeCompletionDTO) -> Record:
    owner = "agent_loop" if isinstance(value, NativeCompletionAcceptanceV1) else "effects"
    return Record(value.head, owner, value.schema_id, value.canonical_bytes())


def rows[T: NativeCompletionDTO](
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, kind: type[T]
) -> tuple[T, ...]:
    result = []
    for batch in snapshot.batches:
        for item in batch.records:
            if item.schema_id != kind.model_fields["schema_id"].default:
                continue
            try:
                value = kind.model_validate_json(item.canonical_bytes)
                if (
                    record(value) != item
                    or headed(value, "completion:").head != value.head
                    or value.tenant_id != runtime.tenant_id
                    or value.principal_id != runtime.principal_id
                ):
                    raise ValueError("native completion record identity differs")
            except ValueError as error:
                raise PostgresIntegrityError(
                    f"operation=completion.read tenant={runtime.tenant_id} "
                    f"record_id={item.record_id}"
                ) from error
            result.append(value)
    return tuple(result)


def provider_queries(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, run: ExecutionRun
) -> tuple[ProviderQuery, ...]:
    """Actual native effect authorities at a stable domain-source frontier."""
    if runtime.effects_binding is None:
        return ()
    selected = {
        reference.subject_id for turn in run.turns for reference in (turn.initialized_calls or ())
    }
    result = []
    selected_reduction_heads = set()
    for call_id in sorted(selected):
        intents = effect_rows(runtime, snapshot, NativeEffectIntentV1, call_id)
        if not intents:
            continue
        output = json.loads(
            current_effect_result(runtime, runtime.effects_binding, snapshot, call_id)
        )
        closure = effect_rows(runtime, snapshot, NativeEffectClosureV1, call_id)[0]
        reduction = effect_rows(runtime, snapshot, NativeEffectReductionV1, call_id)[-1]
        selected_reduction_heads.add(reduction.head)
        intent = intents[0]
        accepted_rows = [
            item
            for batch in snapshot.batches
            for item in batch.records
            if item.record_id == intent.accepted_head
            and item.schema_id == "chiplog.agent-loop.native-call-state.v1"
        ]
        if len(accepted_rows) != 1:
            raise LoopHold("provider query original accepted call state absent")
        receipts = effect_rows(runtime, snapshot, NativeEffectEvidenceV1, call_id)
        receipt_refs = tuple(
            ref(value.head, value.head, value.canonical_bytes()) for value in receipts
        )
        evidence = (
            ref(closure.obligation_id, closure.head, closure.canonical_bytes()),
            ref(call_id + "/current", reduction.head, reduction.canonical_bytes()),
            *receipt_refs,
        )
        result.append(
            ProviderQuery(
                tenant=runtime.tenant_id,
                subject=ref(intent.intent_id, intent.head, intent.canonical_bytes()),
                correlation=ref(call_id, intent.accepted_head, accepted_rows[0].canonical_bytes),
                query=ref(call_id + "/provider-query", reduction.head, reduction.canonical_bytes()),
                records=evidence,
                source_frontier=0,
                evidence_kind="AUTHENTICATED_RECEIPT",
                evidence=receipt_refs[0],
                outcome="CONFIRMED_FAILURE"
                if output["effect_outcome"] == "FAILED_NO_EFFECT"
                else "CONFIRMED_SUCCESS"
                if output["effect_outcome"] == "CONFIRMED"
                else "UNKNOWN",
            )
        )
    frontier = max(
        (
            batch.sequence
            for batch in snapshot.batches
            if any(item.record_id in selected_reduction_heads for item in batch.records)
        ),
        default=0,
    )
    return tuple(query.model_copy(update={"source_frontier": frontier}) for query in result)


def observation(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, source: ExecutionRun, worker: ExactHead
) -> DeliveryObservation:
    transport = binding(runtime)
    history = []
    seen = set()
    for index in range(len(source.turns)):
        for attempt in source.turns[index].attempts:
            for member in attempt.manifest.members:
                envelope = HistoricalEnvelope(
                    content=ref(member.record_id, member.revision_head, member.content.encode()),
                    visibility=ref(
                        member.record_id + "/visibility",
                        member.label_head,
                        member.label.canonical_bytes(),
                    ),
                    provenance=ref(
                        member.record_id + "/provenance",
                        member.provenance_head,
                        member.canonical_bytes(),
                    ),
                    disclosure=ref(
                        member.label_head, member.label_head, member.label.canonical_bytes()
                    ),
                    label=member.label,
                    narrowing=(),
                )
                key = (envelope.content, envelope.visibility)
                if key not in seen:
                    seen.add(key)
                    history.append(envelope)
    selected = source.turns[-1]
    attempt = selected.attempts[selected.selector]
    if attempt.response_base64 is None:
        raise LoopRejected("Complete has no exact captured response")
    queries = provider_queries(runtime, snapshot, source)
    frontier = max((value.source_frontier for value in queries), default=0)
    return DeliveryObservation(
        tenant=runtime.tenant_id,
        run=ref(source.run_id, source.head, source.canonical_bytes()),
        turn_id=selected.turn_id,
        captured_response=base64.b64decode(attempt.response_base64, validate=True),
        source_frontier=frontier,
        origin=source.origin,
        recipients=(recipient(runtime, transport),),
        history=tuple(history),
        queries=queries,
        policy=ref(source.policy_head, source.policy_head, source.policy.canonical_bytes()),
        worker_fence=worker,
    )


def semantic_observation(observed: DeliveryObservation) -> DeliveryObservation:
    """Canonical typed interpretation; the original raw bytes stay in source Run.

    The legacy semantic preparer requires canonical JSON. Native models may emit
    equivalent JSON whitespace. Publication separately binds the exact selected
    source Run, selector, captured bytes and immutable visibility manifest.
    """
    parsed = DeliveryCompletion.model_validate_json(observed.captured_response)
    return observed.model_copy(update={"captured_response": parsed.canonical_bytes()})


def source_run(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, accepted: NativeCompletionAcceptanceV1
) -> ExecutionRun:
    matches = [
        run
        for run in runtime._runs(snapshot)
        if run.run_id == accepted.run_id and run.head == accepted.source_run_head
    ]
    if len(matches) != 1:
        raise LoopRejected("accepted immutable completion source absent")
    return matches[0]


def revalidate(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, accepted: NativeCompletionAcceptanceV1
) -> None:
    source = source_run(runtime, snapshot, accepted)
    runtime.assert_all_selected_seals_closed(snapshot, source)
    current = observation(runtime, snapshot, source, accepted.worker_at_acceptance)
    response = DeliveryCompletion.model_validate_json(current.captured_response)
    if prepare_completion(response, semantic_observation(current)) != accepted.proposal:
        raise LoopHold("accepted completion current assertion/recipient/disclosure cut changed")


async def accept_completion(
    runtime: PostgresExecutionRuntime, peer: str, run_id: str, expected_head: str
) -> NativeCompletionAcceptanceV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        existing = [
            value
            for value in rows(runtime, snapshot, NativeCompletionAcceptanceV1)
            if value.run_id == run_id
        ]
        if existing:
            if existing[0].source_run_head != expected_head:
                raise LoopRejected("completion acceptance source changed")
            return existing[0]
        latest = [run for run in runtime._runs(snapshot) if run.run_id == run_id]
        if (
            not latest
            or latest[-1].head != expected_head
            or latest[-1].event != "ModelCompletionPrepared"
            or latest[-1].state != "ACTIVE"
        ):
            raise LoopRejected("acceptance requires exact prepared Complete Run")
        source = latest[-1]
        runtime.assert_all_selected_seals_closed(snapshot, source)
        worker = ref(
            runtime._worker, "worker:" + digest(runtime._token.encode()), runtime._token.encode()
        )
        observed = observation(runtime, snapshot, source, worker)
        completion = DeliveryCompletion.model_validate_json(observed.captured_response)
        proposal = prepare_completion(completion, semantic_observation(observed))
        accepted = headed(
            NativeCompletionAcceptanceV1(
                **common(runtime),
                run_id=run_id,
                source_run_head=source.head,
                worker_at_acceptance=worker,
                proposal=proposal,
            ),
            "completion:",
        )
        intents = tuple(
            headed(
                NativeDeliveryIntentV1(
                    **common(runtime),
                    delivery_id=delivery.delivery_id,
                    acceptance_head=accepted.head,
                    delivery=delivery,
                    receipt_key_fingerprint=digest(binding(runtime).receipt_key),
                ),
                "completion:",
            )
            for delivery in proposal.manifest.ordered_deliveries
        )
        turn = source.turns[-1]
        attempt = turn.attempts[turn.selector]
        sealed_attempt = headed(
            attempt.model_copy(update={"state": "TERMINAL_ACCEPTED"}), "execution-attempt:"
        )
        sealed_turn = headed(
            turn.model_copy(
                update={
                    "state": "ACCEPTED",
                    "attempts": (
                        *turn.attempts[: turn.selector],
                        sealed_attempt,
                        *turn.attempts[turn.selector + 1 :],
                    ),
                }
            ),
            "execution-turn:",
        )
        complete = advance(
            source,
            "CompleteAcceptance",
            state="SUCCEEDED",
            turns=(*source.turns[:-1], sealed_turn),
            delivery_acceptance=DeliveryAcceptanceReference(
                acceptance_identity=proposal.acceptance.identity,
                acceptance_head=proposal.acceptance.head,
                acceptance_fingerprint=proposal.acceptance.fingerprint,
                proposal_canonical_base64=base64.b64encode(proposal.canonical_bytes()).decode(),
            ),
        )
        manifest_head = delivery_manifest_head(proposal.manifest)
        records = [
            record(accepted),
            Record(
                manifest_head.head,
                "agent_loop",
                "chiplog.delivery-manifest.v1",
                proposal.manifest.canonical_bytes(),
            ),
            Record(complete.head, "agent_loop", complete.schema_id, complete.canonical_bytes()),
            *(record(intent) for intent in intents),
        ]
        ordinal = len(runtime._history(snapshot, snapshot.head, runtime.principal_id))
        for index, delivery in enumerate(proposal.manifest.ordered_deliveries):
            entry = ConversationEntryV1(
                entry_id=delivery.delivery_id + "/history",
                tenant=runtime.tenant_id,
                principal=runtime.principal_id,
                ordinal=ordinal + index + 1,
                role="assistant",
                text=delivery.rendered_bytes.decode("utf-8"),
            )
            records.append(
                Record(entry.entry_id, "conversation", entry.schema_id, entry.canonical_bytes())
            )
        runtime.database.append(
            connection,
            "completion.accept",
            run_id,
            accepted.digest(),
            tuple(records),
            snapshot.head,
        )
        return accepted


def selected_delivery(
    runtime: PostgresExecutionRuntime, snapshot: Snapshot, delivery_id: str
) -> tuple[NativeDeliveryIntentV1, NativeCompletionAcceptanceV1]:
    intents = [
        value
        for value in rows(runtime, snapshot, NativeDeliveryIntentV1)
        if value.delivery_id == delivery_id
    ]
    if len(intents) != 1:
        raise LoopRejected("exact prepared delivery intent absent")
    intent = intents[0]
    acceptances = [
        value
        for value in rows(runtime, snapshot, NativeCompletionAcceptanceV1)
        if value.head == intent.acceptance_head
    ]
    if (
        len(acceptances) != 1
        or intent.delivery not in acceptances[0].proposal.manifest.ordered_deliveries
    ):
        raise LoopRejected("delivery intent differs from accepted manifest")
    if recipient(
        runtime, binding(runtime)
    ) != intent.delivery.selection.recipient or intent.receipt_key_fingerprint != digest(
        binding(runtime).receipt_key
    ):
        raise PermissionError("accepted delivery transport registration changed")
    return intent, acceptances[0]


async def commit_delivery_send(
    runtime: PostgresExecutionRuntime, peer: str, delivery_id: str, request: DeploymentGateRequest
) -> NativeDeliverySendV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        intent, accepted = selected_delivery(runtime, snapshot, delivery_id)
        previous = [
            value
            for value in rows(runtime, snapshot, NativeDeliverySendV1)
            if value.delivery_id == delivery_id
        ]
        encoded_request = canonical(request.model_dump(mode="json"))
        if previous:
            if previous[0].gate_request_bytes != encoded_request:
                raise LoopRejected("delivery first-send request changed")
            return previous[0]
        revalidate(runtime, snapshot, accepted)
        gate = PostgresDeploymentGate(
            connection, runtime.tenant_id, runtime._authenticate_entitlement
        )
        gate.require(request, "delivery.first_send", intent.canonical_bytes(), live=True)
        target = binding(runtime).target(runtime)
        issued = IssuedEffectSendTicket(
            tenant_id=runtime.tenant_id,
            issued_operation_id=request.operation_id,
            journal_decision_id="postgres:" + runtime.database.name + ":" + request.operation_id,
            journal_decision_fingerprint=intent.digest(),
            transmission_id=delivery_id + "/child/0",
            transmission_fingerprint=digest(intent.canonical_bytes()),
            request_ordinal=0,
            intent_id=delivery_id,
            intent_fingerprint=intent.digest(),
            provider=target.provider,
            account=target.account,
            recipient=target.recipient,
            canonical_address=target.canonical_address,
            endpoint_head=target.endpoint.head,
            credential_binding_head=target.credential_binding.head,
            adapter_contract_version="chiplog.hermetic-effects.v1",
            payload=intent.delivery.rendered_bytes,
            payload_fingerprint=digest(intent.delivery.rendered_bytes),
            idempotency_key=delivery_id + "/first-send",
            bundle_members=(delivery_id,),
        )
        body = asdict(issued)
        body["payload"], body["canonical_address"] = (
            issued.payload.hex(),
            issued.canonical_address.hex(),
        )
        send = headed(
            NativeDeliverySendV1(
                **common(runtime),
                delivery_id=delivery_id,
                intent_head=intent.head,
                ticket_bytes=canonical(body),
                gate_request_bytes=encoded_request,
            ),
            "completion:",
        )
        runtime.database.append(
            connection,
            "delivery.first-send",
            delivery_id,
            send.digest(),
            (record(send),),
            snapshot.head,
        )
        gate.crossed(request, intent.canonical_bytes())
        return send


async def emit_delivery(
    runtime: PostgresExecutionRuntime, peer: str, delivery_id: str
) -> NativeDeliveryClaimV1:
    with runtime._transaction() as (connection, trust):
        runtime._actor(connection, trust, peer)
        snapshot = runtime.database._snapshot(connection)
        intent, accepted = selected_delivery(runtime, snapshot, delivery_id)
        previous = [
            value
            for value in rows(runtime, snapshot, NativeDeliveryClaimV1)
            if value.delivery_id == delivery_id
        ]
        if previous:
            return previous[0]
        sends = [
            value
            for value in rows(runtime, snapshot, NativeDeliverySendV1)
            if value.delivery_id == delivery_id
        ]
        if len(sends) != 1 or sends[0].intent_head != intent.head:
            raise LoopRejected("delivery requires exact first-send record")
        send = sends[0]
        revalidate(runtime, snapshot, accepted)
        PostgresDeploymentGate(
            connection, runtime.tenant_id, runtime._authenticate_entitlement
        ).require(
            DeploymentGateRequest.model_validate_json(send.gate_request_bytes),
            "delivery.first_send",
            intent.canonical_bytes(),
            live=True,
        )
        claim = headed(
            NativeDeliveryClaimV1(
                **common(runtime),
                delivery_id=delivery_id,
                send_head=send.head,
                worker=runtime._worker,
            ),
            "completion:",
        )
        runtime.database.append(
            connection,
            "delivery.emit-claim",
            delivery_id,
            claim.digest(),
            (record(claim),),
            snapshot.head,
        )
    runtime.current_worker()
    try:
        raw = await binding(runtime).provider.emit_issued(decode_ticket(send.ticket_bytes))
    except HermeticResponseLost:
        return claim
    await retain_delivery_receipt(runtime, peer, delivery_id, raw)
    return claim


async def retain_delivery_receipt(
    runtime: PostgresExecutionRuntime, peer: str, delivery_id: str, raw: bytes
) -> NativeDeliveryReceiptV1:
    snapshot = runtime._snapshot_for_peer(peer)
    selected_delivery(runtime, snapshot, delivery_id)
    claims = [
        value
        for value in rows(runtime, snapshot, NativeDeliveryClaimV1)
        if value.delivery_id == delivery_id
    ]
    sends = [
        value
        for value in rows(runtime, snapshot, NativeDeliverySendV1)
        if value.delivery_id == delivery_id
    ]
    if len(claims) != 1 or len(sends) != 1 or claims[0].send_head != sends[0].head:
        raise LoopHold("delivery receipt lacks its original claimed transmission")
    send = sends[0]
    observed = verify_hermetic_receipt(
        decode_ticket(send.ticket_bytes), raw, registered_receipt_key=binding(runtime).receipt_key
    )
    if observed.occurred_members == (delivery_id,) and not observed.permanently_incapable_members:
        outcome = "CONFIRMED"
    elif observed.permanently_incapable_members == (delivery_id,) and not observed.occurred_members:
        outcome = "FAILED_NO_EFFECT"
    else:
        raise LoopHold("delivery receipt does not close the original delivery member")
    previous = [
        value
        for value in rows(runtime, snapshot, NativeDeliveryReceiptV1)
        if value.delivery_id == delivery_id
    ]
    if previous:
        if previous[0].raw_receipt != raw:
            raise LoopHold("delivery already has a different retained observation")
        return previous[0]
    result = headed(
        NativeDeliveryReceiptV1(
            **common(runtime),
            delivery_id=delivery_id,
            send_head=send.head,
            raw_receipt=raw,
            outcome="CONFIRMED" if outcome == "CONFIRMED" else "FAILED_NO_EFFECT",
        ),
        "completion:",
    )
    await runtime.publish_execution_batch(
        peer, "delivery.receipt", delivery_id, snapshot.head, (record(result),)
    )
    return result


async def reconcile_delivery(
    runtime: PostgresExecutionRuntime, peer: str, delivery_id: str
) -> NativeDeliveryReceiptV1 | None:
    snapshot = runtime._snapshot_for_peer(peer)
    selected_delivery(runtime, snapshot, delivery_id)
    sends = [
        value
        for value in rows(runtime, snapshot, NativeDeliverySendV1)
        if value.delivery_id == delivery_id
    ]
    if len(sends) != 1:
        raise LoopHold("delivery original transmission absent")
    raw = binding(runtime).provider.reconcile(decode_ticket(sends[0].ticket_bytes).transmission_id)
    return None if raw is None else await retain_delivery_receipt(runtime, peer, delivery_id, raw)
