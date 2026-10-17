"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

import hashlib

from tests.support.capabilities.agent_loop.execution_first_path_completion_contracts import (
    request as first_path_request,
)
from tests.support.h1_selected_scope_source import build_h1_selected_scope_source

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
from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.contracts import ExactHead as EffectHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryMemberV1,
    H1EffectsHistoryV1,
    h1_effects_history_capture,
    h1_effects_history_digest,
    h1_effects_history_head,
    h1_normative_conflict_generation,
    h1_normative_conflict_generation_capture,
)
from chiplog.capabilities.effects.h1_producer_semantics import (
    H1_PRODUCER_SEMANTICS,
    h1_producer_semantic_registry_capture,
)
from chiplog.capabilities.effects.h1_producer_source_contracts import (
    H1PreparedDeliveryMandateCandidateV1,
    H1ProducerCurrentInputsV1,
    H1ProducerNotApplicableV1,
    H1ProducerSourceInventoryV1,
    H1ProducerSourceObservationV1,
)
from chiplog.capabilities.effects.h1_scoped_preparation import (
    _effect_head,
    _loop_head,
    _run_head,
    derive_h1_scoped_delivery,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryAuthorityEvidenceV1,
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRouteV1,
    PrepareH1ScopedDeliveryV1,
)
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    make_scoped_intent_retained_exchange,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(name: str, raw: bytes = b"h1") -> LoopHead:
    return LoopHead(identity=name, head=name + ":head", fingerprint=_sha(raw))


def _captured_source(role: str, *, valid_until_ns: int) -> CapturedSource:
    if role == "semantic_registry":
        return h1_producer_semantic_registry_capture(
            clock_contract="clock-contract", clock_epoch="epoch", valid_until_ns=valid_until_ns
        )
    raw = f"h1-source:{role}".encode()
    head = EffectHead(
        subject_id=f"h1-source:{role}",
        head=f"h1-source:{role}:head",
        fingerprint=_sha(raw),
    )
    return CapturedSource(
        source_id=f"h1-source:{role}",
        source_version="chiplog.h1-source.v1",
        owner_id="broker-reader",
        reader_id="broker-reader",
        invalidation_manifest=head,
        head=head,
        canonical_value=raw,
        clock_contract="clock-contract",
        clock_epoch="epoch",
        valid_until_ns=valid_until_ns,
    )


def _h1_sources(
    candidate: H1PreparedDeliveryMandateCandidateV1,
    history: H1EffectsHistoryV1,
    *,
    valid_until_ns: int = 175,
) -> H1ProducerSourceInventoryV1:
    values: dict[str, CapturedSource | H1ProducerSourceObservationV1] = {
        role: _captured_source(role, valid_until_ns=valid_until_ns)
        for role in (
            "trust",
            "planning",
            "semantic_registry",
            "original_adoption",
            "runtime_and_fence",
            "endpoint",
            "credential_lifecycle",
            "deployment_entitlement",
            "clock",
        )
    }
    generation = h1_normative_conflict_generation(history)
    values["effects_history"] = h1_effects_history_capture(
        history,
        clock_contract="clock-contract",
        clock_epoch="epoch",
        valid_until_ns=valid_until_ns,
    )
    values["normative_conflict_generation"] = h1_normative_conflict_generation_capture(
        generation,
        clock_contract="clock-contract",
        clock_epoch="epoch",
        valid_until_ns=valid_until_ns,
    )
    values["planning"] = H1ProducerNotApplicableV1(role="planning", candidate=candidate)
    values["original_adoption"] = H1ProducerNotApplicableV1(
        role="original_adoption", candidate=candidate
    )
    return H1ProducerSourceInventoryV1.model_validate(values)


async def _call(*, selected_intent_id: str | None = None) -> H1ScopedDeliveryOwnerCallV1:
    first = await first_path_request(canonical_response=True)
    prepared = prepare_first_path_execution_completion(first)
    members = ()
    if selected_intent_id is not None:
        raw = b"selected-history-member"
        members = (
            H1EffectsHistoryMemberV1(
                selected_decision=EffectHead(
                    subject_id="selected-decision",
                    head="selected-decision/head",
                    fingerprint=_sha(b"selected-decision"),
                ),
                tenant_commit_sequence=0,
                publication_ordinal=0,
                record_id="selected-record",
                record_kind="effects.INTENT_RECORDED",
                schema_id="chiplog.effects.record.v1",
                fingerprint=_sha(raw),
                canonical_record_bytes=raw,
                intent_id=selected_intent_id,
            ),
        )
    history = H1EffectsHistoryV1(
        tenant_id=first.run.tenant,
        owner_journal_head=None,
        ordered_members=members,
        digest=h1_effects_history_digest(first.run.tenant, None, members),
    )
    delivery = prepared.delivery.manifest.ordered_deliveries[0]
    basis = __import__(
        "chiplog.capabilities.effects.h1_prepared_delivery_basis", fromlist=["x"]
    ).derive_h1_prepared_delivery_basis(first, prepared)
    scope = build_h1_selected_scope_source()
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
    history_capture = h1_effects_history_capture(
        history,
        clock_contract="clock-contract",
        clock_epoch="epoch",
        valid_until_ns=175,
    )
    generation_capture = h1_normative_conflict_generation_capture(
        h1_normative_conflict_generation(history),
        clock_contract="clock-contract",
        clock_epoch="epoch",
        valid_until_ns=175,
    )
    derivation = derive_h1_scoped_delivery(
        basis=basis,
        original_run=_run_head(first.run),
        captured_attempt=_loop_head(first.selected_attempt),
        delivery=delivery,
        evidence=evidence,
        grant=grant,
        policy=policy,
        # The fixture can deliberately build a self-consistent invalid owner
        # request; the owner must reject its actual intent against history.
        target_intent_id="fixture-intent",
        original_history=history_capture,
        current_history=history_capture,
        original_generation=generation_capture,
        current_generation=generation_capture,
        history_observation=h1_effects_history_head(history),
        clock_contract="clock-contract",
        clock_epoch="epoch",
        valid_until_ns=175,
    )
    candidate = derivation.candidate
    derived = derivation.mandate
    record = derivation.policy_record
    grant_head = _effect_head(grant_anchor.grant)
    policy_effect_head = _effect_head(policy_anchor.policy)
    original_sources = _h1_sources(candidate, history)
    current = H1ProducerCurrentInputsV1(
        command_fingerprint=_sha(b"command"),
        immutable_mandate_candidate=candidate,
        physical_cut=EffectHead(
            subject_id="h1-current-cut",
            head="h1-current-cut:head",
            fingerprint=_sha(b"h1-current-cut"),
        ),
        history_observation=h1_effects_history_head(history),
        sources=_h1_sources(candidate, history),
        supported_semantics=H1_PRODUCER_SEMANTICS,
        clock_contract="clock-contract",
        clock_epoch="epoch",
        observed_time_ns=150,
        lease_expires_at_ns=175,
    )
    precursor = derivation.precursor_request
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
        original_sources=original_sources,
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
