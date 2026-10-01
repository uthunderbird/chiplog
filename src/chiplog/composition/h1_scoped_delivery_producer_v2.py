"""Installed-runtime selected V2 producer facade for the dispatch owner route."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.effects.h1_scoped_dispatch_semantics import (
    H1_SCOPED_DISPATCH_V2_SEMANTICS,
    h1_scoped_dispatch_v2_semantic_registry_capture,
)
from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    H1ScopedDeliveryOwnerCallV2,
    H1ScopedDeliveryRouteV2,
    PreparedH1ScopedDeliveryV2,
    PrepareH1ScopedDeliveryV2,
)
from chiplog.composition.h1_scoped_delivery_producer import (
    H1ScopedDeliveryProducer,
    H1ScopedDeliveryProducerViolation,
    _retained_sources_from_closed,
)
from chiplog.platform.broker import CallBudget, PublicPortCall, PublicPortSuccess


class H1ScopedDeliveryProducerV2(H1ScopedDeliveryProducer):
    """Select V2 from the installed v22 runtime, never from a caller DTO."""

    def _build_request_held(self, capture):  # type: ignore[no-untyped-def]
        base = super()._build_request_held(capture)
        epoch, _, deadline = capture.clock
        semantic = h1_scoped_dispatch_v2_semantic_registry_capture(
            clock_contract="chiplog.dispatch.monotonic.v2",
            clock_epoch=epoch,
            valid_until_ns=deadline,
        )
        mandate = capture.derivation.mandate.model_copy(
            update={"semantics": H1_SCOPED_DISPATCH_V2_SEMANTICS}
        )
        mandate_bytes = mandate.canonical_bytes()
        candidate = base.current.immutable_mandate_candidate.model_copy(
            update={
                "canonical_mandate_bytes": mandate_bytes,
                "mandate_fingerprint": hashlib.sha256(mandate_bytes).hexdigest(),
            }
        )
        precursor = capture.derivation.precursor_request.model_copy(update={"mandate": mandate})
        original = base.original_sources.model_copy(
            update={
                "semantic_registry": semantic,
                "planning": base.original_sources.planning.model_copy(
                    update={"candidate": candidate}
                ),
                "original_adoption": base.original_sources.original_adoption.model_copy(
                    update={"candidate": candidate}
                ),
            }
        )
        current_sources = base.current.sources.model_copy(
            update={
                "semantic_registry": semantic,
                "planning": base.current.sources.planning.model_copy(
                    update={"candidate": candidate}
                ),
                "original_adoption": base.current.sources.original_adoption.model_copy(
                    update={"candidate": candidate}
                ),
            }
        )
        current = base.current.model_copy(
            update={
                "sources": current_sources,
                "immutable_mandate_candidate": candidate,
                "supported_semantics": H1_SCOPED_DISPATCH_V2_SEMANTICS,
            }
        )
        provisional = PrepareH1ScopedDeliveryV2.model_validate(
            {name: getattr(base, name) for name in type(base).model_fields}
            | {
                "schema_id": "chiplog.effects.prepare-h1-scoped-delivery.v2",
                "original_sources": original,
                "current": current,
                "precursor_request": precursor,
                "retained_sources": (),
            }
        )
        return provisional.model_copy(
            update={
                "retained_sources": _retained_sources_from_closed(
                    provisional,
                    mandate=mandate,
                    policy_record=capture.derivation.policy_record,
                    precursor=precursor,
                    evidence=capture.evidence,
                    basis=capture.projection.basis,
                )
            }
        )

    def _build_route_held(self, capture, request):  # type: ignore[no-untyped-def]
        self._runtime._authority_gate().require_held()
        self._inventory_reader.recheck_held(capture)
        effects, _, _ = capture.runtime_sessions
        first = capture.original_completion_request
        route = H1ScopedDeliveryRouteV2(
            tenant_id=first.run.tenant,
            database_id=first.source.database_id,
            worker_session_id=first.run.worker_session,
            broker_epoch=effects.broker_epoch,
            runtime_generation=effects.generation_id,
            broker_session_id="broker:" + effects.generation_id,
            owner_session_id=effects.session_id,
            request_id=capture.command_id,
        )
        call = H1ScopedDeliveryOwnerCallV2(
            route=route,
            request=request,
            request_digest=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        )
        caller = effects.model_copy(
            update={"owner_id": "broker", "session_id": "broker:" + effects.generation_id}
        )
        sent = PublicPortCall(
            operation_id=route.operation,
            request_id=route.request_id,
            caller=caller,
            callee=effects,
            schema_id=call.schema_id,
            canonical_payload=call.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                policy_version=1,
                absolute_deadline_ns=capture.clock[2],
            ),
        )
        return call, sent

    def _require_result_held(self, *, call, sent, returned, sent_at_ns, returned_at_ns):  # type: ignore[no-untyped-def]
        self._runtime._authority_gate().require_held()
        if (
            type(returned) is not PublicPortSuccess
            or returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or returned.schema_id != "chiplog.effects.prepared-h1-scoped-delivery.v2"
            or returned_at_ns < sent_at_ns
            or returned_at_ns >= sent.budget.absolute_deadline_ns
        ):
            raise H1ScopedDeliveryProducerViolation(
                "H1 V2 Effects owner did not return exact success"
            )
        try:
            result = PreparedH1ScopedDeliveryV2.model_validate_json(returned.canonical_payload)
        except ValueError as error:
            raise H1ScopedDeliveryProducerViolation(
                "H1 V2 Effects owner result is malformed"
            ) from error
        if (
            result.canonical_bytes() != returned.canonical_payload
            or result.source_request_fingerprint != call.request_digest
            or result.authority_evidence != call.request.authority_evidence
            or result.intent_request.identity != call.request.identity
            or result.intent_request.intent.intent_id != call.request.intent_id
            or result.intent_request.current != call.request.current
        ):
            raise H1ScopedDeliveryProducerViolation(
                "H1 V2 Effects owner result differs from request"
            )
        return result
