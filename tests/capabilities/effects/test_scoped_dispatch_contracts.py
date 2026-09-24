"""Joined v3 pre-send and shared lifecycle consumers; observations grant no authority."""

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.acceptance_v2 import prepared_acceptance
from tests.support.effects import child

from chiplog.capabilities.effects import lifecycle_transition_contracts as life
from chiplog.capabilities.effects import scoped_dispatch_contracts as dispatch
from chiplog.capabilities.effects import scoped_intent_contracts as scope
from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    RecordEvidenceCommand,
    TransportObservationBinding,
)
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.fences import Absent


def source(name: str) -> scope.ScopedAuthorityRecord:
    return scope.ScopedAuthorityRecord(
        owner="effects",
        head=reference(name, name.encode()),
        schema_id=name + ".v1",
        canonical_record_bytes=b"\xff\x00" + name.encode(),
        selected_decision=reference("decision", b"decision"),
    )


def selected(name: str) -> life.SelectedEffectsSource:
    return life.SelectedEffectsSource(
        owner="effects",
        subject=reference(name, name.encode()),
        schema_id=name + ".v1",
        canonical_record_bytes=b"\xff\x00" + name.encode(),
        selected_decision=reference("decision", b"decision"),
        physical_record=reference("physical:" + name, name.encode()),
    )


def intent() -> scope.ExternalActionIntentV3:
    old = prepared_acceptance().effects_proposal.snapshot.intent
    wire = old.mandate.model_dump()
    wire["schema_id"] = "chiplog.effects.dispatch-mandate.v3"
    mandate = scope.DispatchMandateV3.model_validate(wire)
    precursor = scope.ScopedPrecursorRequest(
        request_id="precursor",
        mandate=mandate,
        interpretation_policy=source("policy"),
        preexisting_sources=(source("authority"),),
    )
    return scope.ExternalActionIntentV3(
        intent_id="new-intent",
        fingerprint="a" * 64,
        mandate=mandate,
        acquisition=scope.ScopedDispatchAcquisition(
            authority=scope.HumanScopedAdoption(
                adoption_act=source("adoption"),
                display=reference("display", b"display"),
                exact_display_bytes=b"display",
                exact_mandate_bytes=mandate.canonical_bytes(),
                authenticated_invocation=source("invocation"),
            ),
            precursor_request=precursor,
            precursor_result=scope.ScopedPrecursorResult(
                source_request_fingerprint="a" * 64,
                mandate_fingerprint="b" * 64,
                interpretation_policy=source("policy").head,
                complete_evaluation_evidence=(source("evaluation").head,),
            ),
            original_sources=old.acquisition.original_sources,
        ),
    )


def authorize() -> dispatch.PrepareScopedAuthorization:
    retained = prepared_acceptance()
    return dispatch.PrepareScopedAuthorization(
        identity=CommandIdentity(
            command_id="authorize", fingerprint="a" * 64, expected_tenant_head=7
        ),
        observed=dispatch.ScopedPreSendCut(
            original_intent=intent(),
            selected_intent=selected("intent"),
            current_parent=selected("parent"),
            state="INTENT_RECORDED",
            authorization=Absent(),
        ),
        current=retained.effects_request.current,
        complete_current_origin_sources=(source("origin-current"),),
        fence=retained.effects_request.command.fence,
    )


def authorization() -> scope.ScopedDispatchAuthorizationRecord:
    value = authorize()
    return scope.ScopedDispatchAuthorizationRecord(
        authorization_id="authorization",
        command=value.identity,
        original_intent=source("intent").head,
        expected_parent=source("parent").head,
        immutable_mandate=source("mandate").head,
        semantics=value.observed.original_intent.mandate.semantics,
        complete_current_origin_sources=value.complete_current_origin_sources,
        current_inputs_fingerprint="a" * 64,
        fence=value.fence,
    )


