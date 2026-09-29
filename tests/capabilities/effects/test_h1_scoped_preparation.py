"""H1 scoped producer checks use a real loop completion and owner envelope."""

from __future__ import annotations

import base64
import hashlib

import pytest
from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as first_path_request,
)
from tests.capabilities.effects.test_h1_local_preparation_record_contracts import _scope_source

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
from chiplog.capabilities.effects.contracts import ExactHead as EffectHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from chiplog.capabilities.effects.h1_local_preparation_contracts import PREPARE_SCHEMA
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
    H1ScopedDeliveryDerivationV1,
    _effect_head,
    _loop_head,
    _run_head,
    derive_h1_scoped_delivery,
    prepare_h1_scoped_delivery,
)
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryAuthorityEvidenceV1,
    H1ScopedDeliveryOwnerCallV1,
    H1ScopedDeliveryRouteV1,
    PreparedH1ScopedDeliveryV1,
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


def _with_request(
    call: H1ScopedDeliveryOwnerCallV1, request: PrepareH1ScopedDeliveryV1
) -> H1ScopedDeliveryOwnerCallV1:
    return call.model_copy(
        update={"request": request, "request_digest": _sha(request.canonical_bytes())}
    )


def _derive_from_call(
    call: H1ScopedDeliveryOwnerCallV1,
    *,
    grant: PreparedExternalDeliveryGrantV2 | None = None,
    policy: PreparedExternalSelfDeliveryPolicyV1 | None = None,
    history: H1EffectsHistoryV1 | None = None,
) -> H1ScopedDeliveryDerivationV1:
    request = call.request
    evidence = request.authority_evidence
    first = request.original_completion_request
    basis = __import__(
        "chiplog.capabilities.effects.h1_prepared_delivery_basis", fromlist=["x"]
    ).derive_h1_prepared_delivery_basis(first, request.prepared_completion)
    grant = grant or PreparedExternalDeliveryGrantV2.model_validate_json(
        evidence.canonical_grant_bytes
    )
    policy = policy or PreparedExternalSelfDeliveryPolicyV1.model_validate_json(
        evidence.canonical_policy_bytes
    )
    history = history or H1EffectsHistoryV1.model_validate_json(
        request.current.sources.effects_history.canonical_value
    )
    history_capture = h1_effects_history_capture(
        history,
        clock_contract=request.current.clock_contract,
        clock_epoch=request.current.clock_epoch,
        valid_until_ns=request.current.lease_expires_at_ns,
    )
    generation_capture = h1_normative_conflict_generation_capture(
        h1_normative_conflict_generation(history),
        clock_contract=request.current.clock_contract,
        clock_epoch=request.current.clock_epoch,
        valid_until_ns=request.current.lease_expires_at_ns,
    )
    return derive_h1_scoped_delivery(
        basis=basis,
        original_run=_run_head(first.run),
        captured_attempt=_loop_head(first.selected_attempt),
        delivery=request.prepared_completion.delivery.manifest.ordered_deliveries[0],
        evidence=evidence,
        grant=grant,
        policy=policy,
        target_intent_id=request.intent_id,
        original_history=history_capture,
        current_history=history_capture,
        original_generation=generation_capture,
        current_generation=generation_capture,
        history_observation=h1_effects_history_head(history),
        clock_contract=request.current.clock_contract,
        clock_epoch=request.current.clock_epoch,
        valid_until_ns=request.current.lease_expires_at_ns,
    )


