"""Mounted first-B producer for the H1 scoped Effects preparation route.

The producer accepts only a reader-issued inventory capture.  It constructs the
typed owner request while the capture is current, sends the exact canonical
wire to the mounted Effects owner outside the authority gate, then rechecks the
same capture before accepting the returned preparation.
"""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING

from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.fences import Absent
from chiplog.capabilities.effects.h1_scoped_preparation import _effect_head
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRouteV1,
    PreparedH1ScopedDeliveryV1,
    PrepareH1ScopedDeliveryV1,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    ExternalActionIntentV3,
    PreparedDeliveryAuthority,
    PrepareScopedIntentPublication,
    ScopedDispatchAcquisition,
    ScopedPrecursorResult,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    make_scoped_intent_retained_exchange,
    scoped_intent_fingerprint,
)
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_producer_inventory import (
    H1ProducerInventoryCapture,
    H1ProducerInventoryReader,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityCapture


class H1ScopedDeliveryProducerViolation(PermissionError):
    """The inventory cannot safely produce or retain an H1 scoped owner call."""


class H1ScopedDeliveryProducer:
    """Produce one mounted, first-B H1 Effects preparation exchange."""

    __slots__ = ("_inventory_reader", "_runtime")

    def __init__(
        self, runtime: CommonCliExecutionRuntime, inventory_reader: H1ProducerInventoryReader
    ) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 scoped producer requires the installed common CLI runtime")
        if (
            type(inventory_reader) is not H1ProducerInventoryReader
            or inventory_reader._runtime is not runtime
        ):
            raise TypeError("H1 scoped producer requires its runtime's inventory reader")
        self._runtime = runtime
        self._inventory_reader = inventory_reader

    async def prepare(
        self, *, authority_capture: H1ScopedDeliveryAuthorityCapture
    ) -> H1CompletionOwnerExchangeV1:
        """Call the mounted Effects owner and return its exact public exchange."""
        capture = await self._inventory_reader.read(authority_capture=authority_capture)
        try:
            with self._runtime._authority_gate().hold():
                call, sent = self._build_sent_held(capture)
                # The gate is intentionally released before the owner await.
                self._inventory_reader.recheck_held(capture)

            sent_at_ns = time.monotonic_ns()
            returned = await self._runtime._supervisor.runtime().call(sent)
            returned_at_ns = time.monotonic_ns()

            with self._runtime._authority_gate().hold():
                self._inventory_reader.recheck_held(capture)
                self._require_result_held(
                    call=call,
                    sent=sent,
                    returned=returned,
                    sent_at_ns=sent_at_ns,
                    returned_at_ns=returned_at_ns,
                )
            return H1CompletionOwnerExchangeV1(
                role="effects",
                sent=sent,
                returned=returned,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
        except H1ScopedDeliveryProducerViolation:
            raise
        except (TypeError, ValueError, AttributeError) as error:
            raise H1ScopedDeliveryProducerViolation(
                "H1 scoped producer cannot authenticate its owner exchange"
            ) from error
        finally:
            self._inventory_reader.release(capture)

    def _build_sent_held(
        self, capture: H1ProducerInventoryCapture
    ) -> tuple[H1ScopedDeliveryOwnerCallV1, PublicPortCall]:
        self._runtime._authority_gate().require_held()
        engine = self._runtime._supervisor.runtime()
        first = capture.original_completion_request
        effects, agent_loop, deployment_trust = capture.runtime_sessions
        if (
            tuple(item.owner_id for item in capture.runtime_sessions)
            != ("effects", "agent_loop", "deployment_trust")
            or effects != engine.session("effects")
            or agent_loop != engine.session("agent_loop")
            or deployment_trust != engine.session("deployment_trust")
            or any(
                item.tenant_id != first.run.tenant
                or item.broker_epoch != effects.broker_epoch
                or item.generation_id != effects.generation_id
                for item in capture.runtime_sessions
            )
            or capture.current_worker.owner_session != agent_loop
        ):
            raise H1ScopedDeliveryProducerViolation("H1 scoped producer runtime sessions differ")
        epoch, observed_ns, deadline_ns = capture.clock
        if (
            observed_ns >= deadline_ns
            or capture.current.clock_epoch != epoch
            or capture.current.observed_time_ns != observed_ns
            or capture.current.lease_expires_at_ns != deadline_ns
        ):
            raise H1ScopedDeliveryProducerViolation("H1 scoped producer inventory clock differs")
        if (
            hashlib.sha256(capture.command_id.encode("utf-8")).hexdigest()
            != capture.command_fingerprint
        ):
            raise H1ScopedDeliveryProducerViolation(
                "H1 scoped producer command fingerprint differs"
            )
        caller = BrokerSession(
            tenant_id=effects.tenant_id,
            broker_epoch=effects.broker_epoch,
            generation_id=effects.generation_id,
            owner_id="broker",
            session_id="broker:" + effects.generation_id,
        )
        route = H1ScopedDeliveryRouteV1(
            tenant_id=first.run.tenant,
            database_id=first.source.database_id,
            worker_session_id=first.run.worker_session,
            broker_epoch=caller.broker_epoch,
            runtime_generation=caller.generation_id,
            broker_session_id=caller.session_id,
            owner_session_id=effects.session_id,
            request_id=capture.command_id,
        )
        identity = CommandIdentity(
            command_id=capture.command_id,
            fingerprint=capture.command_fingerprint,
            expected_tenant_head=capture.expected_tenant_head,
        )
        provisional = PrepareH1ScopedDeliveryV1(
            identity=identity,
            intent_id=capture.intent_id,
            expected_intent=Absent(),
            original_completion_request=first,
            prepared_completion=capture.prepared_completion,
            selected_scope=capture.selected_scope,
            retained_origin=capture.retained_origin,
            fence=capture.current_worker.fence,
            authority_evidence=capture.evidence,
            precursor_request=capture.derivation.precursor_request,
            preexisting_communication_authority=capture.derivation.policy_record,
            current_disclosure_authority=capture.derivation.policy_record,
            original_sources=capture.original_sources,
            current=capture.current,
            complete_current_origin_sources=(capture.derivation.policy_record,),
            retained_sources=(),
        )
        retained_sources = _retained_sources(provisional, capture)
        request = provisional.model_copy(update={"retained_sources": retained_sources})
        call = H1ScopedDeliveryOwnerCallV1(
            route=route,
            request=request,
            request_digest=hashlib.sha256(request.canonical_bytes()).hexdigest(),
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
                absolute_deadline_ns=deadline_ns,
            ),
        )
        if (
            call.canonical_bytes() != sent.canonical_payload
            or call.route.tenant_id != sent.caller.tenant_id
            or call.route.tenant_id != sent.callee.tenant_id
            or call.route.broker_epoch != sent.caller.broker_epoch
            or call.route.runtime_generation != sent.caller.generation_id
            or call.route.broker_session_id != sent.caller.session_id
            or call.route.owner_session_id != sent.callee.session_id
            or call.route.request_id != sent.request_id
            or call.request.identity.command_id != sent.request_id
        ):
            raise H1ScopedDeliveryProducerViolation(
                "H1 scoped producer route differs from owner wire"
            )
        return call, sent

    def _require_result_held(
        self,
        *,
        call: H1ScopedDeliveryOwnerCallV1,
        sent: PublicPortCall,
        returned: object,
        sent_at_ns: int,
        returned_at_ns: int,
    ) -> PreparedH1ScopedDeliveryV1:
        self._runtime._authority_gate().require_held()
        if (
            type(returned) is not PublicPortSuccess
            or returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or returned.schema_id != PreparedH1ScopedDeliveryV1.model_fields["schema_id"].default
            or returned_at_ns < sent_at_ns
            or returned_at_ns >= sent.budget.absolute_deadline_ns
        ):
            raise H1ScopedDeliveryProducerViolation("H1 Effects owner did not return exact success")
        try:
            result = PreparedH1ScopedDeliveryV1.model_validate_json(returned.canonical_payload)
        except ValueError as error:
            raise H1ScopedDeliveryProducerViolation(
                "H1 Effects owner result is malformed"
            ) from error
        request = call.request
        if (
            result.disposition != "PREPARED"
            or result.source_request_fingerprint != call.request_digest
            or result.canonical_bytes() != returned.canonical_payload
            or result.authority_evidence != request.authority_evidence
            or result.precursor_request != request.precursor_request
            or result.retained_sources != request.retained_sources
            or result.intent_request.identity != request.identity
            or result.intent_request.expected_intent != request.expected_intent
            or result.intent_request.current != request.current
            or result.intent_request.fence != request.fence
            or result.intent_request.intent.intent_id != request.intent_id
        ):
            raise H1ScopedDeliveryProducerViolation("H1 Effects owner result differs from request")
        return result


def _retained_sources(
    request: PrepareH1ScopedDeliveryV1, capture: H1ProducerInventoryCapture
) -> tuple[object, ...]:
    """Reproduce the owner-required retained-source projection exactly."""
    derivation = capture.derivation
    evidence = capture.evidence
    policy_record = derivation.policy_record
    mandate = derivation.mandate
    precursor = derivation.precursor_request
    acquisition = ScopedDispatchAcquisition(
        authority=PreparedDeliveryAuthority(
            basis=capture.projection.basis,
            preexisting_communication_authority=policy_record,
            current_disclosure_authority=policy_record,
            exact_mandate_bytes=mandate.canonical_bytes(),
        ),
        precursor_request=precursor,
        precursor_result=ScopedPrecursorResult(
            source_request_fingerprint=hashlib.sha256(precursor.canonical_bytes()).hexdigest(),
            mandate_fingerprint=hashlib.sha256(mandate.canonical_bytes()).hexdigest(),
            interpretation_policy=policy_record.head,
            complete_evaluation_evidence=(
                _effect_head(evidence.grant_anchor.grant),
                _effect_head(evidence.policy_anchor.policy),
                _effect_head(evidence.policy_anchor.decision),
            ),
        ),
        original_sources=request.original_sources,
    )
    unsigned = ExternalActionIntentV3(
        intent_id=request.intent_id,
        fingerprint="0" * 64,
        mandate=mandate,
        acquisition=acquisition,
    )
    intent = unsigned.model_copy(update={"fingerprint": scoped_intent_fingerprint(unsigned)})
    publication = PrepareScopedIntentPublication(
        identity=request.identity,
        intent=intent,
        expected_intent=request.expected_intent,
        current=request.current,
        complete_current_origin_sources=request.complete_current_origin_sources,
        fence=request.fence,
    )
    return make_scoped_intent_retained_exchange(publication).original_sources


__all__ = ["H1ScopedDeliveryProducer", "H1ScopedDeliveryProducerViolation"]
