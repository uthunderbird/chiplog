"""Pure owner-prepared fixture; no trusted journal, credential or runtime claim."""

import base64

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    prepare_consequential_acceptance,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import InitializedCall
from chiplog.capabilities.effects import dispatch_v2_contracts as wire
from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import (
    CapturedSource,
    DispatchAuthorityObservation,
    DispatchObservationCut,
    DispatchSourceInventory,
)
from chiplog.capabilities.effects.dispatch_v2 import (
    DispatchPreparationV2,
    canonical,
    digest,
    evaluate_precursor,
    intent_fingerprint,
    prepare_dispatch,
    reference,
)
from chiplog.capabilities.effects.fences import NonSchedulerFence as EffectsFence
from chiplog.composition.r14_acceptance_v2_contracts import RetainedAcceptancePreparationV2
from chiplog.composition.r14_call_acceptance_port import CallAcceptanceTarget
from chiplog.composition.r14_call_acceptance_preview import build_call_acceptance_preview
from chiplog.composition.r14_call_dispatch_policy import (
    CLOCK,
    SEMANTICS,
    bundle_references,
    loop_dispatch_semantics,
    policy_reference,
)
from tests.support.call_acceptance import preview_inputs


def prepared_acceptance(
    *, mandate_updates: dict[str, object] | None = None, display_bytes: bytes | None = None
) -> RetainedAcceptancePreparationV2:
    def head(subject: str) -> ExactHead:
        return reference(subject, subject.encode())

    def loop_head(ref: ExactHead) -> call.CallSubjectHead:
        return call.CallSubjectHead(
            subject_id=ref.subject_id, revision=Present(head=ref.head, fingerprint=ref.fingerprint)
        )

    model_call = ConsequentialToolCall(
        call_id="model-label",
        tool="request_self_effect",
        arguments=SelfEffectArguments(
            payload=b"\xff\x00payload", bundle_members=("first", "second")
        ),
    )
    original = call.OriginalCallKey(
        tenant_id="hermetic-tenant",
        original_run_id="run",
        original_turn_id="turn",
        captured_response=loop_head(head("response")),
        ordinal=0,
        model_call_label=model_call.call_id,
    )
    initialized = call.InitializedCallRecord(
        original_call_id="call:" + original.digest(),
        predecessor=Absent(),
        call=call.SealedCallInput(
            original=original,
            classification="CONSEQUENTIAL",
            tool_schema=loop_head(head("schema")),
            tool_policy=loop_head(head("tool-policy")),
            canonical_call_base64=base64.b64encode(model_call.model_dump_json().encode()).decode(),
            retry_lineage=NotApplicable(),
        ),
    )
    init_ref = call_record_reference(initialized.original_call_id, initialized)
    inventory = call.CallInventorySnapshot(
        tenant_id="hermetic-tenant",
        tenant_commit_sequence=0,
        ordered_calls=(
            call.CallLifecycleObservation(
                original_call_id=initialized.original_call_id,
                initialized=init_ref,
                initialized_record=initialized,
                acceptance=InitializedCall(initialized=init_ref.revision),
                terminal=Absent(),
            ),
        ),
    )
    run = loop_head(head("run"))
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="run",
        run_head=run.revision.head,
        worker_session_id="worker",
        runtime_generation="generation",
    )
    cut = call.CallPreparationCut(
        tenant_id="hermetic-tenant",
        current_run=run,
        run_state="ACTIVE",
        tenant_commit_sequence=0,
        materialization_commitment="a" * 64,
        predecessor_inventory=inventory,
        complete_call_inventory=call_record_reference("call-inventory:hermetic-tenant", inventory),
        authority_registry=loop_head(head("registry")),
        sources=(
            call.CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=loop_head(head("actor")),
                generation="generation",
                frontier="0",
                canonical_value_base64="YWN0b3I=",
                observed_at_ns=1,
                valid_until_ns=20,
            ),
        ),
        fence=fence,
    )
    _, template = preview_inputs()
    origin = wire.InitializedCallOrigin(
        kind="INITIALIZED_CONSEQUENTIAL_CALL",
        original_run_id="run",
        original_call_id=initialized.original_call_id,
        initialization_run_head=head("run"),
        initialized_head=ExactHead(
            subject_id=init_ref.subject_id,
            head=init_ref.revision.head,
            fingerprint=init_ref.revision.fingerprint,
        ),
        tool_name=model_call.tool,
        tool_schema="chiplog.request-self-effect.v2",
        tool_policy=head("tool-policy"),
        sealed_arguments=model_call.arguments.model_dump_json().encode(),
    )
    mandate = template.model_copy(
        update={
            "tenant_id": "hermetic-tenant",
            "principal_id": "hermetic-principal",
            "actor_id": "hermetic-principal",
            "origin": origin,
            "operation_profile": policy_reference(),
            "semantics": SEMANTICS,
            "horizon": template.horizon.model_copy(update={"clock_contract": CLOCK}),
            "dependencies": (),
            "factual_assertion_evidence": (),
            "payload": model_call.arguments.payload,
            "effect_fingerprint": digest(model_call.arguments.payload),
            "bundle_members": bundle_references(initialized.original_call_id, model_call.arguments),
            "recipient": template.recipient.model_copy(
                update={
                    "account": "hermetic-account",
                    "recipient": "hermetic-principal",
                    "canonical_address": b"hermetic://effects/hermetic-principal",
                }
            ),
        }
    )

    if mandate_updates is not None:
        mandate = mandate.model_copy(update=mandate_updates)

    def sources() -> DispatchSourceInventory:
        values: dict[str, CapturedSource] = {}
        for name in DispatchSourceInventory.model_fields:
            raw = canonical({"source": name})
            if name == "runtime_and_fence":
                raw = canonical({"fence": fence.model_dump(mode="json")})
            elif name == "normative_conflict_generation":
                raw = canonical(mandate.normative_conflict_generation.model_dump(mode="json"))
            elif name == "effects_history":
                raw = b"[]"
            values[name] = CapturedSource(
                source_id=name,
                source_version="v1",
                owner_id="owner",
                reader_id="reader",
                invalidation_manifest=head("invalidation"),
                head=reference(name, raw),
                canonical_value=raw,
                clock_contract=mandate.horizon.clock_contract,
                clock_epoch=mandate.horizon.clock_epoch,
                valid_until_ns=20,
            )
        return DispatchSourceInventory.model_validate(values)

    precursor = wire.DispatchPrecursorRequestV2(
        schema_id="chiplog.effects.dispatch-precursor-request.v2",
        request_id="precursor",
        mandate_digest=digest(mandate.canonical_bytes()),
        interpretation_policy=policy_reference(),
        preexisting_source_heads=(mandate.preexisting_authority_basis,),
    )
    preview = build_call_acceptance_preview(
        CallAcceptanceTarget(
            original_call_id=initialized.original_call_id,
            initialized=init_ref,
            current_run=run,
        ),
        mandate,
    )
    display, ingress = preview.display_bytes if display_bytes is None else display_bytes, b"adopt"
    acquisition = wire.DispatchAcquisitionV2(
        schema_id="chiplog.effects.dispatch-acquisition.v2",
        adoption=wire.DispatchAdoptionV2(
            schema_id="chiplog.effects.dispatch-adoption.v2",
            adoption_act_id="act",
            display=reference("display", display),
            display_bytes=display,
            mandate_bytes=mandate.canonical_bytes(),
            ingress=reference("ingress", ingress),
            ingress_bytes=ingress,
        ),
        authenticated_invocation=b"fixture-not-authenticated",
        precursor_request=precursor,
        precursor_result=evaluate_precursor(precursor, mandate),
        original_sources=sources(),
    )
    intent = wire.ExternalActionIntentV2(
        schema_id="chiplog.effects.external-action-intent.v2",
        intent_id="intent",
        fingerprint="0" * 64,
        mandate=mandate,
        acquisition=acquisition,
    )
    intent = intent.model_copy(update={"fingerprint": intent_fingerprint(intent)})
    loop_request = call.AcceptConsequentialCallRequest(
        command_id="accept-loop",
        initialized_record=initialized,
        binding=call.ConsequentialAcceptanceBinding(
            original_call_id=initialized.original_call_id,
            original=original,
            initialized=init_ref,
            tool_schema=initialized.call.tool_schema,
            tool_policy=initialized.call.tool_policy,
            external_intent=loop_head(reference(intent.intent_id, intent.canonical_bytes())),
            dispatch_semantics=loop_dispatch_semantics(),
            cut=cut,
        ),
    )
    loop = prepare_consequential_acceptance(loop_request)
    assert isinstance(loop, call.PreparedConsequentialAcceptance)
    command = wire.PublishDispatchIntentV2(
        schema_id="chiplog.effects.publish-dispatch-intent.v2",
        identity=CommandIdentity(
            command_id="accept-effects", fingerprint="a" * 64, expected_tenant_head=0
        ),
        intent=intent,
        complete_publication_manifest=tuple(
            ExactHead(
                subject_id=r.subject_id, head=r.revision.head, fingerprint=r.revision.fingerprint
            )
            for r in loop.complete_acceptance_manifest
        ),
        fence=EffectsFence.model_validate_json(fence.canonical_bytes()),
    )
    observation = DispatchAuthorityObservation(
        schema_id="chiplog.effects.dispatch-observation.v2",
        query_fingerprint=digest(command.canonical_bytes()),
        cut=DispatchObservationCut(
            tenant_id="hermetic-tenant",
            database_identity=head("database"),
            selected_journal_head=head("journal"),
            materialization_commitment="a" * 64,
            tenant_frontier=0,
            source_role_registry=policy_reference(),
            capture_id="capture",
        ),
        sources=sources(),
        complete_effect_history=(),
        complete_history_fingerprint=digest(b"[]"),
    )
    current = wire.CurrentDispatchInputsV2(
        schema_id="chiplog.effects.current-dispatch-inputs.v2",
        command_fingerprint=digest(command.canonical_bytes()),
        immutable_mandate=reference(mandate.mandate_id, mandate.canonical_bytes()),
        observation=observation,
        supported_semantics=SEMANTICS,
        clock_contract=mandate.horizon.clock_contract,
        clock_epoch=mandate.horizon.clock_epoch,
        observed_time_ns=2,
        lease_expires_at_ns=20,
    )
    request = DispatchPreparationV2(
        schema_id="chiplog.effects.dispatch-preparation.v2",
        command=command,
        previous=None,
        current=current,
    )
    return RetainedAcceptancePreparationV2(
        loop_request=loop_request,
        loop_proposal=loop,
        effects_request=request,
        effects_proposal=prepare_dispatch(request),
    )