def test_v3_authorization_before_send_needs_no_child_or_obligation() -> None:
    value = authorize()
    adapter: TypeAdapter[dispatch.ScopedDispatchRequest] = TypeAdapter(
        dispatch.ScopedDispatchRequest
    )
    assert adapter.validate_json(value.canonical_bytes()) == value
    assert isinstance(value.observed.authorization, Absent)
    assert not {"children", "original_obligation"} & dispatch.ScopedPreSendCut.model_fields.keys()
    for field in ("current", "complete_current_origin_sources", "fence"):
        wire = value.model_dump()
        del wire[field]
        with pytest.raises(ValidationError):
            adapter.validate_python(wire)
    record = authorization()
    assert "authorization" not in scope.ScopedDispatchAuthorizationRecord.model_fields
    parent = dispatch.ScopedPreSendRevision(
        original_intent=record.original_intent,
        predecessor=record.expected_parent,
        state="DISPATCH_AUTHORIZED",
        authorization=reference(record.authorization_id, record.canonical_bytes()),
        retired_authorizations=(),
        current_inputs_fingerprint="a" * 64,
        complete_current_origin_sources=record.complete_current_origin_sources,
    )
    result = dispatch.PreparedScopedAuthorization(
        source_request_fingerprint="a" * 64,
        authorization=record,
        parent=parent,
        complete_batch_fingerprint="b" * 64,
    )
    assert (
        dispatch.PreparedScopedAuthorization.model_validate_json(result.canonical_bytes()) == result
    )


@pytest.mark.parametrize("value", [False, -1, 1, 2**64, "0"])
def test_first_send_ordinal_is_exact_strict_zero(value: object) -> None:
    wire = authorize().model_dump()
    wire.update(
        kind="PREPARE_SCOPED_FIRST_SEND_V3",
        selected_authorization=source("authorization").head,
        ordinal=value,
    )
    with pytest.raises(ValidationError):
        dispatch.PrepareScopedFirstSend.model_validate(wire)


def test_selected_authorization_and_exact_scope_survive_first_send_request() -> None:
    wire = authorize().model_dump()
    observed = authorize().observed.model_dump()
    observed.update(
        state="DISPATCH_AUTHORIZED",
        authorization=dispatch.SelectedScopedAuthorization(
            source=selected("authorization"), record=authorization()
        ),
    )
    wire.update(
        kind="PREPARE_SCOPED_FIRST_SEND_V3",
        observed=observed,
        selected_authorization=source("authorization").head,
        ordinal=0,
    )
    request = dispatch.PrepareScopedFirstSend.model_validate(wire)
    assert dispatch.PrepareScopedFirstSend.model_validate_json(request.canonical_bytes()) == request
    assert (
        request.complete_current_origin_sources[0].canonical_record_bytes
        == b"\xff\x00origin-current"
    )
    decision = dispatch.ScopedFirstSendDecision(
        identity=request.identity,
        source_request_fingerprint="a" * 64,
        original_intent=source("intent").head,
        authorization=request.selected_authorization,
        prior_parent=source("parent").head,
        current_inputs_fingerprint="b" * 64,
        complete_current_origin_sources=request.complete_current_origin_sources,
        fence=request.fence,
    )
    assert (
        dispatch.ScopedFirstSendDecision.model_validate_json(decision.canonical_bytes()) == decision
    )


@pytest.mark.parametrize(
    "target", ["HELD_BEFORE_SEND", "CANCELLED_BEFORE_SEND", "SUPERSEDED_BEFORE_SEND"]
)
def test_pre_send_disposition_has_explicit_current_denial_evidence(target: str) -> None:
    wire = authorize().model_dump()
    wire.update(
        kind="PREPARE_SCOPED_PRE_SEND_DISPOSITION_V3",
        target=target,
        decision_evidence=source("decision"),
    )
    value = dispatch.PrepareScopedPreSendDisposition.model_validate(wire)
    assert (
        dispatch.PrepareScopedPreSendDisposition.model_validate_json(value.canonical_bytes())
        == value
    )
    del wire["decision_evidence"]
    with pytest.raises(ValidationError):
        dispatch.PrepareScopedPreSendDisposition.model_validate(wire)


