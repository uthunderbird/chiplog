"""Effects consumer wire: complete proof shape is not permission to retransmit."""

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.acceptance_v2 import prepared_acceptance

from chiplog.capabilities.effects import lifecycle_transition_contracts as life
from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead, TransmissionAttempt
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchObligationV2
from chiplog.capabilities.effects.dispatch_v2 import DispatchAuthorizationV2, reference
from chiplog.capabilities.effects.dispatch_v2_contracts import CommitFirstSendV2
from chiplog.capabilities.effects.fences import Absent


def head(name: str) -> ExactHead:
    return reference(name, name.encode())


def source(name: str) -> life.SelectedEffectsSource:
    return life.SelectedEffectsSource(
        owner="effects",
        subject=head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=b"\xff\x00" + name.encode(),
        selected_decision=head("decision:" + name),
        physical_record=head("physical:" + name),
    )


def request() -> life.PrepareSafeRetransmission:
    retained = prepared_acceptance()
    intent = retained.effects_proposal.snapshot.intent
    mandate = intent.mandate
    intent_head = reference(intent.intent_id, intent.canonical_bytes())
    authorization = DispatchAuthorizationV2(
        authorization=head("original-authorization"),
        intent=intent_head,
        expected_attempt=head("before-send"),
        mandate=reference(mandate.mandate_id, mandate.canonical_bytes()),
        semantics=mandate.semantics,
        fence=retained.effects_request.command.fence,
        observation_fingerprint="a" * 64,
    )
    child = TransmissionAttempt(
        transmission=head("child:0"),
        intent=intent_head,
        ordinal=0,
        semantics=mandate.semantics,
        dispatch_time_ns=1,
        payload_fingerprint=mandate.effect_fingerprint,
        recipient=mandate.recipient,
        idempotency_fence_key=mandate.idempotency_fence_key,
        send_commit=head("first-send"),
        coverage_proof=None,
    )
    cut = life.EffectsLineageCut(
        tenant_id=mandate.tenant_id,
        database_id="database",
        tenant_commit_sequence=7,
        materialization_commitment="a" * 64,
        original_intent=intent,
        original_authorization=authorization,
        current_parent=head("unknown-parent"),
        state="OUTCOME_UNKNOWN",
        complete_ordered_children=(child,),
        complete_child_sources=(source("child:0"),),
        complete_ordered_evidence=(source("timeout"),),
        original_obligation=DispatchObligationV2(
            obligation=head("original-obligation"),
            original_send=head("first-send"),
            original_intent=intent_head,
            original_children=(child.transmission,),
            closure_predicate="ALL_ORIGINAL_CHILDREN_TERMINAL_V2",
            resolver_binding="AUTHENTICATED_ORIGINAL_PRINCIPAL_V2",
            state="OPEN",
            closure_evidence=(),
        ),
        complete_inventory_fingerprint="b" * 64,
    )
    subject = life.RetryCoverageSubject(
        original_intent=intent_head,
        exact_effect_fingerprint=mandate.effect_fingerprint,
        exact_payload_fingerprint=hashlib.sha256(mandate.payload).hexdigest(),
        recipient=mandate.recipient,
        idempotency_fence_key=mandate.idempotency_fence_key,
        complete_prior_children=(child.transmission,),
        next_transmission_id="child:1",
        next_ordinal=1,
        clock_contract="clock.v1",
        clock_epoch="epoch",
        coverage_starts_ns=0,
        coverage_expires_ns=20,
    )
    return life.PrepareSafeRetransmission(
        identity=CommandIdentity(command_id="retry", fingerprint="a" * 64, expected_tenant_head=7),
        observed=cut,
        expected_parent_state="OUTCOME_UNKNOWN",
        coverage=life.ProviderIdempotencyCoverage(
            subject=subject,
            provider_contract=source("provider-contract"),
            complete_coverage_proof=source("coverage"),
        ),
        current=retained.effects_request.current,
        complete_current_origin_sources=(),
        fence=retained.effects_request.command.fence,
    )


