"""Public scoped-intent consumers; adoption bytes remain untrusted until broker issuance."""

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.acceptance_v2 import prepared_acceptance
from tests.support.delivery_completion import _captured

from chiplog.capabilities.agent_loop.delivery_preparation import (
    DeliveryCompletion,
    prepare_completion,
)
from chiplog.capabilities.effects import scoped_intent_contracts as scope
from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    DeliverySendBinding,
    ExactHead,
    OriginalAmbiguity,
    OriginSelection,
)
from chiplog.capabilities.effects.dispatch_v2 import reference
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    DispatchMandateV2,
    ExternalActionIntentV2,
)
from chiplog.capabilities.effects.fences import Absent


def head(name: str) -> ExactHead:
    return reference(name, name.encode())


def source(name: str) -> scope.ScopedAuthorityRecord:
    return scope.ScopedAuthorityRecord(
        owner="effects",
        head=head(name),
        schema_id=name + ".v1",
        canonical_record_bytes=b"\xff\x00" + name.encode(),
        selected_decision=head("selected:" + name),
    )


def compensation() -> scope.CompensationOriginV3:
    intent = prepared_acceptance().effects_proposal.snapshot.intent
    return scope.CompensationOriginV3(
        original=OriginalAmbiguity(
            original_intent=head("original-uncertain-intent"),
            original_binding=intent.mandate.semantics,
            ambiguity_reconciliation_head=head("original-ambiguity"),
        ),
        consequence_addressed=head("consequence"),
        proposed_compensation=head("compensation-proposal"),
    )


def duplicate_risk() -> scope.DuplicateRiskOriginV3:
    return scope.DuplicateRiskOriginV3(
        unresolved_attempts=(compensation().original,),
        possible_duplicate_effects=(head("duplicate"),),
        affected_parties_resources=(head("party"),),
        commitment_consequences=(head("commitment"),),
        safer_alternatives=(head("wait-for-reconciliation"),),
    )


def mandate(origin: scope.ScopedDispatchOrigin) -> scope.DispatchMandateV3:
    wire = prepared_acceptance().effects_proposal.snapshot.intent.mandate.model_dump()
    wire.update(
        schema_id="chiplog.effects.dispatch-mandate.v3",
        mandate_id="fresh-scoped-mandate",
        origin=origin,
    )
    return scope.DispatchMandateV3.model_validate(wire)


def adopted_intent(origin: scope.ScopedDispatchOrigin) -> scope.ExternalActionIntentV3:
    fresh = mandate(origin)
    retained = prepared_acceptance()
    precursor = scope.ScopedPrecursorRequest(
        request_id="precursor",
        mandate=fresh,
        interpretation_policy=source("policy"),
        preexisting_sources=(source("original-authority"),),
    )
    authority = scope.HumanScopedAdoption(
        adoption_act=source("adoption"),
        display=head("display"),
        exact_display_bytes=b"\xffexplicit-risk-display",
        exact_mandate_bytes=fresh.canonical_bytes(),
        authenticated_invocation=source("human-invocation"),
    )
    return scope.ExternalActionIntentV3(
        intent_id="fresh-intent",
        fingerprint="a" * 64,
        mandate=fresh,
        acquisition=scope.ScopedDispatchAcquisition(
            authority=authority,
            precursor_request=precursor,
            precursor_result=scope.ScopedPrecursorResult(
                source_request_fingerprint=hashlib.sha256(precursor.canonical_bytes()).hexdigest(),
                mandate_fingerprint=hashlib.sha256(fresh.canonical_bytes()).hexdigest(),
                interpretation_policy=precursor.interpretation_policy.head,
                complete_evaluation_evidence=(head("evaluation"),),
            ),
            original_sources=retained.effects_proposal.snapshot.intent.acquisition.original_sources,
        ),
    )


@pytest.mark.parametrize("kind", ["compensation", "duplicate-risk"])
def test_fresh_intent_retains_whole_origin_in_adopted_mandate(kind: str) -> None:
    origin = compensation() if kind == "compensation" else duplicate_risk()
    intent = adopted_intent(origin)
    restored = scope.ExternalActionIntentV3.model_validate_json(intent.canonical_bytes())
    assert restored == intent
    assert isinstance(restored.acquisition.authority, scope.HumanScopedAdoption)
    assert restored.acquisition.authority.exact_mandate_bytes == restored.mandate.canonical_bytes()
    assert restored.acquisition.authority.exact_display_bytes == b"\xffexplicit-risk-display"
    assert "adoption" not in type(origin).model_fields
    with pytest.raises(ValidationError):
        ExternalActionIntentV2.model_validate_json(intent.canonical_bytes())
    with pytest.raises(ValidationError):
        DispatchMandateV2.model_validate_json(intent.mandate.canonical_bytes())


def test_v3_preserves_every_existing_mandate_field() -> None:
    assert set(scope.DispatchMandateV3.model_fields) == set(DispatchMandateV2.model_fields)
    old = prepared_acceptance().effects_proposal.snapshot.intent.mandate.model_dump()
    new = mandate(compensation()).model_dump()
    for name in old.keys() - {"schema_id", "mandate_id", "origin"}:
        assert new[name] == old[name]


