"""Actual isolated owner preparation for an adopted original call preview."""

from __future__ import annotations

import base64
import secrets
import time
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.call_acceptance_preparation import call_record_reference
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, NonSchedulerFence, Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_v2 import (
    DispatchPreparationV2,
    DispatchRecordV2,
    canonical,
    digest,
    intent_fingerprint,
    reference,
)
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    DispatchAcquisitionV2,
    DispatchAdoptionV2,
    ExternalActionIntentV2,
    PublishDispatchIntentV2,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess

from .r7_planning import ObservedTrustCall
from .r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from .r14_acceptance_v2_records import build_acceptance_envelope
from .r14_call_acceptance_port import CallAcceptanceAdoption
from .r14_call_batch import LOOP_PREPARATION_SCHEMA
from .r14_call_dispatch_policy import loop_dispatch_semantics, policy_reference
from .r14_call_preview import (
    CallPreviewIssuance,
    _effect_head,
    execution_run_head,
    read_call_previews,
)
from .r14_loop_history import read_execution_call_history
from .r16_dispatch_inputs import (
    DispatchCapture,
    capture_dispatch,
    current_inputs,
    require_dispatch_scope,
    source_inventory,
)
from .r16_dispatch_publication import authenticate, owner_call
from .r16_effects_inputs import canonical as retained_canonical

if TYPE_CHECKING:
    from .r16_dispatch_runtime import ExecutionDispatchRuntime


class PreparedCallExchange(DispatchObservationDTO):
    preview: CallPreviewIssuance
    adoption: CallAcceptanceAdoption
    captured: DispatchCapture
    observed: ObservedTrustCall
    loop_sent: PublicPortCall
    effects_sent: PublicPortCall
    retained: RetainedAcceptancePreparationV2


