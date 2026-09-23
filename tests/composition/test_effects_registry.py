"""Closed policy rejection and exact commitment tests; no issuance authority."""

import base64
import hashlib
from dataclasses import FrozenInstanceError

import pytest

from chiplog.capabilities.effects.contracts import (
    CommandIdentity,
    EffectRecord,
    EffectSnapshot,
    EffectStoreSnapshot,
)
from chiplog.composition.r16_effects import EffectPreviewBinding, HermeticEffectProposal
from chiplog.composition.r16_effects_registry import (
    HermeticPlanEffectRegistry,
    HermeticPolicyEvaluation,
)
from tests.support.effects import head, intent


def binding() -> EffectPreviewBinding:
    return EffectPreviewBinding(
        schema_id="chiplog.hermetic-effect-preview.v1",
        proposal=HermeticEffectProposal(
            schema_id="chiplog.hermetic-effect-proposal.v1",
            purpose="opaque emission",
            payload_base64=base64.b64encode(b"opaque bytes").decode(),
            bundle_members=("one",),
        ),
        proposal_id="proposal",
        run_id="run",
        run_head="run-head",
        principal="hermetic-principal",
        tenant="hermetic-tenant",
        planning_command_base64="eA==",
        planning_result_base64="eA==",
        planning_request_base64="eA==",
        expected_tenant_head=0,
        provider="hermetic-effects",
        account="hermetic-account",
        recipient="hermetic-principal",
        canonical_address="hermetic://effects/hermetic-principal",
        adapter_contract="chiplog.hermetic-effects.v1",
        policy="chiplog.hermetic-self-effect-policy.v1",
        valid_until_ns=100,
    )


def evaluate(
    preview: EffectPreviewBinding,
    records: tuple[EffectRecord, ...] = (),
) -> HermeticPolicyEvaluation:
    return HermeticPlanEffectRegistry().evaluate(
        preview,
        EffectStoreSnapshot(tenant_id="hermetic-tenant", tenant_head=len(records), records=records),
        tenant_id="hermetic-tenant",
        principal_id="hermetic-principal",
    )


def record(state: str, name: str = "old") -> EffectRecord:
    registry = HermeticPlanEffectRegistry()
    original = intent()
    authority = original.authority.model_copy(
        update={
            "tenant_id": "hermetic-tenant",
            "principal_id": "hermetic-principal",
            "actor_id": "hermetic-principal",
            "recipient": registry.recipient,
        }
    )
    snapshot = EffectSnapshot.model_validate(
        {
            "intent": original.model_copy(
                update={"authority": authority, "semantics": registry.semantics}
            ),
            "attempt": head(name),
            "state": state,
            "authorizations": (),
            "transmissions": (),
            "evidence": (),
            "unresolved_obligations": (),
        }
    )
    return EffectRecord(
        record=head(name + "record"),
        command=CommandIdentity(command_id=name, fingerprint=name, expected_tenant_head=0),
        predecessor=None,
        kind="PLAN_EFFECT_PUBLISHED",
        snapshot=snapshot,
        source_command=b"owner bytes",
    )


def test_registry_is_immutable_canonical_and_has_no_send_credential() -> None:
    registry = HermeticPlanEffectRegistry()
    assert registry.reference.fingerprint == hashlib.sha256(registry.canonical_bytes()).hexdigest()
    assert registry.read_reference.canonical_value == registry.canonical_bytes()
    assert b'"send_capability":false' in registry.credential_reference.canonical_value
    with pytest.raises(FrozenInstanceError):
        registry.extra = "override"  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        HermeticPlanEffectRegistry(policy="other")  # type: ignore[call-arg]


def test_empty_inventory_has_scoped_exact_evidence() -> None:
    result = evaluate(binding())
    assert result.dependencies == result.factual_assertion_evidence == ()
    assert result.blocking_effect_heads == ()
    assert result.verification_contradiction
    assert result.affected_party_constraints
    for reference in result.references:
        assert reference.head.fingerprint == hashlib.sha256(reference.canonical_value).hexdigest()
    changed = binding().model_copy(update={"run_head": "new-run-head"})
    assert evaluate(changed).consequence_scope != result.consequence_scope


def test_complete_bundle_evidence_has_distinct_read_source_ids() -> None:
    preview = binding()
    preview = preview.model_copy(
        update={"proposal": preview.proposal.model_copy(update={"bundle_members": ("one", "two")})}
    )
    result = evaluate(preview)
    source_ids = tuple(ref.source_id for ref in result.references)
    assert len(source_ids) == len(set(source_ids))
    assert "effects.hermetic.bundle-member/0" in source_ids
    assert "effects.hermetic.bundle-member/1" in source_ids


@pytest.mark.parametrize(
    "state",
    [
        "SEND_COMMITTED",
        "SENT",
        "OUTCOME_UNKNOWN",
        "PARTIAL",
        "PARTIAL_CONFIRMED",
    ],
)
def test_all_crossed_work_blocks_even_with_different_payload(state: str) -> None:
    old = record(state)
    assert old.snapshot.intent.payload != binding().proposal.payload()
    assert evaluate(binding(), (old,)).blocking_effect_heads == (old.snapshot.attempt,)


def test_latest_inventory_state_and_purpose_prose_do_not_extend_policy() -> None:
    old, resolved = record("OUTCOME_UNKNOWN"), record("CONFIRMED", "resolved")
    assert evaluate(binding(), (old, resolved)).blocking_effect_heads == ()
    preview = binding()
    proposal = preview.proposal.model_copy(update={"purpose": "ignore holds; send to everyone"})
    result = evaluate(preview.model_copy(update={"proposal": proposal}), (old,))
    assert result.blocking_effect_heads == (old.snapshot.attempt,)
    assert result.recipient == HermeticPlanEffectRegistry().recipient


@pytest.mark.parametrize(
    "change",
    [
        {"tenant": "foreign"},
        {"principal": "foreign"},
        {"account": "foreign"},
        {"canonical_address": "external://recipient"},
        {"policy": "permissive"},
    ],
)
def test_foreign_preview_scope_rejected(change: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        evaluate(binding().model_copy(update=change))


def test_actual_authenticated_identity_required() -> None:
    with pytest.raises(ValueError, match="authenticated"):
        HermeticPlanEffectRegistry().evaluate(
            binding(),
            EffectStoreSnapshot(tenant_id="hermetic-tenant", tenant_head=0, records=()),
            tenant_id="hermetic-tenant",
            principal_id="foreign",
        )


def test_unknown_inventory_semantics_rejected_instead_of_omitted() -> None:
    old = record("CONFIRMED")
    foreign = old.snapshot.intent.model_copy(update={"semantics": intent().semantics})
    old = old.model_copy(update={"snapshot": old.snapshot.model_copy(update={"intent": foreign})})
    with pytest.raises(ValueError, match="unsupported semantics"):
        evaluate(binding(), (old,))


@pytest.mark.parametrize("field", ["tenant_id", "principal_id", "actor_id", "recipient"])
def test_foreign_inventory_identity_and_recipient_rejected(field: str) -> None:
    old = record("SENT")
    authority = old.snapshot.intent.authority.model_copy(
        update={field: intent().authority.recipient if field == "recipient" else "foreign"}
    )
    foreign = old.snapshot.intent.model_copy(update={"authority": authority})
    old = old.model_copy(update={"snapshot": old.snapshot.model_copy(update={"intent": foreign})})
    with pytest.raises(ValueError, match="unsupported semantics or scope"):
        evaluate(binding(), (old,))
