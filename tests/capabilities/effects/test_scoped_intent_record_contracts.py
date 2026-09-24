"""Exact physical and semantic V3 scoped-intent record contracts."""

import hashlib

import pytest
from pydantic import ValidationError
from tests.support.scoped_intent_records import delivery_publication_fixture

from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryOriginV3
from chiplog.capabilities.effects.scoped_intent_record_contracts import (
    ScopedIntentCanonicalMemberV3,
    ScopedIntentRecordIntegrityError,
    decode_scoped_intent_member,
    make_scoped_intent_member,
    scoped_intent_complete_owner_commitment,
    scoped_intent_fingerprint,
    validate_scoped_intent_publication,
)


@pytest.mark.parametrize("delivery_index", [0, 1])
async def test_v3_delivery_intent_has_native_identity_and_distinct_physical_hash(
    delivery_index: int,
) -> None:
    fixture = await delivery_publication_fixture(delivery_index)
    member = validate_scoped_intent_publication(fixture.request, fixture.result, fixture.retained)

    assert member.record_kind == "ExternalActionIntent"
    assert member.schema_id == "chiplog.effects.external-action-intent.v3"
    assert member.record_id == fixture.intent.intent_id
    assert member.canonical_record_bytes == fixture.intent.canonical_bytes()
    assert member.fingerprint == hashlib.sha256(fixture.intent.canonical_bytes()).hexdigest()
    assert fixture.intent.fingerprint == scoped_intent_fingerprint(fixture.intent)
    assert member.fingerprint != fixture.intent.fingerprint
    assert decode_scoped_intent_member(member) == fixture.intent


async def test_decoder_rejects_semantic_fingerprint_even_with_valid_physical_hash() -> None:
    fixture = await delivery_publication_fixture()
    bad_intent = fixture.intent.model_copy(update={"fingerprint": "0" * 64})
    bad_member = make_scoped_intent_member(bad_intent)

    with pytest.raises(ScopedIntentRecordIntegrityError, match="semantic intent fingerprint"):
        decode_scoped_intent_member(bad_member)


async def test_decoder_rejects_physical_hash_even_with_semantic_intent() -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    bad_member = member.model_copy(update={"fingerprint": "0" * 64})

    with pytest.raises(ScopedIntentRecordIntegrityError, match="member fingerprint"):
        decode_scoped_intent_member(bad_member)


async def test_decoder_rejects_envelope_substitution() -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    bad_member = member.model_copy(update={"record_id": "another-intent"})

    with pytest.raises(ScopedIntentRecordIntegrityError, match="envelope differs"):
        decode_scoped_intent_member(bad_member)


async def test_publication_rejects_each_retained_preimage_mutation() -> None:
    fixture = await delivery_publication_fixture()
    fields = (
        "precursor_request_bytes",
        "precursor_result_bytes",
        "mandate_bytes",
        "acquisition_bytes",
    )
    for field in fields:
        retained = fixture.retained.__class__(**{**fixture.retained.__dict__, field: b"{}"})
        with pytest.raises(ScopedIntentRecordIntegrityError, match="retained"):
            validate_scoped_intent_publication(fixture.request, fixture.result, retained)


async def test_publication_rejects_request_result_reference_and_commitment_mutations() -> None:
    fixture = await delivery_publication_fixture()
    wrong_request = fixture.request.model_copy(
        update={"intent": fixture.intent.model_copy(update={"intent_id": "other"})}
    )
    with pytest.raises(ScopedIntentRecordIntegrityError, match="request and result"):
        validate_scoped_intent_publication(wrong_request, fixture.result, fixture.retained)

    wrong_result = fixture.result.model_copy(update={"source_request_fingerprint": "0" * 64})
    with pytest.raises(ScopedIntentRecordIntegrityError, match="source request"):
        validate_scoped_intent_publication(fixture.request, wrong_result, fixture.retained)

    wrong_refs = fixture.result.model_copy(update={"original_references": ()})
    with pytest.raises(ScopedIntentRecordIntegrityError, match="original references"):
        validate_scoped_intent_publication(fixture.request, wrong_refs, fixture.retained)

    wrong_commitment = fixture.result.model_copy(update={"complete_owner_commitment": "0" * 64})
    with pytest.raises(ScopedIntentRecordIntegrityError, match="complete owner commitment"):
        validate_scoped_intent_publication(fixture.request, wrong_commitment, fixture.retained)