def original_call_request(
    preview: CallPreviewIssuance,
    adoption: CallAcceptanceAdoption,
    captured: DispatchCapture,
    observed: ObservedTrustCall,
    inventory: call.CallInventorySnapshot,
) -> tuple[call.AcceptConsequentialCallRequest, ExternalActionIntentV2]:
    """Interpret retained inputs only; owner outputs and source selection are separate."""
    target, mandate = preview.preview.target, preview.request.mandate
    worker = captured.cut.worker
    if worker is None:
        raise ValueError("call preparation lacks original worker")
    rows = tuple(
        row for row in inventory.ordered_calls if row.original_call_id == target.original_call_id
    )
    if (
        len(rows) != 1
        or rows[0].initialized != target.initialized
        or not isinstance(rows[0].acceptance, InitializedCall)
        or not isinstance(rows[0].terminal, Absent)
        or execution_run_head(worker.run) != target.current_run
    ):
        raise LoopRejected("adoption target is no longer the original live branch")
    initialized = rows[0].initialized_record
    command_id = "call-acceptance/" + digest(
        canonical(
            [
                captured.cut.tenant_id,
                mandate.principal_id,
                "effects.accept_call",
                adoption.act_id,
            ]
        )
    )
    original_adoption = DispatchAdoptionV2(
        schema_id="chiplog.effects.dispatch-adoption.v2",
        adoption_act_id=adoption.act_id,
        display=reference(preview.preview.preview_id, preview.preview.display_bytes),
        display_bytes=preview.preview.display_bytes,
        mandate_bytes=mandate.canonical_bytes(),
        ingress=reference("adoption/" + adoption.act_id, adoption.canonical_bytes()),
        ingress_bytes=adoption.canonical_bytes(),
    )
    sources = source_inventory(captured, mandate, original_adoption.canonical_bytes(), None)
    intent = ExternalActionIntentV2(
        schema_id="chiplog.effects.external-action-intent.v2",
        intent_id=command_id + "/intent",
        fingerprint="0" * 64,
        mandate=mandate,
        acquisition=DispatchAcquisitionV2(
            schema_id="chiplog.effects.dispatch-acquisition.v2",
            adoption=original_adoption,
            authenticated_invocation=retained_canonical(observed),
            precursor_request=preview.request.request,
            precursor_result=preview.result,
            original_sources=sources,
        ),
    )
    intent = intent.model_copy(update={"fingerprint": intent_fingerprint(intent)})
    families: tuple[
        tuple[
            Literal[
                "ACTOR",
                "MANDATE",
                "TOOL_SCHEMA",
                "POLICY",
                "APPLICABILITY",
                "RECIPIENT",
                "CONSEQUENCE_SCOPE",
                "PLANNING",
                "DEPENDENCY",
                "EVIDENCE",
                "CONFLICT_ORDER",
            ],
            bytes,
        ],
        ...,
    ] = (
        ("ACTOR", captured.principal),
        ("MANDATE", mandate.canonical_bytes()),
        ("TOOL_SCHEMA", preview.initialization.request.tool_registry.canonical_bytes()),
        ("POLICY", retained_canonical(sources.semantic_registry)),
        (
            "APPLICABILITY",
            retained_canonical(
                (
                    sources.runtime_and_fence,
                    sources.credential_lifecycle,
                    sources.deployment_entitlement,
                    sources.clock,
                )
            ),
        ),
        ("RECIPIENT", retained_canonical(sources.endpoint)),
        ("CONSEQUENCE_SCOPE", mandate.consequence_scope.canonical_bytes()),
        ("PLANNING", retained_canonical(sources.planning)),
        (
            "DEPENDENCY",
            canonical([item.model_dump(mode="json") for item in mandate.dependencies]),
        ),
        (
            "EVIDENCE",
            retained_canonical((sources.effects_history, mandate.factual_assertion_evidence)),
        ),
        ("CONFLICT_ORDER", retained_canonical(sources.normative_conflict_generation)),
    )
    cut = call.CallPreparationCut(
        tenant_id=captured.cut.tenant_id,
        current_run=target.current_run,
        run_state="ACTIVE",
        tenant_commit_sequence=captured.cut.tenant_frontier,
        materialization_commitment=captured.cut.materialization_commitment,
        predecessor_inventory=inventory,
        complete_call_inventory=call_record_reference(
            "call-inventory:" + captured.cut.tenant_id, inventory
        ),
        authority_registry=call.CallSubjectHead(
            subject_id=policy_reference().subject_id,
            revision=Present(
                head=policy_reference().head, fingerprint=policy_reference().fingerprint
            ),
        ),
        sources=tuple(
            call.CallAuthorityObservation(
                source_id="call." + family,
                family=family,
                source=call.CallSubjectHead(
                    subject_id="call." + family,
                    revision=Present(
                        head=reference("call." + family, raw).head, fingerprint=digest(raw)
                    ),
                ),
                generation=worker.owner_session.generation_id,
                frontier=str(captured.cut.tenant_frontier),
                canonical_value_base64=base64.b64encode(raw).decode(),
                observed_at_ns=captured.observed_time_ns,
                valid_until_ns=min(
                    mandate.horizon.expires_at_ns, captured.observed_time_ns + 5_000_000_000
                ),
            )
            for family, raw in families
        ),
        fence=NonSchedulerFence.model_validate_json(worker.fence.canonical_bytes()),
    )
    loop_request = call.AcceptConsequentialCallRequest(
        command_id=command_id,
        initialized_record=initialized,
        binding=call.ConsequentialAcceptanceBinding(
            original_call_id=target.original_call_id,
            original=initialized.call.original,
            initialized=target.initialized,
            tool_schema=initialized.call.tool_schema,
            tool_policy=initialized.call.tool_policy,
            external_intent=call.CallSubjectHead(
                subject_id=intent.intent_id,
                revision=Present(
                    head=reference(intent.intent_id, intent.canonical_bytes()).head,
                    fingerprint=digest(intent.canonical_bytes()),
                ),
            ),
            dispatch_semantics=loop_dispatch_semantics(),
            cut=cut,
        ),
    )
    return loop_request, intent