def test_retry_retains_original_mandate_all_children_and_new_send_coverage() -> None:
    value = request()
    adapter: TypeAdapter[life.EffectsLifecycleRequest] = TypeAdapter(life.EffectsLifecycleRequest)
    restored = adapter.validate_json(value.canonical_bytes())
    assert restored == value
    assert isinstance(restored, life.PrepareSafeRetransmission)
    assert (
        restored.observed.original_intent.canonical_bytes()
        == value.observed.original_intent.canonical_bytes()
    )
    assert restored.coverage.subject.complete_prior_children == tuple(
        c.transmission for c in restored.observed.complete_ordered_children
    )
    assert restored.coverage.subject.next_ordinal == 1
    assert restored.coverage.subject.next_transmission_id == "child:1"
    with pytest.raises(ValidationError):
        CommitFirstSendV2.model_validate_json(value.canonical_bytes())


@pytest.mark.parametrize(
    "state", ["PARTIAL", "CONFIRMED", "FAILED_NO_EFFECT", "PARTIAL_CONFIRMED", "INTENT_RECORDED"]
)
def test_retry_command_does_not_admit_terminal_or_partial_parent(state: str) -> None:
    wire = request().model_dump()
    wire["expected_parent_state"] = state
    with pytest.raises(ValidationError):
        life.PrepareSafeRetransmission.model_validate(wire)


@pytest.mark.parametrize(
    "field",
    [
        "complete_prior_children",
        "next_transmission_id",
        "next_ordinal",
        "recipient",
        "idempotency_fence_key",
        "coverage_expires_ns",
    ],
)
def test_coverage_cannot_omit_a_bound_component(field: str) -> None:
    wire = request().coverage.subject.model_dump()
    del wire[field]
    with pytest.raises(ValidationError):
        life.RetryCoverageSubject.model_validate(wire)


def test_permanent_incapacity_proof_is_not_timeout_or_absence_at_read() -> None:
    value = request()
    proof = life.AllPriorChildrenIncapable(
        subject=value.coverage.subject,
        complete_ordered_proofs=(
            life.ChildPermanentIncapacity(
                child=value.observed.complete_ordered_children[0].transmission,
                reason="WINNING_PROVIDER_FENCE",
                registered_protocol=head("provider-fence"),
                evidence=source("fence-proof"),
            ),
        ),
    )
    adapter: TypeAdapter[life.SafeRetryCoverage] = TypeAdapter(life.SafeRetryCoverage)
    assert adapter.validate_json(proof.canonical_bytes()) == proof
    for reason in ("TIMEOUT", "ABSENCE_OBSERVED", "SAFE"):
        wire = proof.complete_ordered_proofs[0].model_dump()
        wire["reason"] = reason
        with pytest.raises(ValidationError):
            life.ChildPermanentIncapacity.model_validate(wire)


