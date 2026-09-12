from __future__ import annotations

from dataclasses import replace

from chiplog.architecture.r4_r5_freeze import (
    R4_R5_ARCHITECTURE,
    R4_R5_CAPABILITIES,
    R4_R5_EXPORTS,
    R4_R5_PACKAGES,
    R4_R5_RECORDS,
    R4_R5_SURFACES,
    verify_r4_r5_freeze,
)


def test_r4_r5_freeze_is_closed_and_ordered() -> None:
    verify_r4_r5_freeze()


def test_frozen_signatures_bind_field_shape() -> None:
    target = R4_R5_EXPORTS[0]
    changed = replace(target, fields=(*target.fields, ("payload_tenant", "str")))
    assert changed.signature_digest != target.signature_digest


def test_record_type_and_schema_id_are_distinct_bindings() -> None:
    assert len({item.record_type_id for item in R4_R5_RECORDS}) == len(R4_R5_RECORDS)
    assert all(item.record_type_id != item.schema_id for item in R4_R5_RECORDS)


def test_r2_manifest_is_the_same_frozen_universe() -> None:
    assert tuple(item.name for item in R4_R5_ARCHITECTURE.packages) == tuple(
        name for name, _ in R4_R5_PACKAGES
    )
    assert tuple(item.name for item in R4_R5_ARCHITECTURE.capabilities) == R4_R5_CAPABILITIES
    assert tuple(item.name for item in R4_R5_ARCHITECTURE.surfaces) == R4_R5_SURFACES