async def prepare_call_exchange(
    runtime: ExecutionDispatchRuntime, peer: str, adoption: CallAcceptanceAdoption
) -> PreparedCallExchange:
    """Fresh preparation only; selected replay must be resolved before this call."""
    observed = await authenticate(runtime, peer)
    resources = runtime._require_dispatch_resources()
    with runtime._authority_gate().hold():
        matches = tuple(
            value
            for value in read_call_previews(runtime)
            if value.preview.canonical_bytes() == adoption.preview_bytes
        )
        if len(matches) != 1:
            raise LoopRejected("adoption has no exact independently selected preview")
        preview = matches[0]
        target, mandate = preview.preview.target, preview.request.mandate
        captured = capture_dispatch(
            runtime,
            resources,
            observed,
            target.current_run.subject_id,
            original_call_id=target.original_call_id,
        )
        if captured.principal != preview.captured.principal:
            raise LoopRejected("adoption principal differs from original preview")
        require_dispatch_scope(captured, resources, mandate, None, first_send=False)
        worker = captured.cut.worker
        assert worker is not None
        _, inventory, _ = read_execution_call_history(runtime)
        loop_request, intent = original_call_request(
            preview, adoption, captured, observed, inventory
        )
        command_id = loop_request.command_id
        original_adoption = intent.acquisition.adoption
    callee = captured.sessions[2]
    loop_sent = PublicPortCall(
        operation_id="agent_loop.prepare_consequential_acceptance",
        request_id="call-acceptance:" + secrets.token_hex(24),
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker:" + callee.generation_id,
        ),
        callee=callee,
        schema_id=LOOP_PREPARATION_SCHEMA,
        canonical_payload=loop_request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=min(
                mandate.horizon.expires_at_ns, time.monotonic_ns() + 5_000_000_000
            ),
        ),
    )
    returned = await runtime._supervisor.runtime().call(loop_sent)
    if (
        not isinstance(returned, PublicPortSuccess)
        or returned.request_id != loop_sent.request_id
        or returned.responder != callee
        or returned.schema_id != "chiplog.call.preparation-result.v1"
        or time.monotonic_ns() >= loop_sent.budget.absolute_deadline_ns
    ):
        raise LoopRejected("call acceptance owner response identity, schema or deadline differs")
    import json

    if json.loads(returned.canonical_payload).get("kind") == "CALL_PREPARATION_REJECTED_V1":
        rejected = call.CallPreparationRejected.model_validate_json(returned.canonical_payload)
        raise LoopRejected("call acceptance owner rejected: " + rejected.reason)
    loop_proposal = call.PreparedConsequentialAcceptance.model_validate_json(
        returned.canonical_payload
    )
    command = PublishDispatchIntentV2(
        schema_id="chiplog.effects.publish-dispatch-intent.v2",
        identity=CommandIdentity(
            command_id=command_id,
            fingerprint=digest(adoption.canonical_bytes()),
            expected_tenant_head=captured.cut.tenant_frontier,
        ),
        intent=intent,
        fence=worker.fence,
        complete_publication_manifest=tuple(
            _effect_head(ref) for ref in loop_proposal.complete_acceptance_manifest
        ),
    )
    effects_request = DispatchPreparationV2(
        schema_id="chiplog.effects.dispatch-preparation.v2",
        command=command,
        previous=None,
        current=current_inputs(
            captured,
            command.canonical_bytes(),
            intent,
            original_adoption_bytes=original_adoption.canonical_bytes(),
        ),
    )
    effects_sent, raw = await owner_call(
        runtime,
        "effects.prepare_dispatch_v2",
        effects_request.schema_id,
        effects_request.canonical_bytes(),
    )
    retained = RetainedAcceptancePreparationV2(
        loop_request=loop_request,
        loop_proposal=loop_proposal,
        effects_request=effects_request,
        effects_proposal=DispatchRecordV2.model_validate_json(raw),
    )
    build_acceptance_envelope(retained)
    with runtime._authority_gate().hold():
        fresh = capture_dispatch(
            runtime,
            resources,
            observed,
            worker.run.run_id,
            original_call_id=target.original_call_id,
        )
        if fresh != captured:
            raise LoopRejected("call acceptance source cut changed during owner preparation")
        require_dispatch_scope(fresh, resources, mandate, None, first_send=False)
        if time.monotonic_ns() >= min(
            loop_sent.budget.absolute_deadline_ns, effects_sent.budget.absolute_deadline_ns
        ):
            raise LoopRejected("call acceptance owner preparation expired")
    return PreparedCallExchange(
        preview=preview,
        adoption=adoption,
        captured=captured,
        observed=observed,
        loop_sent=loop_sent,
        effects_sent=effects_sent,
        retained=retained,
    )