def test_send_decision_child_parent_hashes_form_a_dag_and_one_result() -> None:
    value = request()
    assert isinstance(value.observed.original_authorization, DispatchAuthorizationV2)
    assert isinstance(value.observed.original_obligation, DispatchObligationV2)
    decision = life.RetransmissionDecisionRecord(
        identity=value.identity,
        source_request_fingerprint=hashlib.sha256(value.canonical_bytes()).hexdigest(),
        original_intent=value.coverage.subject.original_intent,
        original_authorization=value.observed.original_authorization.authorization,
        prior_parent=value.observed.current_parent,
        coverage=value.coverage,
        current_inputs_fingerprint=hashlib.sha256(value.current.canonical_bytes()).hexdigest(),
        complete_current_origin_sources=value.complete_current_origin_sources,
        fence=value.fence,
    )
    decision_head = reference("retry-decision", decision.canonical_bytes())
    wire = value.observed.complete_ordered_children[0].model_dump()
    wire.update(
        transmission=head("child:1"),
        ordinal=1,
        send_commit=decision_head,
        coverage_proof=head("coverage"),
    )
    child = TransmissionAttempt.model_validate(wire)
    parent = life.RetriedParentRevision(
        original_intent=decision.original_intent,
        original_authorization=decision.original_authorization,
        send_decision=decision_head,
        predecessor=decision.prior_parent,
        state="OUTCOME_UNKNOWN",
        complete_ordered_children=(
            value.observed.complete_ordered_children[0].transmission,
            reference("child:1", child.canonical_bytes()),
        ),
        retained_evidence=(head("timeout"),),
        retained_obligation=value.observed.original_obligation.obligation,
        coverage=value.coverage,
        current_command_fingerprint=value.current.command_fingerprint,
        fence=value.fence,
    )
    result = life.PreparedSafeRetransmission(
        source_request_fingerprint=decision.source_request_fingerprint,
        decision=decision,
        child=child,
        parent=parent,
        complete_batch_fingerprint="b" * 64,
    )
    adapter: TypeAdapter[life.EffectsLifecycleResult] = TypeAdapter(life.EffectsLifecycleResult)
    assert adapter.validate_json(result.canonical_bytes()) == result
    assert result.child.send_commit == result.parent.send_decision
    assert (
        result.parent.complete_ordered_children[-1].fingerprint
        == hashlib.sha256(result.child.canonical_bytes()).hexdigest()
    )
    for field in ("decision", "child", "parent"):
        wire_result = result.model_dump()
        del wire_result[field]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire_result)


@pytest.mark.parametrize(
    "reason",
    ["INCOMPLETE", "RIVAL", "CONTRADICTORY", "MEANING_CHANGED", "UNCLASSIFIED", "REGISTRY_CHANGED"],
)
def test_effects_reduction_holds_without_granting_send(reason: str) -> None:
    value = request()
    assert isinstance(value.observed.original_obligation, DispatchObligationV2)
    record = life.EffectsSemanticReductionRecord(
        stream_id="original-evidence",
        reduction_id="stable-reduction",
        original_intent=value.coverage.subject.original_intent,
        predecessor=Absent(),
        reducer=head("reducer"),
        complete_ordered_children=value.coverage.subject.complete_prior_children,
        complete_ordered_evidence=(head("timeout"),),
        current_parent=value.observed.current_parent,
        original_obligation=value.observed.original_obligation.obligation,
        original_terminal_closure=Absent(),
        disposition=life.EffectsReductionHold.model_validate(
            {"reason": reason, "relevant_evidence": (head("timeout"),)}
        ),
    )
    result = life.PreparedEffectsSemanticReduction(
        source_request_fingerprint="a" * 64, record=record, complete_batch_fingerprint="b" * 64
    )
    adapter: TypeAdapter[life.EffectsLifecycleResult] = TypeAdapter(life.EffectsLifecycleResult)
    assert adapter.validate_json(result.canonical_bytes()) == result
    with pytest.raises(ValidationError):
        life.PreparedSafeRetransmission.model_validate_json(result.canonical_bytes())
    wire = record.model_dump()
    wire["owner"] = "agent_loop"
    with pytest.raises(ValidationError):
        life.EffectsSemanticReductionRecord.model_validate(wire)
    assert "fence" not in life.PrepareEffectsSemanticReduction.model_fields


def test_reduction_request_requires_exact_prior_and_full_evidence_cut() -> None:
    value = request()
    reduction = life.PrepareEffectsSemanticReduction(
        identity=value.identity,
        observed=value.observed,
        stream_id="original-evidence",
        expected=Absent(),
        registered_reducer=source("reducer"),
        authenticated_invocation=source("evidence-auth"),
    )
    adapter: TypeAdapter[life.EffectsLifecycleRequest] = TypeAdapter(life.EffectsLifecycleRequest)
    assert adapter.validate_json(reduction.canonical_bytes()) == reduction
    assert (
        reduction.observed.complete_ordered_evidence[0].canonical_record_bytes == b"\xff\x00timeout"
    )
