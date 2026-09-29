"""H1 scoped producer checks use a real loop completion and owner envelope."""

from __future__ import annotations

import base64
import hashlib

from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as first_path_request,
)
from tests.capabilities.effects.test_h1_local_preparation_record_contracts import _scope_source
from tests.capabilities.effects.test_scoped_intent_contracts import prepared_acceptance

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.deployment_trust import (
    prepared_external_delivery_policy_owner_contracts as policy_owner,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    BoundedExternalSelfSendMandateV1,
    ExternalDeliveryResourcesV1,
    PreparedExternalDeliveryGrantAnchorV2,
    PreparedExternalDeliveryGrantScopeV2,
    PreparedExternalDeliveryGrantV2,
    SelectedExternalDeliverySourceV1,
    prepared_external_delivery_grant_content_head_v2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    prepared_delivery_basis_head_v3,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
    PreparedExternalSelfDeliveryPolicyTermsV1,
    PreparedExternalSelfDeliveryPolicyV1,
)
from chiplog.capabilities.effects._h1_scoped_process import ROUTE, ROUTES, dispatch
from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from chiplog.capabilities.effects.h1_local_preparation_contracts import PREPARE_SCHEMA
from chiplog.capabilities.effects.h1_scoped_preparation import (
    _delivery_binding,
    _effect_head,
    _loop_head,
    _policy_record,
    _run_head,
    prepare_h1_scoped_delivery,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryAuthorityEvidenceV1,
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRouteV1,
    PreparedH1ScopedDeliveryV1,
    PrepareH1ScopedDeliveryV1,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    DispatchMandateV3,
    MandateHorizon,
    PreparedDeliveryOriginV3,
    ScopedPrecursorRequest,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    make_scoped_intent_retained_exchange,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(name: str, raw: bytes = b"h1") -> LoopHead:
    return LoopHead(identity=name, head=name + ":head", fingerprint=_sha(raw))


async def _call() -> H1ScopedDeliveryOwnerCallV1:
    first = await first_path_request(canonical_response=True)
    prepared = prepare_first_path_execution_completion(first)
    delivery = prepared.delivery.manifest.ordered_deliveries[0]
    basis = __import__(
        "chiplog.capabilities.effects.h1_prepared_delivery_basis", fromlist=["x"]
    ).derive_h1_prepared_delivery_basis(first, prepared)
    scope = _scope_source()
    recipient = delivery.selection.recipient
    terms = PreparedExternalSelfDeliveryPolicyTermsV1(
        principal_id=first.run.principal,
        channel_id="channel",
        recipient=recipient,
        communication_permission="PREPARED_EXTERNAL_SEND",
        disclosure_permission="EXACT_RENDERED_PAYLOAD",
        self_recipient_semantics="OPERATOR_ATTESTED_SELF",
        payload_class="NonAuthoritativeText",
        payload_digest=_sha(delivery.rendered_bytes),
        payload_byte_length=len(delivery.rendered_bytes),
        source_classes=("CLI",),
        selection_modes=("ORIGIN_EXACT",),
        external_delivery=True,
        max_calls=1,
        clock_contract="clock-contract",
        clock_epoch="epoch",
        not_before_ns=100,
        expires_at_ns=200,
    )
    policy = PreparedExternalSelfDeliveryPolicyV1(
        issuer="deployment_trust",
        tenant_id=first.run.tenant,
        database_id=first.source.database_id,
        policy_id="policy",
        revision=0,
        predecessor=None,
        status="ACTIVE",
        terms=terms,
        authorization_command=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="policy-command", head="policy-command:head", fingerprint=_sha(b"pc")),
        authorization_source=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="policy-source", head="policy-source:head", fingerprint=_sha(b"ps")),
    )
    policy_raw = policy.canonical_bytes()
    policy_anchor = PreparedExternalSelfDeliveryPolicyAnchorV1(
        owner_id="deployment_trust",
        decision=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(
            identity="policy-decision", head="policy-decision:head", fingerprint=_sha(b"pd")
        ),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_self_delivery_policy",
        schema_id="chiplog.deployment_trust.record.v1",
        record=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="policy-record", head="policy-record:head", fingerprint=_sha(b"pr")),
        policy=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(
            identity=policy.policy_id,
            head=policy_owner.prepared_self_delivery_policy_content_head(policy_raw),
            fingerprint=_sha(policy_raw),
        ),
        revision=0,
    )
    policy_head = policy_anchor.policy
    grant = PreparedExternalDeliveryGrantV2(
        issuer="deployment_trust",
        tenant_id=first.run.tenant,
        database_id=first.source.database_id,
        grant_id="grant",
        revision=0,
        predecessor=None,
        status="ACTIVE",
        selected_policy_anchor=policy_anchor,
        scope=PreparedExternalDeliveryGrantScopeV2(
            principal_id=first.run.principal,
            worker_session_id=first.run.worker_session,
            contour_head="contour",
            authenticated_credential_head="credential",
            authenticated_session_head="session",
            selected_source=SelectedExternalDeliverySourceV1(
                source_class="CLI",
                selected_initialization=scope.scope.selected_resource_observation_ref.selected_initialization,
                selected_admission_decision=_head("admission-decision"),
                selected_admission_record=LoopHead(
                    identity="admitted",
                    head="admitted/" + _sha(b"record"),
                    fingerprint=_sha(b"record"),
                ),
                admitted_authentication=scope.scope.admitted_authentication,
                ingress_binding=delivery.selection.ingress_binding,
            ),
            resources=ExternalDeliveryResourcesV1(
                signature_domain="dispatch-resources.v1",
                signed_observation_fingerprint=scope.scope.selected_resource_observation_ref.signed_observation_fingerprint,
                resource_grant=_head("resource-grant"),
                recipient=recipient,
                clock_epoch="epoch",
            ),
            mandate=BoundedExternalSelfSendMandateV1(
                purpose="PREPARED_EXTERNAL_SELF_SEND",
                external_delivery=True,
                selection="ORIGIN_EXACT",
                payload_class="NonAuthoritativeText",
                communication_authority=policy_head,
                disclosure_authority=policy_head,
                self_recipient_binding=policy_head,
                original_run=__import__(
                    "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
                ).ExactHead(
                    identity=first.run.run_id, head=first.run.head, fingerprint=first.run.digest()
                ),
                captured_attempt=LoopHead(
                    identity=first.selected_attempt.subject_id,
                    head=first.selected_attempt.revision.head,
                    fingerprint=first.selected_attempt.revision.fingerprint,
                ),
                preparation_basis=prepared_delivery_basis_head_v3(basis),
                delivery_id=delivery.delivery_id,
                payload_digest=_sha(delivery.rendered_bytes),
                payload_byte_length=len(delivery.rendered_bytes),
                max_calls=1,
                clock_contract="clock-contract",
                clock_epoch="epoch",
                not_before_ns=100,
                expires_at_ns=200,
            ),
        ),
        authorization_command=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="grant-command", head="grant-command:head", fingerprint=_sha(b"gc")),
        authorization_source=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="grant-source", head="grant-source:head", fingerprint=_sha(b"gs")),
    )
    grant_raw = grant.canonical_bytes()
    grant_anchor = PreparedExternalDeliveryGrantAnchorV2(
        owner_id="deployment_trust",
        decision=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="grant-decision", head="grant-decision:head", fingerprint=_sha(b"gd")),
        record_ordinal=0,
        record_type_id="chiplog.deployment_trust.prepared_external_delivery_grant",
        schema_id="chiplog.deployment_trust.record.v1",
        record=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(identity="grant-record", head="grant-record:head", fingerprint=_sha(b"gr")),
        grant=__import__(
            "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
        ).ExactHead(
            identity=grant.grant_id,
            head=prepared_external_delivery_grant_content_head_v2(grant_raw),
            fingerprint=_sha(grant_raw),
        ),
        revision=0,
    )
    evidence = H1ScopedDeliveryAuthorityEvidenceV1(
        grant_anchor=grant_anchor,
        canonical_grant_bytes=grant_raw,
        policy_anchor=policy_anchor,
        canonical_policy_bytes=policy_raw,
        trust_observation=scope.current_request.expected_trust_observation,
    )
    record = _policy_record(policy, policy_raw, evidence)
    current = prepared_acceptance().effects_request.current.model_copy(
        update={
            "command_fingerprint": _sha(b"command"),
            "clock_contract": "clock-contract",
            "clock_epoch": "epoch",
            "observed_time_ns": 150,
        }
    )
    grant_head = _effect_head(grant_anchor.grant)
    policy_effect_head = _effect_head(policy_anchor.policy)
    command_head = _effect_head(grant.authorization_command)
    source_head = _effect_head(grant.authorization_source)
    derived = DispatchMandateV3(
        mandate_id="h1-scoped-mandate:"
        + _sha((grant.grant_id + "\x00" + delivery.delivery_id).encode()),
        tenant_id=grant.tenant_id,
        principal_id=grant.scope.principal_id,
        actor_id=grant.scope.principal_id,
        operation_profile=policy_effect_head,
        origin=PreparedDeliveryOriginV3(
            original_run=_run_head(first.run),
            captured_attempt=_loop_head(first.selected_attempt),
            binding=_delivery_binding(delivery),
            preparation_basis=_effect_head(prepared_delivery_basis_head_v3(basis)),
        ),
        planning_revision=policy_effect_head,
        preexisting_authority_basis=grant_head,
        authority_sources=(policy_effect_head, command_head, source_head),
        affected_party_constraints=(),
        normative_conflict_generation=policy_effect_head,
        dependencies=(),
        factual_assertion_evidence=(source_head,),
        verification_contradiction=(),
        authority_applicability=(policy_effect_head,),
        consequence_scope=grant_head,
        communication_mandate=policy_effect_head,
        disclosure_projection=policy_effect_head,
        channel_class="channel",
        interaction_context=grant_head,
        recipient=_delivery_binding(delivery).selection.recipient,
        payload=delivery.rendered_bytes,
        effect_fingerprint=_sha(delivery.rendered_bytes),
        bundle_members=(grant_head,),
        idempotency_fence_key="grant:" + delivery.delivery_id,
        horizon=MandateHorizon(
            clock_contract="clock-contract",
            clock_epoch="epoch",
            not_before_ns=100,
            expires_at_ns=200,
            continuity_policy=policy_effect_head,
        ),
        semantics=current.supported_semantics,
    )
    precursor = ScopedPrecursorRequest(
        request_id="h1-scoped-precursor:"
        + _sha(
            b'["chiplog.effects.h1-scoped-precursor.v1","grant","'
            + delivery.delivery_id.encode()
            + b'"]'
        ),
        mandate=derived,
        interpretation_policy=record,
        preexisting_sources=(record,),
    )
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id=first.run.run_id,
        run_head=first.run.head,
        worker_session_id=first.run.worker_session,
        runtime_generation="generation",
    )
    inner = PrepareH1ScopedDeliveryV1(
        identity=CommandIdentity(
            command_id="command", fingerprint=_sha(b"command"), expected_tenant_head=7
        ),
        intent_id="intent",
        expected_intent=__import__("chiplog.capabilities.effects.fences", fromlist=["x"]).Absent(),
        original_completion_request=first,
        prepared_completion=prepared,
        selected_scope=scope,
        retained_origin=H1RetainedSelectedWrapperV1(
            initialization_envelope_bytes=b"initialization",
            admitted_record_bytes=b"record",
            selected_admitted_record_ref=__import__(
                "chiplog.capabilities.agent_loop.delivery_contracts", fromlist=["x"]
            ).ExactHead(
                identity="admitted", head="admitted/" + _sha(b"record"), fingerprint=_sha(b"record")
            ),
            authentication_result_bytes=b"authentication",
            admitted_record_digest=_sha(b"record"),
        ),
        fence=fence,
        authority_evidence=evidence,
        precursor_request=precursor,
        preexisting_communication_authority=record,
        current_disclosure_authority=record,
        original_sources=prepared_acceptance().effects_proposal.snapshot.intent.acquisition.original_sources,
        current=current,
        complete_current_origin_sources=(record,),
        retained_sources=(),
    )
    # Build the exact fixed retained-source projection from the same owner inputs.
    acquisition = __import__("chiplog.capabilities.effects.scoped_intent_contracts", fromlist=["x"])
    unsigned = acquisition.ExternalActionIntentV3(
        intent_id=inner.intent_id,
        fingerprint="0" * 64,
        mandate=derived,
        acquisition=acquisition.ScopedDispatchAcquisition(
            authority=acquisition.PreparedDeliveryAuthority(
                basis=basis,
                preexisting_communication_authority=record,
                current_disclosure_authority=record,
                exact_mandate_bytes=derived.canonical_bytes(),
            ),
            precursor_request=precursor,
            precursor_result=acquisition.ScopedPrecursorResult(
                source_request_fingerprint=_sha(precursor.canonical_bytes()),
                mandate_fingerprint=_sha(derived.canonical_bytes()),
                interpretation_policy=record.head,
                complete_evaluation_evidence=(
                    grant_head,
                    policy_effect_head,
                    _effect_head(policy_anchor.decision),
                ),
            ),
            original_sources=inner.original_sources,
        ),
    )
    from chiplog.capabilities.effects.scoped_intent_record_contracts import (
        scoped_intent_fingerprint,
    )

    exact_intent = unsigned.model_copy(update={"fingerprint": scoped_intent_fingerprint(unsigned)})
    publication = acquisition.PrepareScopedIntentPublication(
        identity=inner.identity,
        intent=exact_intent,
        expected_intent=inner.expected_intent,
        current=inner.current,
        complete_current_origin_sources=inner.complete_current_origin_sources,
        fence=inner.fence,
    )
    inner = inner.model_copy(
        update={
            "retained_sources": make_scoped_intent_retained_exchange(publication).original_sources
        }
    )
    return H1ScopedDeliveryOwnerCallV1(
        route=H1ScopedDeliveryRouteV1(
            tenant_id=first.run.tenant,
            database_id=first.source.database_id,
            worker_session_id=first.run.worker_session,
            broker_epoch=0,
            runtime_generation="generation",
            broker_session_id="broker",
            owner_session_id="effects",
            request_id="command",
        ),
        request=inner,
        request_digest=_sha(inner.canonical_bytes()),
    )