async def test_scoped_owner_uses_pure_derivation_and_rejects_stale_authority_proof() -> None:
    call = await _call()
    owner_result = prepare_h1_scoped_delivery(call)
    assert isinstance(owner_result, PreparedH1ScopedDeliveryV1)

    # Compatibility goldens from the isolated pre-change fixture:
    # HEAD b08a44ecc1d6ba0d0f567c9451e0a44fba674339; uv.lock blob
    # 50ba3c14844c84ab2d7821dee0187c7ad9738574; selector is this test's _call().
    assert _sha(owner_result.canonical_bytes()) == (
        "8990f2143e0d0764012f643648e94740749be20a895669306c43e25ef77e8578"
    )
    assert _sha(owner_result.mandate_bytes) == (
        "c1de670e28f20b0d8b2d0826ac711391a3b10e5ce0258831e0baf49a8aa33c2b"
    )
    assert _sha(owner_result.precursor_request.canonical_bytes()) == (
        "7b555b9d4a3f75751ad0bee4330c93d5f542a11d7da3b57ae28a1dbb639956b4"
    )
    derivation = _derive_from_call(call)
    assert owner_result.mandate_bytes == derivation.candidate.canonical_mandate_bytes
    assert owner_result.precursor_request == derivation.precursor_request
    assert owner_result.intent_result.intent.mandate == derivation.mandate

    grant = PreparedExternalDeliveryGrantV2.model_validate_json(
        call.request.authority_evidence.canonical_grant_bytes
    )
    changed_grant = grant.model_copy(update={"grant_id": "changed-grant"})
    with pytest.raises(ValueError, match="differs from closed authority evidence"):
        _derive_from_call(call, grant=changed_grant)
    changed_evidence = call.request.authority_evidence.model_copy(
        update={"canonical_grant_bytes": changed_grant.canonical_bytes()}
    )
    changed_request = call.request.model_copy(update={"authority_evidence": changed_evidence})
    assert prepare_h1_scoped_delivery(_with_request(call, changed_request)).disposition == "DENIED"


async def test_pure_derivation_binds_policy_and_history_generation_changes() -> None:
    call = await _call()
    derivation = _derive_from_call(call)
    policy = PreparedExternalSelfDeliveryPolicyV1.model_validate_json(
        call.request.authority_evidence.canonical_policy_bytes
    )
    changed_policy = policy.model_copy(
        update={"terms": policy.terms.model_copy(update={"channel_id": "changed-channel"})}
    )
    with pytest.raises(ValueError, match="differs from closed authority evidence"):
        _derive_from_call(call, policy=changed_policy)

    alternate_history = H1EffectsHistoryV1(
        tenant_id=call.request.original_completion_request.run.tenant,
        owner_journal_head="alternate-history-head",
        ordered_members=(),
        digest=h1_effects_history_digest(
            call.request.original_completion_request.run.tenant,
            "alternate-history-head",
            (),
        ),
    )
    assert (
        _derive_from_call(call, history=alternate_history).candidate.canonical_mandate_bytes
        != derivation.candidate.canonical_mandate_bytes
    )

    changed_generation = h1_normative_conflict_generation_capture(
        h1_normative_conflict_generation(alternate_history),
        clock_contract=call.request.current.clock_contract,
        clock_epoch=call.request.current.clock_epoch,
        valid_until_ns=call.request.current.lease_expires_at_ns,
    )
    changed_sources = call.request.current.sources.model_copy(
        update={"normative_conflict_generation": changed_generation}
    )
    changed_current = call.request.current.model_copy(update={"sources": changed_sources})
    changed_request = call.request.model_copy(update={"current": changed_current})
    assert prepare_h1_scoped_delivery(_with_request(call, changed_request)).disposition == "DENIED"


async def test_scoped_owner_separates_policy_horizon_from_observation_capture_lease() -> None:
    call = await _call()

    result = prepare_h1_scoped_delivery(call)

    assert isinstance(result, PreparedH1ScopedDeliveryV1)
    assert result.intent_result.intent.mandate.horizon.expires_at_ns == 200
    assert call.request.current.lease_expires_at_ns == 175
    assert call.request.original_sources.semantic_registry.valid_until_ns == 175
    assert call.request.current.sources.effects_history.valid_until_ns == 175
    assert call.request.current.sources.normative_conflict_generation.valid_until_ns == 175


async def test_scoped_owner_rejects_capture_deadline_that_uses_policy_horizon() -> None:
    call = await _call()
    history = H1EffectsHistoryV1.model_validate_json(
        call.request.current.sources.effects_history.canonical_value
    )
    wrong_sources = _h1_sources(
        call.request.current.immutable_mandate_candidate, history, valid_until_ns=200
    )
    current = call.request.current.model_copy(update={"sources": wrong_sources})
    request = call.request.model_copy(
        update={"original_sources": wrong_sources, "current": current}
    )

    assert prepare_h1_scoped_delivery(_with_request(call, request)).disposition == "DENIED"


