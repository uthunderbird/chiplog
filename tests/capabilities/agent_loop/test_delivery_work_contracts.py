"""Owner-local delivery-work genesis and initial claim preserve selected bytes."""

import pytest

from chiplog.capabilities.agent_loop.delivery_work_contracts import (
    DELIVERY_WORK_CLAIM_COMMAND_SCHEMA,
    DELIVERY_WORK_OWNER,
    DeliveryWorkClaimProjectionV1,
    DeliveryWorkSourceMemberV1,
    DeliveryWorkSourceV1,
    PrepareDeliveryWorkClaimV1,
    delivery_work_record_id,
    delivery_work_subject_id,
    prepare_delivery_work_claim,
    prepare_delivery_work_claim_projection,
    prepare_delivery_work_genesis,
)


def _member(
    owner: str, kind: str, record_id: str, schema: str, raw: bytes
) -> DeliveryWorkSourceMemberV1:
    import hashlib

    return DeliveryWorkSourceMemberV1(
        owner=owner,
        record_kind=kind,
        record_id=record_id,
        schema_id=schema,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def _source(intent_id: str = "intent") -> DeliveryWorkSourceV1:
    return DeliveryWorkSourceV1(
        tenant_id="tenant",
        intent_id=intent_id,
        selected_decision_id="decision",
        selected_decision_fingerprint="a" * 64,
        terminal_run=_member(DELIVERY_WORK_OWNER, "Run", "run", "run.v1", b"run"),
        terminal_manifest=_member(
            DELIVERY_WORK_OWNER, "TerminalManifest", "manifest", "manifest.v1", b"manifest"
        ),
        intent=_member("effects", "ScopedIntent", intent_id, "intent.v3", b"intent"),
    )


def test_genesis_and_claim_bind_exact_predecessor_source_and_lease() -> None:
    source = _source()
    genesis = prepare_delivery_work_genesis(source)
    assert genesis.state == "UNLEASED" and genesis.generation == 0
    assert genesis.record_id == delivery_work_record_id(source.subject_id, 0)
    claim = prepare_delivery_work_claim(
        source,
        genesis,
        holder_id="holder",
        session_id="session",
        trusted_clock_ns=10,
        lease_expires_at_ns=20,
    )
    assert claim.state == "CLAIMED" and claim.generation == 1
    assert claim.predecessor_head == genesis.head
    assert claim.record_id == delivery_work_record_id(source.subject_id, 1)


def test_claim_rejects_rival_stale_and_mutated_selected_sources() -> None:
    source = _source()
    genesis = prepare_delivery_work_genesis(source)
    with pytest.raises(ValueError, match="predecessor"):
        prepare_delivery_work_claim(
            _source("rival"),
            genesis,
            holder_id="h",
            session_id="s",
            trusted_clock_ns=1,
            lease_expires_at_ns=2,
        )
    claimed = prepare_delivery_work_claim(
        source, genesis, holder_id="h", session_id="s", trusted_clock_ns=1, lease_expires_at_ns=2
    )
    with pytest.raises(ValueError, match="predecessor"):
        prepare_delivery_work_claim(
            source,
            claimed,
            holder_id="h",
            session_id="s",
            trusted_clock_ns=3,
            lease_expires_at_ns=4,
        )
    with pytest.raises(ValueError, match="fingerprint"):
        DeliveryWorkSourceV1.model_validate(
            _source().model_dump(mode="python")
            | {
                "intent": _source().intent.model_dump(mode="python")
                | {"canonical_record_bytes": b"changed"}
            }
        )


def test_independent_intents_get_independent_reserved_subjects() -> None:
    first, second = _source("first"), _source("second")
    assert first.subject_id != second.subject_id
    assert (
        prepare_delivery_work_genesis(first).record_id
        != prepare_delivery_work_genesis(second).record_id
    )


def test_subject_preimage_cannot_collide_when_identities_contain_separators() -> None:
    assert delivery_work_subject_id("a\x00b", "c") != delivery_work_subject_id("a", "b\x00c")


def test_intent_identity_must_match_the_exact_retained_effects_member() -> None:
    with pytest.raises(ValueError, match="wrong owners or duplicate"):
        DeliveryWorkSourceV1.model_validate(
            _source().model_dump(mode="python") | {"intent_id": "rival-intent"}
        )


def test_first_claim_command_carries_only_exact_authority_free_projection() -> None:
    source = _source()
    predecessor = prepare_delivery_work_genesis(source)
    projection = prepare_delivery_work_claim_projection(source, predecessor)
    command = PrepareDeliveryWorkClaimV1(projection=projection)

    assert command.schema_id == DELIVERY_WORK_CLAIM_COMMAND_SCHEMA
    assert projection.predecessor_head == predecessor.head
    assert projection.record_id == delivery_work_record_id(source.subject_id, 1)
    assert set(command.model_dump()) == {"schema_id", "projection"}
    assert command.projection.predecessor.holder_id is None
    assert command.projection.predecessor.trusted_clock_ns is None


def test_first_claim_projection_rejects_a_substituted_or_non_genesis_predecessor() -> None:
    source = _source()
    predecessor = prepare_delivery_work_genesis(source)
    projection = prepare_delivery_work_claim_projection(source, predecessor)
    with pytest.raises(ValueError, match="exact unleased genesis"):
        DeliveryWorkClaimProjectionV1.model_validate(
            projection.model_dump(mode="python") | {"predecessor_head": "foreign-head"}
        )
    claimed = prepare_delivery_work_claim(
        source,
        predecessor,
        holder_id="h",
        session_id="s",
        trusted_clock_ns=1,
        lease_expires_at_ns=2,
    )
    with pytest.raises(ValueError, match="exact unleased genesis"):
        prepare_delivery_work_claim_projection(source, claimed)
