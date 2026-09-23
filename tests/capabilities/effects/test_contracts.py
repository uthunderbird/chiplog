"""Public consumer shape evidence only; these tests do not prove dispatch behavior."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.contracts import (
    ClosedEffectObligation,
    CurrentEffectInputs,
    DeliverySendBinding,
    DispatchSemanticBinding,
    ExactHead,
    FirstTransmission,
    ModelSelection,
    OpenEffectObligation,
    OriginSelection,
    ProviderRecipient,
    SafeRetransmission,
)


def test_broker_blocker_inventory_is_required_exact_heads() -> None:
    schema = CurrentEffectInputs.model_json_schema()
    assert "blocking_effect_heads" in schema["required"]
    assert schema["properties"]["blocking_effect_heads"]["items"] == {"$ref": "#/$defs/ExactHead"}


def test_delivery_consumer_preserves_exact_explicit_origin_tuple() -> None:
    head = ExactHead(subject_id="endpoint", head="head-1", fingerprint="fp-1")
    recipient = ProviderRecipient(
        provider="hermetic",
        account="account",
        recipient="principal",
        endpoint=head,
        canonical_address=b"local://principal",
        credential_binding=head,
    )
    origin = OriginSelection(kind="ORIGIN_EXACT", ingress_binding=head, recipient=recipient)
    delivery = DeliverySendBinding(
        delivery_id="delivery",
        acceptance=head,
        render_digest="render",
        manifest_digest="manifest",
        selection=origin,
        visibility=(head,),
        provenance=(head,),
        disclosure=(head,),
        narrowing=(),
        policy=head,
    )
    assert delivery.selection == origin
    with pytest.raises(ValidationError):
        ModelSelection.model_validate(
            {"kind": "MODEL_SELECTED_EXACT", "recipient": recipient, "ingress_binding": head}
        )
    with pytest.raises(ValidationError):
        DeliverySendBinding.model_validate({**delivery.model_dump(), "selection": None})


def test_semantic_binding_requires_every_version_and_rejects_unknown_fields() -> None:
    values = {
        "normative_manifest": "vision-dispatch-v1",
        "reducer_version": "1",
        "transition_registry_version": "1",
        "canonicalization_fingerprint_version": "1",
        "adapter_contract_version": "fake-1",
    }
    binding = DispatchSemanticBinding.model_validate(values)
    for field in values:
        with pytest.raises(ValidationError):
            DispatchSemanticBinding.model_validate({k: v for k, v in values.items() if k != field})
    with pytest.raises(ValidationError):
        DispatchSemanticBinding.model_validate({**values, "compatible": True})
    with pytest.raises(ValidationError):
        binding.reducer_version = "changed"


def test_transmission_variants_cannot_hide_prior_children_or_reset_ordinal() -> None:
    assert FirstTransmission(kind="FIRST_TRANSMISSION", ordinal=0).ordinal == 0
    head = ExactHead(subject_id="child", head="head-1", fingerprint="fp-1")
    values = {
        "kind": "SAFE_RETRANSMISSION",
        "ordinal": 1,
        "prior_children": (head,),
        "proof_kind": "PROVIDER_IDEMPOTENCY_COVERS_ALL",
        "proof": head,
        "covered_effect_fingerprint": "effect",
        "coverage_starts_ns": 1,
        "coverage_expires_ns": 10,
    }
    assert SafeRetransmission.model_validate(values).prior_children == (head,)
    for mutation in (
        {"ordinal": 0},
        {"ordinal": 2**64},
        {"prior_children": ()},
        {"proof_kind": "ABSENCE_AT_READ"},
    ):
        with pytest.raises(ValidationError):
            SafeRetransmission.model_validate({**values, **mutation})


def test_owner_local_fence_wire_matches_frozen_public_schema() -> None:
    from chiplog.capabilities.agent_loop import recovery_contracts as source
    from chiplog.capabilities.effects import fences as consumer

    for name in ("NonSchedulerFence", "SchedulerExecutionFence", "PostTerminalWorkFence"):
        assert (
            getattr(source, name).model_json_schema() == getattr(consumer, name).model_json_schema()
        )
    original = source.NonSchedulerFence(
        lineage=source.NotApplicable(),
        physical_root=source.NotApplicable(),
        lease=source.NotApplicable(),
        clock_proof=source.NotApplicable(),
        run_id="run",
        run_head="run-head",
        worker_session_id="session",
        runtime_generation="gen",
    )
    translated = consumer.NonSchedulerFence.model_validate_json(original.canonical_bytes())
    assert translated.canonical_bytes() == original.canonical_bytes()
    for omitted in ("physical_root", "worker_session_id", "runtime_generation"):
        values = translated.model_dump()
        del values[omitted]
        with pytest.raises(ValidationError):
            consumer.NonSchedulerFence.model_validate(values)


def test_arbitrary_raw_bytes_roundtrip_without_utf8_interpretation() -> None:
    head = ExactHead(subject_id="endpoint", head="head", fingerprint="fp")
    value = ProviderRecipient(
        provider="fake",
        account="account",
        recipient="recipient",
        endpoint=head,
        canonical_address=bytes(range(256)),
        credential_binding=head,
    )
    assert ProviderRecipient.model_validate_json(value.canonical_bytes()) == value


def test_effect_obligation_preserves_original_opening_in_terminal_successor() -> None:
    head = ExactHead(subject_id="obligation", head="opening", fingerprint="fingerprint")
    binding = DispatchSemanticBinding(
        normative_manifest="manifest",
        reducer_version="1",
        transition_registry_version="1",
        canonicalization_fingerprint_version="1",
        adapter_contract_version="1",
    )
    opening = OpenEffectObligation(
        obligation=head,
        intent=head,
        original_attempt=head,
        original_crossed_children=(head,),
        semantics=binding,
        safe_actions=("BOUND_RECONCILIATION",),
        denied_actions=("BLIND_RETRY",),
    )
    closure = ClosedEffectObligation(
        obligation=head.model_copy(update={"head": "closed"}),
        opening=opening,
        closure_evidence=(head,),
        complete_children=(head,),
        outcome="CONFIRMED",
        semantics=binding,
    )
    assert ClosedEffectObligation.model_validate_json(closure.canonical_bytes()).opening == opening
    with pytest.raises(ValidationError):
        ClosedEffectObligation.model_validate({**closure.model_dump(), "closure_evidence": ()})
