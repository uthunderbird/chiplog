from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.domain_primitives import (
    CanonicalBytes,
    CanonicalizationVersion,
    CodecVersion,
    OwnerTag,
    PreservedBytes,
    ProducingVersions,
    RecordId,
    RecordTypeId,
    SchemaId,
    TenantId,
)
from chiplog.domain_primitives.codec import (
    admit_exact_version,
    canonical_record_bytes,
    fingerprint,
    verify_preserved_bytes,
)

VERSIONS = ProducingVersions(
    SchemaId("planning", "revision", 1), CodecVersion(1), CanonicalizationVersion(1)
)

RECORD_ID = RecordId(TenantId("tenant-01"), "revision-01")

RECORD_TYPE = RecordTypeId("planning", "PlanningRevision")

OWNER = OwnerTag("planning")

FIELDS = {"action": "CREATE_ROOT", "ordinal": 1, "title": "Купить молоко"}

GOLDEN = (
    b'{"fields":{"action":"CREATE_ROOT","ordinal":1,"title":"'
    b"\xd0\x9a\xd1\x83\xd0\xbf\xd0\xb8\xd1\x82\xd1\x8c \xd0\xbc\xd0\xbe\xd0\xbb"
    b'\xd0\xbe\xd0\xba\xd0\xbe"},"owner":"planning","record_id":{"tenant_id":"tenant-01",'
    b'"value":"revision-01"},"record_type":{"name":"PlanningRevision","namespace":"planning"},'
    b'"versions":{"canonicalization":1,"codec":1,"schema":{"name":"revision","namespace":"planning","version":1}}}'
)


def _canonical(
    *,
    record_id: RecordId = RECORD_ID,
    record_type: RecordTypeId = RECORD_TYPE,
    owner: OwnerTag = OWNER,
    versions: ProducingVersions = VERSIONS,
    fields: dict[str, object] | None = None,
) -> CanonicalBytes:
    return canonical_record_bytes(record_id, record_type, owner, versions, fields or FIELDS)


def test_v1_golden_bytes_bind_every_producing_identity() -> None:
    canonical = _canonical()

    assert canonical.payload == GOLDEN
    assert canonical.producing_versions == VERSIONS
    assert fingerprint(canonical).digest.hex() == (
        "706d25f4e62aada3f46d6ebe40578f6ae7603f342c43eb8769d6c438622ded9d"
    )


@pytest.mark.parametrize(
    "mutated",
    [
        _canonical(record_id=RecordId(TenantId("tenant-02"), "revision-01")),
        _canonical(record_id=RecordId(TenantId("tenant-01"), "revision-02")),
        _canonical(record_type=RecordTypeId("journal", "PlanningRevision")),
        _canonical(record_type=RecordTypeId("planning", "OtherRevision")),
        _canonical(owner=OwnerTag("journal")),
        _canonical(versions=replace(VERSIONS, schema_id=SchemaId("planning", "revision", 2))),
        _canonical(versions=replace(VERSIONS, codec=CodecVersion(2))),
        _canonical(versions=replace(VERSIONS, canonicalization=CanonicalizationVersion(2))),
        _canonical(fields={**FIELDS, "ordinal": 2}),
    ],
)
def test_v1_one_field_mutation_changes_bytes_and_fingerprint(mutated: object) -> None:
    original = _canonical()
    assert mutated != original
    assert fingerprint(mutated) != fingerprint(original)  # type: ignore[arg-type]


def test_v1_historical_bytes_verify_with_producing_not_current_versions() -> None:
    historical = PreservedBytes(GOLDEN, VERSIONS)
    expected = fingerprint(_canonical())

    assert verify_preserved_bytes(historical, expected)
    relabelled = replace(
        historical,
        producing_versions=replace(VERSIONS, canonicalization=CanonicalizationVersion(2)),
    )
    assert relabelled.original == GOLDEN
    assert not verify_preserved_bytes(relabelled, expected)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"store_version": 2}, "unknown store version"),
        ({"supported_schemas": frozenset()}, "unknown schema version"),
        ({"supported_codec_versions": frozenset()}, "unknown codec version"),
        (
            {"supported_canonicalization_versions": frozenset()},
            "unknown canonicalization version",
        ),
    ],
)
def test_v1_unknown_exact_version_holds_without_write(
    overrides: dict[str, object], reason: str
) -> None:
    writes: list[object] = []
    arguments: dict[str, object] = {
        "store_version": 1,
        "supported_store_versions": frozenset({1}),
        "supported_schemas": frozenset({VERSIONS.schema_id}),
        "supported_codec_versions": frozenset({1}),
        "supported_canonicalization_versions": frozenset({1}),
        "write": lambda preserved, observed: writes.append((preserved, observed)),
    }
    arguments.update(overrides)

    result = admit_exact_version(PreservedBytes(GOLDEN, VERSIONS), **arguments)  # type: ignore[arg-type]

    assert result.status == "HOLD"
    assert result.reason == reason
    assert result.fingerprint is None
    assert writes == []


def test_v1_runtime_admission_preserves_original_bytes_and_writes_once() -> None:
    preserved = PreservedBytes(GOLDEN, VERSIONS)
    writes: list[object] = []

    result = admit_exact_version(
        preserved,
        store_version=1,
        supported_store_versions=frozenset({1}),
        supported_schemas=frozenset({VERSIONS.schema_id}),
        supported_codec_versions=frozenset({1}),
        supported_canonicalization_versions=frozenset({1}),
        write=lambda original, observed: writes.append((original, observed)),
    )

    assert result.status == "ADMITTED"
    assert result.preserved is preserved
    assert writes == [(preserved, result.fingerprint)]