def test_v3_joins_all_child_lifecycle_without_inventing_obligation() -> None:
    cut = life.EffectsLineageCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=7,
        materialization_commitment="a" * 64,
        original_intent=intent(),
        original_authorization=authorization(),
        current_parent=source("parent").head,
        state="SEND_COMMITTED",
        complete_ordered_children=(child(0), child(1)),
        complete_child_sources=(selected("child:0"), selected("child:1")),
        complete_ordered_evidence=(),
        original_obligation=Absent(),
        complete_inventory_fingerprint="b" * 64,
    )
    restored = life.EffectsLineageCut.model_validate_json(cut.canonical_bytes())
    assert restored == cut
    assert isinstance(restored.original_intent, scope.ExternalActionIntentV3)
    assert isinstance(restored.original_authorization, scope.ScopedDispatchAuthorizationRecord)
    assert isinstance(restored.original_obligation, Absent)
    genesis = life.EffectsObligationRevision(
        obligation=source("original-obligation").head,
        predecessor=Absent(),
        original_intent=source("intent").head,
        original_send=source("first-send").head,
        complete_ordered_children=tuple(
            item.transmission for item in cut.complete_ordered_children
        ),
        state="OPEN",
        closure_predicate=source("closure-policy").head,
        resolver_binding=source("resolver-policy").head,
        closure_evidence=(),
    )
    assert life.EffectsObligationRevision.model_validate_json(genesis.canonical_bytes()) == genesis
    # Inert mismatched child identities above must fail owner preparation later.
    reduction = life.PrepareEffectsSemanticReduction(
        identity=authorize().identity,
        observed=cut,
        stream_id="stream",
        expected=Absent(),
        registered_reducer=selected("reducer"),
        authenticated_invocation=selected("evidence-auth"),
    )
    assert (
        life.PrepareEffectsSemanticReduction.model_validate_json(reduction.canonical_bytes())
        == reduction
    )
    reconcile = dispatch.PrepareAllChildReconciliation(
        identity=authorize().identity,
        observed=cut,
        registered_resolver=selected("resolver"),
        authenticated_invocation=selected("resolver-auth"),
        registered_reducer=selected("reducer"),
    )
    assert (
        dispatch.PrepareAllChildReconciliation.model_validate_json(reconcile.canonical_bytes())
        == reconcile
    )
    assert "fence" not in dispatch.PrepareAllChildReconciliation.model_fields
    transmission = cut.complete_ordered_children[0]
    evidence = dispatch.PrepareAllChildEvidence(
        command=RecordEvidenceCommand(
            identity=authorize().identity,
            expected_attempt=cut.current_parent,
            evidence_id="transport-sent",
            raw_bytes=b"\xffsent",
            authentication=TransportObservationBinding(
                kind="BROKER_TRANSPORT_OBSERVATION",
                tenant_id="tenant",
                broker_epoch=source("epoch").head,
                issued_operation=source("issued-send").head,
                transmission=transmission.transmission,
                exact_recipient=transmission.recipient,
                adapter_contract_version="1",
                raw_digest="raw-digest",
            ),
            semantics=cut.original_intent.mandate.semantics,
            observation="TRANSPORT_SENT",
            covered_children=(transmission.transmission,),
            occurred_members=(),
            permanently_incapable_members=(),
        ),
        observed=cut,
        selected_raw_custody=selected("raw"),
        source_authentication=selected("transport-auth"),
        registered_reducer=selected("reducer"),
    )
    adapter: TypeAdapter[dispatch.ScopedDispatchRequest] = TypeAdapter(
        dispatch.ScopedDispatchRequest
    )
    assert adapter.validate_json(evidence.canonical_bytes()) == evidence
    parent = dispatch.AllChildOutcomeParent(
        original_intent=source("intent").head,
        predecessor=cut.current_parent,
        state="SENT",
        complete_ordered_children=tuple(
            item.transmission for item in cut.complete_ordered_children
        ),
        complete_ordered_evidence=(source("transport-sent").head,),
        original_obligation=Absent(),
    )
    reduced = life.EffectsSemanticReductionRecord(
        stream_id="stream",
        reduction_id="reduction",
        original_intent=parent.original_intent,
        predecessor=Absent(),
        reducer=source("reducer").head,
        complete_ordered_children=parent.complete_ordered_children,
        complete_ordered_evidence=parent.complete_ordered_evidence,
        current_parent=source("sent-parent").head,
        original_obligation=Absent(),
        original_terminal_closure=Absent(),
        disposition=life.EffectsReductionHold(
            reason="INCOMPLETE", relevant_evidence=parent.complete_ordered_evidence
        ),
    )
    result = dispatch.PreparedAllChildOutcome(
        source_request_fingerprint="a" * 64,
        appended_evidence=dispatch.AllChildEvidenceRecord(
            original_intent=parent.original_intent,
            exact_child=transmission.transmission,
            command=evidence.command,
            selected_raw_custody=evidence.selected_raw_custody.subject,
            source_authentication=evidence.source_authentication.subject,
        ),
        parent=parent,
        obligation=None,
        reduction=reduced,
        complete_batch_fingerprint="b" * 64,
    )
    assert dispatch.PreparedAllChildOutcome.model_validate_json(result.canonical_bytes()) == result
    for omitted in ("parent", "obligation", "reduction", "appended_evidence"):
        wire = result.model_dump()
        del wire[omitted]
        with pytest.raises(ValidationError):
            dispatch.PreparedAllChildOutcome.model_validate(wire)