async def test_publication_rejects_inner_precursor_and_delivery_digest_mutations() -> None:
    fixture = await delivery_publication_fixture()
    acquisition = fixture.intent.acquisition
    wrong_precursor = acquisition.precursor_result.model_copy(
        update={"mandate_fingerprint": "0" * 64}
    )
    wrong_acquisition = acquisition.model_copy(update={"precursor_result": wrong_precursor})
    wrong_intent = fixture.intent.model_copy(update={"acquisition": wrong_acquisition})
    wrong_intent = wrong_intent.model_copy(
        update={"fingerprint": scoped_intent_fingerprint(wrong_intent)}
    )
    with pytest.raises(ScopedIntentRecordIntegrityError, match="mandate fingerprint"):
        decode_scoped_intent_member(make_scoped_intent_member(wrong_intent))

    assert isinstance(fixture.intent.mandate.origin, PreparedDeliveryOriginV3)
    origin = fixture.intent.mandate.origin.model_copy(
        update={
            "binding": fixture.intent.mandate.origin.binding.model_copy(
                update={"render_digest": "0" * 64}
            )
        }
    )
    wrong_mandate = fixture.intent.mandate.model_copy(update={"origin": origin})
    wrong_precursor_request = fixture.intent.acquisition.precursor_request.model_copy(
        update={"mandate": wrong_mandate}
    )
    wrong_precursor_result = fixture.intent.acquisition.precursor_result.model_copy(
        update={
            "source_request_fingerprint": hashlib.sha256(
                wrong_precursor_request.canonical_bytes()
            ).hexdigest(),
            "mandate_fingerprint": hashlib.sha256(wrong_mandate.canonical_bytes()).hexdigest(),
        }
    )
    wrong_authority = fixture.intent.acquisition.authority.model_copy(
        update={"exact_mandate_bytes": wrong_mandate.canonical_bytes()}
    )
    wrong_acquisition = fixture.intent.acquisition.model_copy(
        update={
            "authority": wrong_authority,
            "precursor_request": wrong_precursor_request,
            "precursor_result": wrong_precursor_result,
        }
    )
    wrong_intent = fixture.intent.model_copy(
        update={"mandate": wrong_mandate, "acquisition": wrong_acquisition}
    )
    wrong_intent = wrong_intent.model_copy(
        update={"fingerprint": scoped_intent_fingerprint(wrong_intent)}
    )
    with pytest.raises(ScopedIntentRecordIntegrityError, match="intent does not bind"):
        decode_scoped_intent_member(make_scoped_intent_member(wrong_intent))


async def test_commitment_binds_ordered_descriptors_and_original_references() -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    reversed_sources = tuple(reversed(fixture.retained.original_sources))

    assert (
        scoped_intent_complete_owner_commitment(member, reversed_sources)
        != fixture.result.complete_owner_commitment
    )


async def test_publication_rejects_coordinated_foreign_sources_and_recomputed_commitment() -> None:
    fixture = await delivery_publication_fixture()
    source = fixture.retained.original_sources[0]
    foreign_bytes = b"foreign retained source"
    foreign_source = source.model_copy(
        update={
            "canonical_record_bytes": foreign_bytes,
            "reference": source.reference.model_copy(
                update={
                    "subject_id": "foreign-source",
                    "head": "foreign-head",
                    "fingerprint": hashlib.sha256(foreign_bytes).hexdigest(),
                }
            ),
        }
    )
    foreign_sources = (foreign_source, *fixture.retained.original_sources[1:])
    retained = fixture.retained.__class__(
        **{**fixture.retained.__dict__, "original_sources": foreign_sources}
    )
    member = make_scoped_intent_member(fixture.intent)
    result = fixture.result.model_copy(
        update={
            "original_references": retained.original_references,
            "complete_owner_commitment": scoped_intent_complete_owner_commitment(
                member, retained.original_sources
            ),
        }
    )

    with pytest.raises(ScopedIntentRecordIntegrityError, match="retained original source roles"):
        validate_scoped_intent_publication(fixture.request, result, retained)


async def test_publication_rejects_coordinated_duplicate_missing_and_metadata_roles() -> None:
    fixture = await delivery_publication_fixture()
    member = make_scoped_intent_member(fixture.intent)
    first = fixture.retained.original_sources[0]
    variants = (
        (first, first, *fixture.retained.original_sources[2:]),
        fixture.retained.original_sources[1:],
        (
            first.model_copy(update={"schema_id": "foreign.schema.v1"}),
            *fixture.retained.original_sources[1:],
        ),
        (
            first.model_copy(
                update={
                    "selected_decision": first.selected_decision.model_copy(
                        update={"head": "foreign-selected-decision"}
                    )
                }
            ),
            *fixture.retained.original_sources[1:],
        ),
    )
    for original_sources in variants:
        retained = fixture.retained.__class__(
            **{**fixture.retained.__dict__, "original_sources": original_sources}
        )
        result = fixture.result.model_copy(
            update={
                "original_references": retained.original_references,
                "complete_owner_commitment": scoped_intent_complete_owner_commitment(
                    member, retained.original_sources
                ),
            }
        )
        with pytest.raises(
            ScopedIntentRecordIntegrityError, match="retained original source roles"
        ):
            validate_scoped_intent_publication(fixture.request, result, retained)


def test_member_wire_rejects_unknown_fields_and_nonhex_fingerprint() -> None:
    with pytest.raises(ValidationError):
        ScopedIntentCanonicalMemberV3.model_validate(
            {
                "record_kind": "ExternalActionIntent",
                "schema_id": "chiplog.effects.external-action-intent.v3",
                "record_id": "intent",
                "canonical_record_bytes": b"body",
                "fingerprint": "not-a-digest",
                "injected_decoder": "no",
            }
        )
