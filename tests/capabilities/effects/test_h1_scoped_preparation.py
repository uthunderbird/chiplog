"""H1 scoped producer checks use a real loop completion and owner envelope."""

from __future__ import annotations

import base64

import pytest
from tests.support.capabilities.effects.h1_scoped_preparation import (
    _call,
    _h1_sources,
    _sha,
)

from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyV1,
)
from chiplog.capabilities.effects._h1_scoped_process import ROUTE, ROUTES, dispatch
from chiplog.capabilities.effects.contracts import ExactHead as EffectHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.h1_local_preparation_contracts import PREPARE_SCHEMA
from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryV1,
    h1_effects_history_capture,
    h1_effects_history_digest,
    h1_effects_history_head,
    h1_normative_conflict_generation,
    h1_normative_conflict_generation_capture,
)
from chiplog.capabilities.effects.h1_producer_semantics import (
    H1_PRODUCER_SEMANTICS,
)
from chiplog.capabilities.effects.h1_producer_source_contracts import (
    H1ProducerNotApplicableV1,
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
    H1ScopedDeliveryOwnerCallV1,
    PreparedH1ScopedDeliveryV1,
    PrepareH1ScopedDeliveryV1,
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
    assert (
        prepare_h1_scoped_delivery(
            _with_request(
                call, call.request.model_copy(update={"original_sources": original_sources})
            )
        ).disposition
        == "DENIED"
    )


async def test_scoped_owner_requires_current_h1_semantic_registry_capture_to_match_original() -> (
    None
):
    call = await _call()
    current_registry = call.request.current.sources.semantic_registry.model_copy(
        update={"reader_id": "forged-reader"}
    )
    current_sources = call.request.current.sources.model_copy(
        update={"semantic_registry": current_registry}
    )
    current = call.request.current.model_copy(update={"sources": current_sources})
    assert (
        prepare_h1_scoped_delivery(
            _with_request(call, call.request.model_copy(update={"current": current}))
        ).disposition
        == "DENIED"
    )


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
    assert (
        prepare_h1_scoped_delivery(
            _with_request(call, call.request.model_copy(update={"current": current}))
        ).disposition
        == "DENIED"
    )


async def test_scoped_owner_requires_equal_original_history_and_generation_captures() -> None:
    call = await _call()
    changed_history = call.request.original_sources.effects_history.model_copy(
        update={"reader_id": "another-reader"}
    )
    original_sources = call.request.original_sources.model_copy(
        update={"effects_history": changed_history}
    )
    assert (
        prepare_h1_scoped_delivery(
            _with_request(
                call, call.request.model_copy(update={"original_sources": original_sources})
            )
        ).disposition
        == "DENIED"
    )

    changed_generation = call.request.original_sources.normative_conflict_generation.model_copy(
        update={"reader_id": "another-reader"}
    )
    original_sources = call.request.original_sources.model_copy(
        update={"normative_conflict_generation": changed_generation}
    )
    assert (
        prepare_h1_scoped_delivery(
            _with_request(
                call, call.request.model_copy(update={"original_sources": original_sources})
            )
        ).disposition
        == "DENIED"
    )


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
    assert (
        prepare_h1_scoped_delivery(
            _with_request(call, call.request.model_copy(update={"current": current}))
        ).disposition
        == "DENIED"
    )


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
    forged_planning = planning.model_copy(update={"candidate": forged_candidate})
    forged_sources = call.request.original_sources.model_copy(update={"planning": forged_planning})
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