async def test_scoped_owner_derives_canonical_result_and_rejects_mutated_evidence() -> None:
    call = await _call()
    result = prepare_h1_scoped_delivery(call)
    assert isinstance(result, PreparedH1ScopedDeliveryV1)
    assert result.canonical_bytes() == prepare_h1_scoped_delivery(call).canonical_bytes()

    raw = bytearray(call.canonical_bytes())
    raw[0] = ord("[")
    # The mounted process uses this exact canonical bytes boundary; malformed
    # owner bytes cannot enter as a substituted request.
    assert (
        prepare_h1_scoped_delivery(call.model_copy(update={"request_digest": "0" * 64})).disposition
        == "DENIED"
    )

    changed_policy = call.request.authority_evidence.model_copy(
        update={"canonical_policy_bytes": b"{}"}
    )
    denied = prepare_h1_scoped_delivery(
        call.model_copy(
            update={
                "request": call.request.model_copy(update={"authority_evidence": changed_policy})
            }
        )
    )
    assert denied.disposition == "DENIED"


async def test_scoped_process_accepts_only_canonical_owner_envelopes() -> None:
    call = await _call()
    response = dispatch(ROUTE[0], call.canonical_bytes())
    assert response["schema_id"] == "chiplog.effects.prepared-h1-scoped-delivery.v1"
    result = PreparedH1ScopedDeliveryV1.model_validate_json(base64.b64decode(response["payload"]))
    assert result.canonical_bytes() == base64.b64decode(response["payload"])
    assert dispatch(ROUTE[0], call.canonical_bytes() + b" ")["failure"] == "PROTOCOL_REJECTED"

    changed_basis = call.request.prepared_completion.model_copy(
        update={"source_request_fingerprint": "0" * 64}
    )
    denied = prepare_h1_scoped_delivery(
        call.model_copy(
            update={
                "request": call.request.model_copy(update={"prepared_completion": changed_basis})
            }
        )
    )
    assert denied.disposition == "DENIED"

    denied = prepare_h1_scoped_delivery(
        call.model_copy(
            update={
                "request": call.request.model_copy(
                    update={
                        "complete_current_origin_sources": (
                            call.request.preexisting_communication_authority,
                            call.request.current_disclosure_authority,
                        )
                    }
                )
            }
        )
    )
    assert denied.disposition == "DENIED"


def test_scoped_aggregate_preserves_h1_local_route_and_handler() -> None:
    local_route = (
        "effects.prepare_h1_local_commentary",
        "broker",
        "effects",
        "chiplog.effects.h1-local-commentary-owner-call.v1",
        "chiplog.effects.prepared-h1-local-commentary.v1",
    )
    assert local_route in ROUTES
    rejected = dispatch(
        "effects.prepare_h1_local_commentary",
        b'{"schema_id":"' + PREPARE_SCHEMA.encode() + b'"}',
    )
    assert rejected["failure"] == "PROTOCOL_REJECTED"