async def test_scoped_owner_rejects_expired_observation_lease_while_policy_is_current() -> None:
    call = await _call()
    current = call.request.current.model_copy(
        update={"observed_time_ns": 175, "lease_expires_at_ns": 175}
    )
    request = call.request.model_copy(update={"current": current})

    assert prepare_h1_scoped_delivery(_with_request(call, request)).disposition == "DENIED"


async def test_scoped_owner_rejects_expired_policy_while_observation_lease_is_current() -> None:
    call = await _call()
    history = H1EffectsHistoryV1.model_validate_json(
        call.request.current.sources.effects_history.canonical_value
    )
    sources = _h1_sources(
        call.request.current.immutable_mandate_candidate, history, valid_until_ns=225
    )
    current = call.request.current.model_copy(
        update={"sources": sources, "observed_time_ns": 200, "lease_expires_at_ns": 225}
    )
    request = call.request.model_copy(update={"original_sources": sources, "current": current})

    assert prepare_h1_scoped_delivery(_with_request(call, request)).disposition == "DENIED"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("normative_manifest", "forged-manifest"),
        ("reducer_version", "forged-reducer"),
        ("transition_registry_version", "forged-transitions"),
        ("canonicalization_fingerprint_version", "forged-canonicalization"),
        ("adapter_contract_version", "forged-adapter"),
    ),
)
async def test_scoped_owner_requires_each_h1_producer_semantic_binding_field(
    field: str, replacement: str
) -> None:
    call = await _call()
    semantics = H1_PRODUCER_SEMANTICS.model_copy(update={field: replacement})
    current = call.request.current.model_copy(update={"supported_semantics": semantics})
    request = call.request.model_copy(update={"current": current})
    assert prepare_h1_scoped_delivery(_with_request(call, request)).disposition == "DENIED"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("source_id", "forged-source"),
        ("source_version", "forged-version"),
        ("owner_id", "forged-owner"),
        ("canonical_value", b"forged-bytes"),
        (
            "head",
            EffectHead(
                subject_id="forged-source",
                head="forged-source/head",
                fingerprint=_sha(b"forged-head"),
            ),
        ),
    ),
)
async def test_scoped_owner_requires_exact_h1_semantic_registry_capture(
    field: str, replacement: str | bytes | EffectHead
) -> None:
    call = await _call()
    original = call.request.original_sources.semantic_registry.model_copy(
        update={field: replacement}
    )
    original_sources = call.request.original_sources.model_copy(
        update={"semantic_registry": original}
    )
    assert prepare_h1_scoped_delivery(
        _with_request(call, call.request.model_copy(update={"original_sources": original_sources}))
    ).disposition == "DENIED"


async def test_scoped_owner_requires_current_h1_semantic_registry_capture_to_match_original(
) -> None:
    call = await _call()
    current_registry = call.request.current.sources.semantic_registry.model_copy(
        update={"reader_id": "forged-reader"}
    )
    current_sources = call.request.current.sources.model_copy(
        update={"semantic_registry": current_registry}
    )
    current = call.request.current.model_copy(update={"sources": current_sources})
    assert prepare_h1_scoped_delivery(
        _with_request(call, call.request.model_copy(update={"current": current}))
    ).disposition == "DENIED"


@pytest.mark.parametrize(
    ("role", "field", "replacement"),
    (
        ("effects_history", "source_id", "forged-history-source"),
        ("effects_history", "reader_id", "forged-history-reader"),
        ("effects_history", "clock_epoch", "forged-epoch"),
        ("effects_history", "canonical_value", b'{"fabricated":true}'),
        ("normative_conflict_generation", "source_version", "forged-generation-version"),
        ("normative_conflict_generation", "owner_id", "forged-generation-owner"),
        ("normative_conflict_generation", "valid_until_ns", 199),
        ("normative_conflict_generation", "canonical_value", b'{"fabricated":true}'),
    ),
)
async def test_scoped_owner_requires_exact_history_and_generation_capture_metadata(
    role: str, field: str, replacement: str | bytes | int
) -> None:
    call = await _call()
    source = getattr(call.request.current.sources, role).model_copy(update={field: replacement})
    sources = call.request.current.sources.model_copy(update={role: source})
    current = call.request.current.model_copy(update={"sources": sources})
    assert prepare_h1_scoped_delivery(
        _with_request(call, call.request.model_copy(update={"current": current}))
    ).disposition == "DENIED"


