"""Explicit preview/adoption and isolated v2 effects preparation orchestration."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from typing import TYPE_CHECKING, Literal

from chiplog.capabilities.agent_loop.contracts import LoopRejected, ProposalDisplay
from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import DispatchObservationDTO
from chiplog.capabilities.effects.dispatch_v2 import (
    DispatchPreparationV2,
    DispatchRecordV2,
    PrecursorPreparationV2,
    canonical,
    digest,
    reference,
)
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    AuthorizeDispatchV2,
    CommitFirstSendV2,
    DispatchAcquisitionV2,
    DispatchAdoptionV2,
    DispatchMandateV2,
    DispatchPrecursorRequestV2,
    DispatchPrecursorResultV2,
    ExternalActionIntentV2,
    MandateHorizon,
    PlanEffectOrigin,
    PublishDispatchIntentV2,
)
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r16_dispatch_history import normative_generation
from chiplog.composition.r16_dispatch_inputs import (
    DispatchCapture,
    capture_dispatch,
    current_inputs,
    planning_scope,
    require_dispatch_scope,
    source_inventory,
)
from chiplog.composition.r16_dispatch_registry import CLOCK, SEMANTICS, policy_reference
from chiplog.composition.r16_effects import EffectPreviewBinding, R16EffectsProducer
from chiplog.composition.r16_effects_inputs import canonical as retained_canonical
from chiplog.composition.r16_effects_inputs import planning_records
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactRecordHead,
    ExactReplayQuery,
    NoSelectedDecision,
    ObservedPresence,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PlanEffectBatch,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import BrokerPublicationCoordinator, source_commands

if TYPE_CHECKING:
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime


class DispatchPreview(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-preview.v2"]
    original_display_id: str
    original_display_digest: str
    original_adoption_act_id: str
    worker_run_id: str
    mandate: DispatchMandateV2
    precursor_request: DispatchPrecursorRequestV2
    precursor_result: DispatchPrecursorResultV2
    precursor_invocation: bytes


async def authenticate(runtime: R16DispatchRuntime, peer: str) -> ObservedTrustCall:
    if peer != "hermetic-ingress":
        raise LoopRejected("dispatch requires the exact registered hermetic ingress")
    return await runtime._observed_trust_call(
        "AUTHENTICATE",
        {
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "peer_credential": f"uid:{os.getuid()}",
            "session_id": "hermetic-session",
        },
    )


async def owner_call(
    runtime: R16DispatchRuntime,
    operation: str,
    schema: str,
    payload: bytes,
) -> tuple[PublicPortCall, bytes]:
    callee = runtime._supervisor.runtime().session("effects")
    sent = PublicPortCall(
        operation_id=operation,
        request_id="dispatch:" + secrets.token_hex(24),
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker:" + callee.generation_id,
        ),
        callee=callee,
        schema_id=schema,
        canonical_payload=payload,
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )
    returned = await runtime._supervisor.runtime().call(sent)
    if isinstance(returned, PublicPortRejected):
        raise LoopRejected("dispatch owner rejected: " + returned.failure.reason)
    if (
        returned.request_id != sent.request_id
        or returned.responder != callee
        or time.monotonic_ns() >= sent.budget.absolute_deadline_ns
        or returned.schema_id
        not in {
            "chiplog.effects.dispatch-record.v2",
            "chiplog.effects.dispatch-outcome-record.v2",
            "chiplog.effects.dispatch-precursor-result.v2",
        }
    ):
        raise LoopRejected("dispatch owner response identity, schema or deadline differs")
    return sent, returned.canonical_payload


async def preview_dispatch(runtime: R16DispatchRuntime, proposal_id: str) -> ProposalDisplay:
    original = await R16EffectsProducer(runtime).display_effect(proposal_id)
    binding = EffectPreviewBinding.model_validate_json(original.canonical_command)
    observed = await authenticate(runtime, "hermetic-ingress")
    with runtime._authority_gate().hold():
        captured = capture_dispatch(runtime, runtime._dispatch_resources, observed, original.run_id)
        resources = runtime._dispatch_resources
        planning_result = base64.b64decode(binding.planning_result_base64, validate=True)
        result = json.loads(planning_result)
        revision_id = result["result"]["revision_id"]["value"]
        revision = next(item for item in result["records"] if item["record_id"] == revision_id)
        planning_revision = ExactHead(
            subject_id=revision_id,
            head=revision_id,
            fingerprint=digest(base64.b64decode(revision["canonical_bytes"], validate=True)),
        )
        basis = reference("dispatch.preexisting-principal", captured.principal)
        plan_scope = reference("dispatch.planning-scope", planning_scope(captured, None))
        payload = binding.proposal.payload()
        display_id = original.display_id + "/dispatch-v2"
        mandate = DispatchMandateV2(
            schema_id="chiplog.effects.dispatch-mandate.v2",
            mandate_id=display_id + "/mandate",
            tenant_id=runtime._tenant_id,
            principal_id="hermetic-principal",
            actor_id="hermetic-principal",
            operation_profile=policy_reference(),
            origin=PlanEffectOrigin(
                kind="PLAN_EFFECT_PUBLICATION",
                proposal=reference(proposal_id, binding.proposal.canonical_bytes()),
                planning_predecessor=reference("planning-predecessor", runtime._snapshot()),
                proposed_planning_revision=planning_revision,
                original_planning_request=base64.b64decode(
                    binding.planning_request_base64, validate=True
                ),
                original_planning_result=planning_result,
            ),
            planning_revision=planning_revision,
            preexisting_authority_basis=basis,
            authority_sources=(basis, plan_scope, policy_reference()),
            affected_party_constraints=(
                reference("self-only", canonical(["hermetic-principal", "hermetic-account"])),
            ),
            normative_conflict_generation=normative_generation(captured.history, None),
            dependencies=(),
            factual_assertion_evidence=(),
            verification_contradiction=(),
            authority_applicability=(policy_reference(),),
            consequence_scope=reference("opaque-self-emission", payload),
            communication_mandate=reference("opaque-self-payload", payload),
            disclosure_projection=reference("exact-payload-only", payload),
            channel_class="HERMETIC_SELF",
            interaction_context=reference("original-proposal", original.canonical_command.encode()),
            recipient=resources.recipient(captured.resources),
            payload=payload,
            effect_fingerprint=digest(payload),
            bundle_members=tuple(
                reference("member/" + str(n), name.encode())
                for n, name in enumerate(binding.proposal.bundle_members)
            ),
            idempotency_fence_key=display_id + "/single-effect",
            horizon=MandateHorizon(
                clock_contract=CLOCK,
                clock_epoch=captured.resources.clock_epoch,
                not_before_ns=captured.observed_time_ns,
                expires_at_ns=captured.observed_time_ns + 60_000_000_000,
                continuity_policy=policy_reference(),
            ),
            semantics=SEMANTICS,
        )
        precursor = DispatchPrecursorRequestV2(
            schema_id="chiplog.effects.dispatch-precursor-request.v2",
            request_id=display_id + "/precursor",
            mandate_digest=digest(mandate.canonical_bytes()),
            interpretation_policy=policy_reference(),
            preexisting_source_heads=mandate.authority_sources,
        )
        prepared = PrecursorPreparationV2(
            schema_id="chiplog.effects.precursor-preparation.v2",
            request=precursor,
            mandate=mandate,
        )
    sent, raw = await owner_call(
        runtime,
        "effects.evaluate_dispatch_mandate_v2",
        prepared.schema_id,
        prepared.canonical_bytes(),
    )
    result = DispatchPrecursorResultV2.model_validate_json(raw)
    with runtime._authority_gate().hold():
        if capture_dispatch(runtime, resources, observed, original.run_id) != captured:
            raise LoopRejected("dispatch preview sources changed during precursor evaluation")
        preview = DispatchPreview(
            schema_id="chiplog.effects.dispatch-preview.v2",
            original_display_id=original.display_id,
            original_display_digest=original.display_digest,
            original_adoption_act_id=original.adoption_act_id,
            worker_run_id=original.run_id,
            mandate=mandate,
            precursor_request=precursor,
            precursor_result=result,
            precursor_invocation=retained_canonical((sent, raw)),
        )
        rendered = canonical(
            {
                "operation": "ADOPT_EXACT_SELF_ONLY_SEND_V2",
                "mandate": mandate.model_dump(mode="json"),
                "consequence": "Create this exact Plan revision and emit this payload once. "
                "A lost response remains unknown; no blind retry or replacement is authorized.",
            }
        )
        display = ProposalDisplay(
            display_id=display_id,
            proposal_id=proposal_id,
            revision=original.revision + 1,
            run_id=original.run_id,
            tenant=original.tenant,
            principal=original.principal,
            adoption_act_id=display_id + "/adopt",
            canonical_command=preview.canonical_bytes().decode(),
            display_text=rendered.decode(),
            display_digest=digest(rendered),
            original_binding_base64=base64.b64encode(preview.canonical_bytes()).decode(),
        )
        runtime._append_decision(
            {
                "version": 1,
                "kind": "DISPLAY",
                "operation_id": display_id,
                "display": display.model_dump_json(),
            }
        )
        return display


def initial_intent(
    preview: DispatchPreview,
    display: ProposalDisplay,
    observed: ObservedTrustCall,
    captured: DispatchCapture,
) -> ExternalActionIntentV2:
    adoption_raw = canonical(
        {
            "act": display.adoption_act_id,
            "display": display.display_id,
            "display_digest": display.display_digest,
            "mandate_digest": digest(preview.mandate.canonical_bytes()),
            "principal_reference": json.loads(observed.result.reference_bytes or b"null"),
        }
    )
    adoption = DispatchAdoptionV2(
        schema_id="chiplog.effects.dispatch-adoption.v2",
        adoption_act_id=display.adoption_act_id,
        display=ExactHead(
            subject_id=display.display_id,
            head=display.display_id,
            fingerprint=display.display_digest,
        ),
        display_bytes=display.display_text.encode(),
        mandate_bytes=preview.mandate.canonical_bytes(),
        ingress=reference("adoption/" + display.adoption_act_id, adoption_raw),
        ingress_bytes=adoption_raw,
    )
    acquisition = DispatchAcquisitionV2(
        schema_id="chiplog.effects.dispatch-acquisition.v2",
        adoption=adoption,
        authenticated_invocation=retained_canonical(observed),
        precursor_request=preview.precursor_request,
        precursor_result=preview.precursor_result,
        original_sources=source_inventory(
            captured, preview.mandate, adoption.canonical_bytes(), None
        ),
    )
    body = {
        "schema_id": "chiplog.effects.external-action-intent.v2",
        "intent_id": "dispatch-intent/" + digest(display.adoption_act_id.encode()),
        "mandate": preview.mandate.model_dump(mode="json"),
        "acquisition": acquisition.model_dump(mode="json"),
    }
    fingerprint = digest(b"chiplog.effects.intent.v2\x00" + canonical(body))
    return ExternalActionIntentV2.model_validate_json(
        canonical({**body, "fingerprint": fingerprint})
    )


def _record_bytes(record: DispatchRecordV2) -> OwnerRecordBytes:
    raw = record.canonical_bytes()
    return OwnerRecordBytes(
        owner="effects",
        record_kind="effects." + record.kind,
        record_id=record.record.head,
        schema_id=record.schema_id,
        canonical_bytes=raw,
        fingerprint=digest(raw),
    )


async def publish_dispatch(
    runtime: R16DispatchRuntime,
    peer: str,
    *,
    act_id: str,
    worker_run_id: str,
    operation: Literal["PUBLISH", "AUTHORIZE", "COMMIT_FIRST"],
    subject_id: str,
    display_digest: str | None = None,
) -> BrokerPublicationResult:
    from chiplog.composition.r16_dispatch_authority import (
        DispatchIssuance,
        DispatchPublicationAuthority,
        validate_issuance,
    )

    observed = await authenticate(runtime, peer)
    authority = DispatchPublicationAuthority(runtime, observed)
    coordinator = BrokerPublicationCoordinator(
        runtime._appender, authority, runtime._owner_decisions()
    )
    command_id = "dispatch-v2/" + digest(
        canonical([runtime._tenant_id, "hermetic-principal", act_id])
    )
    with runtime._authority_gate().hold():
        selected = runtime._owner_decisions().lookup(runtime._tenant_id, command_id)
        if selected is not None:
            original = validate_issuance(selected.prepared.request, runtime)
            command = original.request.command
            expected_operation = (
                "PUBLISH"
                if isinstance(command, PublishDispatchIntentV2)
                else ("AUTHORIZE" if isinstance(command, AuthorizeDispatchV2) else "COMMIT_FIRST")
            )
            original_subject = (
                command.intent.acquisition.adoption.display.subject_id
                if isinstance(command, PublishDispatchIntentV2)
                else command.intent.subject_id
            )
            if (
                expected_operation != operation
                or original_subject != subject_id
                or (
                    isinstance(command, PublishDispatchIntentV2)
                    and command.intent.acquisition.adoption.display.fingerprint != display_digest
                )
            ):
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=command_id,
                    reason="act ID already names different exact dispatch inputs",
                )
            batch = selected.prepared.request
            result = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=batch.identity,
                    operation=batch.operation,
                    current_invocation=authority.invocation(batch.identity),
                    original_commands=source_commands(batch),
                )
            )
            if isinstance(result, NoSelectedDecision):
                raise LoopRejected("selected dispatch disappeared")
            if not isinstance(result, PublicationRejected) or result.kind != "HOLD":
                return result
    if selected is not None:
        return await coordinator.recover_selected(runtime._tenant_id, command_id)

    companions: tuple[OwnerRecordBytes, ...] = ()
    planning_command: OwnerCommandBytes | None = None
    if operation == "PUBLISH":
        matches = tuple(d for d in runtime._displays() if d.display_id == subject_id)
        if (
            len(matches) != 1
            or matches[0].display_digest != display_digest
            or matches[0].adoption_act_id != act_id
        ):
            raise LoopRejected("explicit adoption differs from exact v2 preview")
        display = matches[0]
        preview = DispatchPreview.model_validate_json(display.canonical_command)
        if (
            preview.worker_run_id != worker_run_id
            or digest(display.display_text.encode()) != display_digest
        ):
            raise LoopRejected("v2 display identity or worker differs")
        with runtime._authority_gate().hold():
            require_dispatch_scope(
                authority.capture(worker_run_id),
                runtime._dispatch_resources,
                preview.mandate,
                None,
                first_send=False,
            )
        planning = await R16EffectsProducer(runtime).adopt_effect(
            peer,
            preview.original_display_id,
            preview.original_display_digest,
            preview.original_adoption_act_id,
        )
        companions = planning_records(planning)
        planning_command = OwnerCommandBytes(
            owner="planning",
            schema_id="chiplog.planning.public.create.v2",
            canonical_bytes=planning.planning.request_bytes,
            fingerprint=digest(planning.planning.request_bytes),
        )
    with runtime._authority_gate().hold():
        captured = authority.capture(worker_run_id)
        worker = captured.cut.worker
        if worker is None:
            raise LoopRejected("dispatch current worker absent")
        previous: DispatchRecordV2 | None = None
        if operation == "PUBLISH":
            intent = initial_intent(preview, display, observed, captured)
            if any(
                member.intent.subject_id == intent.intent_id for member in captured.history.members
            ):
                raise LoopRejected("v2 intent already exists under another adoption act")
            manifest = (
                *(
                    ExactHead(
                        subject_id=row.record_id, head=row.record_id, fingerprint=row.fingerprint
                    )
                    for row in companions
                ),
                reference(intent.intent_id, intent.canonical_bytes()),
            )
            command = PublishDispatchIntentV2(
                schema_id="chiplog.effects.publish-dispatch-intent.v2",
                identity=CommandIdentity(
                    command_id=command_id,
                    fingerprint=digest(intent.canonical_bytes()),
                    expected_tenant_head=captured.cut.tenant_frontier,
                ),
                intent=intent,
                complete_publication_manifest=manifest,
                fence=worker.fence,
            )
        else:
            matches_v2 = tuple(
                row
                for row in captured.history.v2_records
                if row.snapshot.intent.intent_id == subject_id
            )
            if not matches_v2:
                raise LoopRejected("v2 intent not found in complete current history")
            if not isinstance(matches_v2[-1], DispatchRecordV2):
                raise LoopRejected("outcome stream cannot authorize another first send")
            previous = matches_v2[-1]
            intent = previous.snapshot.intent
            common = dict(
                identity=CommandIdentity(
                    command_id=command_id,
                    fingerprint=digest(
                        canonical(
                            [
                                operation,
                                intent.intent_id,
                                previous.snapshot.attempt.model_dump(mode="json"),
                            ]
                        )
                    ),
                    expected_tenant_head=captured.cut.tenant_frontier,
                ),
                intent=reference(intent.intent_id, intent.canonical_bytes()),
                expected_attempt=previous.snapshot.attempt,
                immutable_mandate=reference(
                    intent.mandate.mandate_id, intent.mandate.canonical_bytes()
                ),
                semantics=intent.mandate.semantics,
                fence=worker.fence,
            )
            if operation == "AUTHORIZE":
                command = AuthorizeDispatchV2.model_validate(
                    {"schema_id": "chiplog.effects.authorize-dispatch.v2", **common}
                )
            else:
                if not previous.snapshot.authorizations:
                    raise LoopRejected("first send lacks latest authorization")
                command = CommitFirstSendV2.model_validate(
                    {
                        "schema_id": "chiplog.effects.commit-first-send.v2",
                        **common,
                        "authorization": previous.snapshot.authorizations[-1].authorization,
                        "ordinal": 0,
                    }
                )
        require_dispatch_scope(
            captured,
            runtime._dispatch_resources,
            intent.mandate,
            None if previous is None else intent,
            first_send=operation == "COMMIT_FIRST",
        )
        current = current_inputs(
            captured,
            command.canonical_bytes(),
            intent,
            original_adoption_bytes=intent.acquisition.adoption.canonical_bytes(),
        )
        request = DispatchPreparationV2(
            schema_id="chiplog.effects.dispatch-preparation.v2",
            command=command,
            previous=previous,
            current=current,
        )
    sent, output = await owner_call(
        runtime, "effects.prepare_dispatch_v2", request.schema_id, request.canonical_bytes()
    )
    record = DispatchRecordV2.model_validate_json(output)
    with runtime._authority_gate().hold():
        issuance = DispatchIssuance(
            schema_id="chiplog.effects.dispatch-issuance.v2",
            captured=captured,
            observed=observed,
            sent=sent,
            request=request,
            record=record,
        )
        records = (*companions, _record_bytes(record))
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=command_id,
            command_fingerprint=digest(request.canonical_bytes()),
            canonicalization_version="chiplog.owner-publication.v1",
        )
        authentication = WorkerAuthentication(
            invocation=authority.invocation(identity),
            applicability_schema=issuance.schema_id,
            applicability_bytes=issuance.canonical_bytes(),
            applicability_fingerprint=digest(issuance.canonical_bytes()),
        )
        expected = AuthoritativeReadManifest(
            tenant_id=runtime._tenant_id,
            tenant_frontier=captured.cut.tenant_frontier,
            expected_materialization_commitment=captured.cut.materialization_commitment,
            registry_head=policy_reference().head,
            registry_fingerprint=policy_reference().fingerprint,
            ordered_heads=(
                ObservedPresence(
                    head=ExactRecordHead(
                        owner="agent_loop",
                        record_kind="Run",
                        subject_id=worker.run.run_id,
                        record_id=worker.run.head,
                        fingerprint=digest(worker.run.canonical_bytes()),
                    )
                ),
            ),
            complete_manifest_fingerprint=digest(retained_canonical(captured)),
        )
        source = OwnerCommandBytes(
            owner="effects",
            schema_id=request.schema_id,
            canonical_bytes=request.canonical_bytes(),
            fingerprint=digest(request.canonical_bytes()),
        )
        if planning_command is not None:
            batch = PlanEffectBatch(
                identity=identity,
                authentication=authentication,
                expected=expected,
                planning_command=planning_command,
                effects_command=source,
                complete_records=records,
                complete_batch_fingerprint=digest(retained_canonical(records)),
            )
        else:
            batch = SingleOwnerBatch(
                operation="effects.authorize"
                if operation == "AUTHORIZE"
                else "effects.commit_send",
                identity=identity,
                authentication=authentication,
                expected=expected,
                command=source,
                complete_records=records,
                complete_batch_fingerprint=digest(retained_canonical(records)),
            )
        authority.register(batch, issuance)
    return await coordinator.commit(batch)