@pytest.mark.parametrize(
    "field",
    [
        "unresolved_attempts",
        "possible_duplicate_effects",
        "affected_parties_resources",
        "commitment_consequences",
        "safer_alternatives",
    ],
)
def test_duplicate_risk_disclosure_families_cannot_be_omitted_or_empty(field: str) -> None:
    wire = duplicate_risk().model_dump()
    del wire[field]
    with pytest.raises(ValidationError):
        scope.DuplicateRiskOriginV3.model_validate(wire)
    wire[field] = ()
    with pytest.raises(ValidationError):
        scope.DuplicateRiskOriginV3.model_validate(wire)


def test_authority_matrix_keeps_bounded_compensation_but_requires_human_duplicate_risk() -> None:
    matrix = dict(scope.SCOPED_AUTHORITY_MATRIX)
    mapping = TypeAdapter(scope.ScopedDispatchOrigin).json_schema()["discriminator"]["mapping"]
    assert set(matrix) == set(mapping)
    assert matrix["AUTHORIZE_DUPLICATE_RISK"] == ("HUMAN_ADOPTION",)
    assert matrix["COMPENSATION"] == ("HUMAN_ADOPTION", "REGISTERED_COMPENSATION_AUTHORITY")
    assert matrix["PREPARED_DELIVERY"] == ("DELIVERY_PREPARATION",)
    authority = scope.RegisteredCompensationAuthority(
        authority_or_bounded_mandate=source("bounded-mandate"),
        current_applicability=source("applicability"),
        adoption_requirement=source("adoption-policy"),
        exact_mandate_bytes=mandate(compensation()).canonical_bytes(),
    )
    adapter: TypeAdapter[scope.ScopedAcquisitionAuthority] = TypeAdapter(
        scope.ScopedAcquisitionAuthority
    )
    assert adapter.validate_json(authority.canonical_bytes()) == authority
    with pytest.raises(ValidationError):
        scope.HumanScopedAdoption.model_validate_json(authority.canonical_bytes())


async def test_delivery_basis_uses_loop_only_proposal_without_effects_outputs() -> None:
    _, observation = await _captured()
    command = DeliveryCompletion.model_validate_json(observation.captured_response)
    proposal = prepare_completion(command, observation)
    acceptance = ExactHead(
        subject_id=proposal.acceptance.identity,
        head=proposal.acceptance.head,
        fingerprint=proposal.acceptance.fingerprint,
    )
    basis = scope.PreparedDeliveryBasisV3(
        source_cut=head("cut"),
        acceptance=acceptance,
        completion_command_bytes=command.canonical_bytes(),
        delivery_observation_bytes=observation.canonical_bytes(),
        loop_proposal_bytes=proposal.canonical_bytes(),
    )
    recipient = prepared_acceptance().effects_proposal.snapshot.intent.mandate.recipient
    origin = scope.PreparedDeliveryOriginV3(
        original_run=head("run"),
        captured_attempt=head("captured"),
        preparation_basis=reference("basis", basis.canonical_bytes()),
        binding=DeliverySendBinding(
            delivery_id="delivery",
            acceptance=acceptance,
            render_digest="render",
            manifest_digest="manifest",
            selection=OriginSelection(
                kind="ORIGIN_EXACT", ingress_binding=head("inbox"), recipient=recipient
            ),
            visibility=(head("visibility"),),
            provenance=(head("provenance"),),
            disclosure=(head("label"),),
            narrowing=(),
            policy=head("policy"),
        ),
    )
    authority = scope.PreparedDeliveryAuthority(
        basis=basis,
        preexisting_communication_authority=source("communication"),
        current_disclosure_authority=source("disclosure"),
        exact_mandate_bytes=mandate(origin).canonical_bytes(),
    )
    assert (
        scope.PreparedDeliveryAuthority.model_validate_json(authority.canonical_bytes())
        == authority
    )
    assert basis.loop_proposal_bytes == proposal.canonical_bytes()
    wire = basis.model_dump()
    wire["effects_outputs"] = (b"intent",)
    with pytest.raises(ValidationError):
        scope.PreparedDeliveryBasisV3.model_validate(wire)
    assert "selected_complete_acceptance" not in scope.PreparedDeliveryBasisV3.model_fields


def test_publication_is_fresh_and_carries_current_original_scope_sources() -> None:
    retained = prepared_acceptance()
    value = scope.PrepareScopedIntentPublication(
        identity=CommandIdentity(
            command_id="publish-new", fingerprint="a" * 64, expected_tenant_head=7
        ),
        intent=adopted_intent(compensation()),
        expected_intent=Absent(),
        current=retained.effects_request.current,
        complete_current_origin_sources=(source("original-ambiguity"),),
        fence=retained.effects_request.command.fence,
    )
    assert (
        scope.PrepareScopedIntentPublication.model_validate_json(value.canonical_bytes()) == value
    )
    result = scope.PreparedScopedIntentPublication(
        source_request_fingerprint="b" * 64,
        intent=value.intent,
        original_references=(head("original-uncertain-intent"),),
        complete_owner_commitment="c" * 64,
    )
    assert (
        scope.PreparedScopedIntentPublication.model_validate_json(result.canonical_bytes())
        == result
    )
    wire = result.model_dump()
    wire["updated_original_intent"] = value.intent
    with pytest.raises(ValidationError):
        scope.PreparedScopedIntentPublication.model_validate(wire)