async def test_scoped_owner_requires_equal_original_history_and_generation_captures() -> None:
    call = await _call()
    changed_history = call.request.original_sources.effects_history.model_copy(
        update={"reader_id": "another-reader"}
    )
    original_sources = call.request.original_sources.model_copy(
        update={"effects_history": changed_history}
    )
    assert prepare_h1_scoped_delivery(
        _with_request(call, call.request.model_copy(update={"original_sources": original_sources}))
    ).disposition == "DENIED"

    changed_generation = call.request.original_sources.normative_conflict_generation.model_copy(
        update={"reader_id": "another-reader"}
    )
    original_sources = call.request.original_sources.model_copy(
        update={"normative_conflict_generation": changed_generation}
    )
    assert prepare_h1_scoped_delivery(
        _with_request(call, call.request.model_copy(update={"original_sources": original_sources}))
    ).disposition == "DENIED"


async def test_scoped_owner_binds_history_observation_and_generation_not_policy_head() -> None:
    call = await _call()
    result = prepare_h1_scoped_delivery(call)
    assert isinstance(result, PreparedH1ScopedDeliveryV1)
    mandate = result.intent_result.intent.mandate
    generation_capture = call.request.current.sources.normative_conflict_generation
    assert mandate.normative_conflict_generation == generation_capture.invalidation_manifest
    assert mandate.normative_conflict_generation != generation_capture.head
    assert mandate.normative_conflict_generation != _effect_head(
        call.request.authority_evidence.policy_anchor.policy
    )

    current = call.request.current.model_copy(
        update={"history_observation": generation_capture.invalidation_manifest}
    )
    assert prepare_h1_scoped_delivery(
        _with_request(call, call.request.model_copy(update={"current": current}))
    ).disposition == "DENIED"


async def test_scoped_owner_rejects_self_consistent_history_containing_target_intent() -> None:
    call = await _call(selected_intent_id="intent")
    assert prepare_h1_scoped_delivery(call).disposition == "DENIED"


async def test_scoped_owner_retains_h1_source_bytes_and_refuses_forged_candidate() -> None:
    call = await _call()
    result = prepare_h1_scoped_delivery(call)
    assert isinstance(result, PreparedH1ScopedDeliveryV1)

    retained = {source.role: source for source in result.retained_sources}
    assert (
        retained["acquisition-inventory-trust"].canonical_record_bytes
        == call.request.original_sources.trust.canonical_value
    )
    assert (
        retained["request-inventory-endpoint"].canonical_record_bytes
        == call.request.current.sources.endpoint.canonical_value
    )
    assert (
        retained["acquisition-inventory-planning"].canonical_record_bytes
        == call.request.original_sources.planning.canonical_bytes()
    )
    for prefix, inventory in (
        ("request-inventory", call.request.current.sources),
        ("acquisition-inventory", call.request.original_sources),
    ):
        for role in type(inventory).model_fields:
            source = getattr(inventory, role)
            if isinstance(source, CapturedSource):
                expected_bytes = source.canonical_value
            elif isinstance(source, H1ProducerNotApplicableV1):
                expected_bytes = source.canonical_bytes()
            else:
                continue
            assert retained[f"{prefix}-{role}"].canonical_record_bytes == expected_bytes

    planning = call.request.original_sources.planning
    assert isinstance(planning, H1ProducerNotApplicableV1)
    forged_candidate = planning.candidate.model_copy(
        update={"prepared_delivery_basis_bytes": b"forged"}
    )
    forged_planning = planning.model_copy(
        update={"candidate": forged_candidate}
    )
    forged_sources = call.request.original_sources.model_copy(
        update={"planning": forged_planning}
    )
    forged_request = call.request.model_copy(update={"original_sources": forged_sources})
    forged_call = call.model_copy(
        update={
            "request": forged_request,
            "request_digest": _sha(forged_request.canonical_bytes()),
        }
    )
    assert prepare_h1_scoped_delivery(forged_call).disposition == "DENIED"

    stale_current = call.request.current.model_copy(update={"clock_epoch": "other-epoch"})
    stale_request = call.request.model_copy(update={"current": stale_current})
    stale_call = call.model_copy(
        update={
            "request": stale_request,
            "request_digest": _sha(stale_request.canonical_bytes()),
        }
    )
    assert prepare_h1_scoped_delivery(stale_call).disposition == "DENIED"


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
