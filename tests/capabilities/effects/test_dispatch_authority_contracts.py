"""Public observation consumer; shape acceptance grants no effect authority."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.dispatch_authority_contracts import (
    DispatchAuthorityObservation,
    DispatchObservationCut,
    DispatchSourceInventory,
    ExactHead,
    SelectedEffectHistoryMember,
    UnavailableSource,
)


def head() -> ExactHead:
    return ExactHead(subject_id="subject", head="head", fingerprint="a" * 64)


def unavailable_inventory() -> dict[str, UnavailableSource]:
    return {
        name: UnavailableSource(reason="UNIMPLEMENTED", detail="No current issuer")
        for name in (
            "trust",
            "planning",
            "semantic_registry",
            "original_adoption",
            "runtime_and_fence",
            "endpoint",
            "credential_lifecycle",
            "deployment_entitlement",
            "clock",
            "effects_history",
            "normative_conflict_generation",
        )
    }


def test_unavailable_observation_roundtrips_without_grant_or_send_field() -> None:
    observation = DispatchAuthorityObservation(
        schema_id="chiplog.effects.dispatch-observation.v2",
        query_fingerprint="a" * 64,
        cut=DispatchObservationCut(
            tenant_id="tenant",
            database_identity=head(),
            selected_journal_head=head(),
            materialization_commitment="b" * 64,
            tenant_frontier=0,
            source_role_registry=head(),
            capture_id="capture",
        ),
        sources=DispatchSourceInventory(**unavailable_inventory()),
        complete_effect_history=(),
        complete_history_fingerprint="c" * 64,
    )
    assert (
        DispatchAuthorityObservation.model_validate_json(observation.canonical_bytes())
        == observation
    )
    for key in ("send_authorized", "grant", "issued_ticket"):
        with pytest.raises(ValidationError):
            DispatchAuthorityObservation.model_validate(observation.model_dump() | {key: True})


@pytest.mark.parametrize("source", list(unavailable_inventory()))
def test_required_source_cannot_be_omitted_or_silently_defaulted(source: str) -> None:
    values = unavailable_inventory()
    del values[source]
    with pytest.raises(ValidationError):
        DispatchSourceInventory(**values)


def test_history_requires_original_selected_cut_and_preserves_exact_bytes() -> None:
    values = {
        "kind": "PLAN_EFFECT_PUBLISHED",
        "selected_decision": head(),
        "publication_ordinal": 1,
        "original_selected_cut": head(),
        "record": head(),
        "predecessor": None,
        "intent": head(),
        "semantics": {
            "normative_manifest": "manifest",
            "reducer_version": "reducer",
            "transition_registry_version": "transitions",
            "canonicalization_fingerprint_version": "canonical",
            "adapter_contract_version": "adapter",
        },
        "command_bytes": b"\x00\xffcommand",
        "record_bytes": b"\xff\x00record",
    }
    member = SelectedEffectHistoryMember.model_validate(values)
    assert SelectedEffectHistoryMember.model_validate_json(member.canonical_bytes()) == member
    for key in ("original_selected_cut", "selected_decision", "command_bytes", "record_bytes"):
        altered = dict(values)
        del altered[key]
        with pytest.raises(ValidationError):
            SelectedEffectHistoryMember.model_validate(altered)
    for change in ({"kind": "UNKNOWN"}, {"publication_ordinal": True}, {"exempt": True}):
        with pytest.raises(ValidationError):
            SelectedEffectHistoryMember.model_validate(values | change)
