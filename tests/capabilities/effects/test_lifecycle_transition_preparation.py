"""Pure retry preparation retains a lineage; it does not authorize a SEND."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from tests.support.acceptance_v2 import prepared_acceptance

from chiplog.capabilities.effects import lifecycle_transition_contracts as life
from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead, TransmissionAttempt
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchObligationV2
from chiplog.capabilities.effects.dispatch_v2 import DispatchAuthorizationV2, reference
from chiplog.capabilities.effects.dispatch_v2_contracts import DispatchMandateV2
from chiplog.capabilities.effects.lifecycle_transition_preparation import (
    prepare_safe_retransmission,
)
from chiplog.capabilities.effects.scoped_intent_contracts import (
    DispatchMandateV3,
    ExternalActionIntentV3,
    HumanScopedAdoption,
    ScopedAuthorityRecord,
    ScopedDispatchAcquisition,
    ScopedDispatchAuthorizationRecord,
    ScopedPrecursorRequest,
    ScopedPrecursorResult,
)


def _head(name: str) -> ExactHead:
    return reference(name, name.encode())


def _selected(name: str, subject: ExactHead | None = None) -> life.SelectedEffectsSource:
    return life.SelectedEffectsSource(
        owner="effects",
        subject=subject or _head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=b"selected:" + name.encode(),
        selected_decision=_head("decision:" + name),
        physical_record=_head("physical:" + name),
    )


def _authority_source(name: str) -> ScopedAuthorityRecord:
    return ScopedAuthorityRecord(
        owner="effects",
        head=_head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=name.encode(),
        selected_decision=_head("decision:" + name),
    )


def _request(*, v3: bool = False, coverage: str = "provider") -> life.PrepareSafeRetransmission:
    retained = prepared_acceptance()
    v2_intent = retained.effects_proposal.snapshot.intent
    mandate: DispatchMandateV2 | DispatchMandateV3 = v2_intent.mandate
    intent: life.LifecycleIntent = v2_intent
    intent_head = reference(v2_intent.intent_id, v2_intent.canonical_bytes())
    authorization: DispatchAuthorizationV2 | ScopedDispatchAuthorizationRecord
    authorization = DispatchAuthorizationV2(
        authorization=_head("original-authorization"),
        intent=intent_head,
        expected_attempt=_head("before-send"),
        mandate=reference(mandate.mandate_id, mandate.canonical_bytes()),
        semantics=mandate.semantics,
        fence=retained.effects_request.command.fence,
        observation_fingerprint="a" * 64,
    )
    if v3:
        mandate_v3 = DispatchMandateV3.model_validate(
            {**mandate.model_dump(), "schema_id": "chiplog.effects.dispatch-mandate.v3"}
        )
        policy = _authority_source("policy")
        precursor = ScopedPrecursorRequest(
            request_id="precursor",
            mandate=mandate_v3,
            interpretation_policy=policy,
            preexisting_sources=(_authority_source("preexisting"),),
        )
        intent = ExternalActionIntentV3(
            intent_id="v3-intent",
            fingerprint="b" * 64,
            mandate=mandate_v3,
            acquisition=ScopedDispatchAcquisition(
                authority=HumanScopedAdoption(
                    adoption_act=_authority_source("adoption"),
                    display=_head("display"),
                    exact_display_bytes=b"display",
                    exact_mandate_bytes=mandate_v3.canonical_bytes(),
                    authenticated_invocation=_authority_source("invocation"),
                ),
                precursor_request=precursor,
                precursor_result=ScopedPrecursorResult(
                    source_request_fingerprint="c" * 64,
                    mandate_fingerprint="d" * 64,
                    interpretation_policy=policy.head,
                    complete_evaluation_evidence=(_head("evaluation"),),
                ),
                original_sources=v2_intent.acquisition.original_sources,
            ),
        )
        mandate = intent.mandate
        intent_head = reference(intent.intent_id, intent.canonical_bytes())
        authorization = ScopedDispatchAuthorizationRecord(
            authorization_id="v3-authorization",
            command=CommandIdentity(
                command_id="original", fingerprint="e" * 64, expected_tenant_head=7
            ),
            original_intent=intent_head,
            expected_parent=_head("before-send"),
            immutable_mandate=reference(mandate.mandate_id, mandate.canonical_bytes()),
            semantics=mandate.semantics,
            complete_current_origin_sources=(),
            current_inputs_fingerprint="f" * 64,
            fence=retained.effects_request.command.fence,
        )
    children = tuple(
        TransmissionAttempt(
            transmission=_head(f"child:{ordinal}"),
            intent=intent_head,
            ordinal=ordinal,
            semantics=mandate.semantics,
            dispatch_time_ns=ordinal + 1,
            payload_fingerprint=mandate.effect_fingerprint,
            recipient=mandate.recipient,
            idempotency_fence_key=mandate.idempotency_fence_key,
            send_commit=_head(f"send:{ordinal}"),
            coverage_proof=None,
        )
        for ordinal in range(2)
    )
    cut = life.EffectsLineageCut(
        tenant_id=mandate.tenant_id,
        database_id="database",
        tenant_commit_sequence=7,
        materialization_commitment="a" * 64,
        original_intent=intent,
        original_authorization=authorization,
        current_parent=_head("unknown-parent"),
        state="OUTCOME_UNKNOWN",
        complete_ordered_children=children,
        complete_child_sources=tuple(
            _selected(f"child:{i}", child.transmission) for i, child in enumerate(children)
        ),
        complete_ordered_evidence=(_selected("evidence"),),
        original_obligation=DispatchObligationV2(
            obligation=_head("original-obligation"),
            original_send=_head("send:0"),
            original_intent=intent_head,
            original_children=tuple(child.transmission for child in children),
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
        complete_prior_children=tuple(child.transmission for child in children),
        next_transmission_id="child:2",
        next_ordinal=2,
        clock_contract=retained.effects_request.current.clock_contract,
        clock_epoch=retained.effects_request.current.clock_epoch,
        coverage_starts_ns=0,
        coverage_expires_ns=20,
    )
    if coverage == "provider":
        proof: life.SafeRetryCoverage = life.ProviderIdempotencyCoverage(
            subject=subject,
            provider_contract=_selected("provider"),
            complete_coverage_proof=_selected("coverage"),
        )
    else:
        proof = life.AllPriorChildrenIncapable(
            subject=subject,
            complete_ordered_proofs=tuple(
                life.ChildPermanentIncapacity(
                    child=child.transmission,
                    reason="WINNING_PROVIDER_FENCE",
                    registered_protocol=_head(f"protocol:{child.ordinal}"),
                    evidence=_selected(f"proof:{child.ordinal}"),
                )
                for child in children
            ),
        )
    return life.PrepareSafeRetransmission(
        identity=CommandIdentity(command_id="retry", fingerprint="a" * 64, expected_tenant_head=7),
        observed=cut,
        expected_parent_state="OUTCOME_UNKNOWN",
        coverage=proof,
        current=retained.effects_request.current,
        complete_current_origin_sources=(),
        fence=retained.effects_request.command.fence,
    )


def _prepared(request: life.PrepareSafeRetransmission) -> life.PreparedSafeRetransmission:
    result = prepare_safe_retransmission(request)
    assert isinstance(result, life.PreparedSafeRetransmission)
    return result


@pytest.mark.parametrize("v3", [False, True])
def test_retry_prepares_native_decision_child_parent_from_original_lineage(v3: bool) -> None:
    value = _request(v3=v3)
    result = _prepared(value)
    child_head = reference(
        value.coverage.subject.next_transmission_id, result.child.canonical_bytes()
    )
    assert result.decision.original_intent == value.coverage.subject.original_intent
    assert result.child.ordinal == 2
    assert result.child.transmission.subject_id == "child:2"
    assert result.child.send_commit == result.parent.send_decision
    assert result.child.coverage_proof == result.parent.send_decision
    assert result.parent.complete_ordered_children == (
        *value.coverage.subject.complete_prior_children,
        child_head,
    )
    assert result.parent.state == "OUTCOME_UNKNOWN"
    assert isinstance(value.observed.original_obligation, DispatchObligationV2)
    assert result.parent.retained_obligation == value.observed.original_obligation.obligation
    assert result.parent.retained_evidence == tuple(
        item.subject for item in value.observed.complete_ordered_evidence
    )
    assert (
        result.complete_batch_fingerprint
        == hashlib.sha256(
            b"chiplog.effects.safe-retransmission-batch.v1\x00"
            + result.decision.canonical_bytes()
            + result.child.canonical_bytes()
            + result.parent.canonical_bytes()
        ).hexdigest()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-source",
        "reordered-source",
        "duplicate-source",
        "state",
        "intent",
        "ordinal",
        "effect",
        "payload",
        "recipient",
        "key",
    ],
)
def test_retry_rejects_missing_reordered_duplicate_or_substituted_child_source(
    mutation: str,
) -> None:
    value = _request()
    if mutation == "missing-source":
        observed = value.observed.model_copy(
            update={"complete_child_sources": value.observed.complete_child_sources[:1]}
        )
        value = value.model_copy(update={"observed": observed})
    elif mutation == "reordered-source":
        observed = value.observed.model_copy(
            update={"complete_child_sources": value.observed.complete_child_sources[::-1]}
        )
        value = value.model_copy(update={"observed": observed})
    elif mutation == "duplicate-source":
        observed = value.observed.model_copy(
            update={"complete_child_sources": value.observed.complete_child_sources[:1] * 2}
        )
        value = value.model_copy(update={"observed": observed})
    elif mutation == "state":
        value = value.model_copy(update={"expected_parent_state": "SENT"})
    elif mutation == "intent":
        value = value.model_copy(
            update={
                "coverage": value.coverage.model_copy(
                    update={
                        "subject": value.coverage.subject.model_copy(
                            update={"original_intent": _head("other")}
                        )
                    }
                )
            }
        )
    else:
        child = value.observed.complete_ordered_children[0]
        updates: dict[str, str] = {
            "ordinal": "ordinal",
            "payload": "payload_fingerprint",
            "effect": "payload_fingerprint",
            "recipient": "recipient",
            "key": "idempotency_fence_key",
        }
        field = updates[mutation]
        changed_value: Any = (
            _head("other")
            if field == "recipient"
            else "other"
            if field == "idempotency_fence_key"
            else 9
            if field == "ordinal"
            else ("0" * 64 if child.payload_fingerprint != "0" * 64 else "1" * 64)
        )
        changed = child.model_copy(update={field: changed_value})
        observed = value.observed.model_copy(
            update={
                "complete_ordered_children": (
                    changed,
                    *value.observed.complete_ordered_children[1:],
                )
            }
        )
        value = value.model_copy(update={"observed": observed})
    assert not isinstance(prepare_safe_retransmission(value), life.PreparedSafeRetransmission)


@pytest.mark.parametrize("coverage", ["provider", "incapacity"])
def test_retry_binds_coverage_descriptors_to_every_prior_child_and_next_send(coverage: str) -> None:
    value = _request(coverage=coverage)
    assert isinstance(prepare_safe_retransmission(value), life.PreparedSafeRetransmission)
    subject = value.coverage.subject.model_copy(update={"next_ordinal": 3})
    assert not isinstance(
        prepare_safe_retransmission(
            value.model_copy(
                update={"coverage": value.coverage.model_copy(update={"subject": subject})}
            )
        ),
        life.PreparedSafeRetransmission,
    )
    repeated_id = value.coverage.subject.model_copy(update={"next_transmission_id": "child:0"})
    assert not isinstance(
        prepare_safe_retransmission(
            value.model_copy(
                update={"coverage": value.coverage.model_copy(update={"subject": repeated_id})}
            )
        ),
        life.PreparedSafeRetransmission,
    )
    if isinstance(value.coverage, life.AllPriorChildrenIncapable):
        shortened = value.coverage.model_copy(
            update={"complete_ordered_proofs": value.coverage.complete_ordered_proofs[:1]}
        )
        assert not isinstance(
            prepare_safe_retransmission(value.model_copy(update={"coverage": shortened})),
            life.PreparedSafeRetransmission,
        )


def test_retry_rejects_partial_terminal_or_expired_proof_interval() -> None:
    value = _request()
    for state in ("PARTIAL", "CONFIRMED"):
        assert not isinstance(
            prepare_safe_retransmission(
                value.model_copy(
                    update={"observed": value.observed.model_copy(update={"state": state})}
                )
            ),
            life.PreparedSafeRetransmission,
        )
    subject = value.coverage.subject.model_copy(
        update={"coverage_expires_ns": value.current.observed_time_ns}
    )
    assert not isinstance(
        prepare_safe_retransmission(
            value.model_copy(
                update={"coverage": value.coverage.model_copy(update={"subject": subject})}
            )
        ),
        life.PreparedSafeRetransmission,
    )
    mismatched_clock = value.coverage.subject.model_copy(update={"clock_epoch": "other-epoch"})
    assert not isinstance(
        prepare_safe_retransmission(
            value.model_copy(
                update={"coverage": value.coverage.model_copy(update={"subject": mismatched_clock})}
            )
        ),
        life.PreparedSafeRetransmission,
    )


def test_retry_is_deterministic_and_pure() -> None:
    value = _request()
    assert _prepared(value).canonical_bytes() == _prepared(value).canonical_bytes()
