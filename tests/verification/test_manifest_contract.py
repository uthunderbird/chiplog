from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.architecture import (
    EMPTY_PRODUCTION_GENERATION,
    ArchitectureManifest,
    ArchitectureViolation,
    ExportDecl,
    PackageDecl,
    verify_manifest,
)
from tests.support.manifest import REFERENCE


def test_reference_is_non_vacuous_and_bound() -> None:
    assert len({item.owner for item in REFERENCE.record_ownership}) >= 2
    assert {item.visibility for item in REFERENCE.exports} == {"private", "public"}
    assert any(item.kind == "reverse" for item in REFERENCE.routes)
    assert verify_manifest(REFERENCE, REFERENCE) == REFERENCE.fingerprint()


def test_named_empty_production_generation_is_not_evidence() -> None:
    assert EMPTY_PRODUCTION_GENERATION.generation == "production-stage0-not-materialized-v1"
    assert not EMPTY_PRODUCTION_GENERATION.evidentiary
    with pytest.raises(ArchitectureViolation, match="VACUOUS_EVIDENCE"):
        verify_manifest(
            replace(EMPTY_PRODUCTION_GENERATION, evidentiary=True),
            replace(EMPTY_PRODUCTION_GENERATION, evidentiary=True),
        )


def test_sparse_generation_cannot_self_certify_as_evidence() -> None:
    sparse = replace(
        EMPTY_PRODUCTION_GENERATION,
        generation="fake-evidence",
        evidentiary=True,
        packages=(PackageDecl("chiplog.capabilities.x", "capability", "x"),),
    )
    with pytest.raises(ArchitectureViolation, match="VACUOUS_EVIDENCE"):
        verify_manifest(sparse, sparse)


def test_r1_signature_set_is_not_caller_selected() -> None:
    with pytest.raises(ArchitectureViolation, match="R1_SIGNATURE_SET"):
        verify_manifest(
            replace(REFERENCE, r1_signatures=()),
            replace(REFERENCE, r1_signatures=()),
        )


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        (
            replace(REFERENCE, packages=REFERENCE.packages[:-1]),
            "UNKNOWN_REFERENCE",
        ),
        (
            replace(
                REFERENCE,
                packages=(
                    *REFERENCE.packages,
                    PackageDecl("chiplog.unknown", "platform", "platform"),
                ),
            ),
            "CLOSED_SET",
        ),
        (replace(REFERENCE, exports=REFERENCE.exports[:-1]), "UNKNOWN_EXPORT"),
        (
            replace(
                REFERENCE,
                exports=tuple(
                    sorted(
                        (
                            *REFERENCE.exports,
                            ExportDecl(
                                "chiplog.composition:Extra",
                                "chiplog.composition",
                                "public",
                                "0" * 64,
                                "",
                            ),
                        ),
                        key=lambda export: export.reference,
                    )
                ),
            ),
            "CLOSED_SET",
        ),
        (replace(REFERENCE, capabilities=REFERENCE.capabilities[:-1]), "UNKNOWN_REFERENCE"),
        (replace(REFERENCE, record_ownership=REFERENCE.record_ownership[:-1]), "CLOSED_SET"),
        (replace(REFERENCE, bridges=REFERENCE.bridges[:-1]), "UNKNOWN_REFERENCE"),
        (replace(REFERENCE, providers=REFERENCE.providers[:-1]), "CLOSED_SET"),
        (replace(REFERENCE, surfaces=REFERENCE.surfaces[:-1]), "UNKNOWN_REFERENCE"),
        (replace(REFERENCE, executable_roots=()), "ROOT_COVERAGE"),
    ],
)
def test_closed_registries_reject_omissions_and_additions(
    candidate: ArchitectureManifest, code: str
) -> None:
    with pytest.raises(ArchitectureViolation, match=code):
        verify_manifest(REFERENCE, candidate)


def test_duplicate_and_reorder_are_distinct_failures() -> None:
    with pytest.raises(ArchitectureViolation, match="DUPLICATE"):
        verify_manifest(
            REFERENCE,
            replace(
                REFERENCE,
                record_ownership=(
                    *REFERENCE.record_ownership,
                    REFERENCE.record_ownership[0],
                ),
            ),
        )
    with pytest.raises(ArchitectureViolation, match="REORDER"):
        verify_manifest(REFERENCE, replace(REFERENCE, packages=tuple(reversed(REFERENCE.packages))))


def test_signature_alias_or_substitution_is_closed() -> None:
    changed = replace(REFERENCE.exports[0], signature_digest="0" * 64)
    with pytest.raises(ArchitectureViolation, match="CLOSED_SET"):
        verify_manifest(REFERENCE, replace(REFERENCE, exports=(changed, *REFERENCE.exports[1:])))


def test_r1_export_cannot_self_certify_a_wrong_signature() -> None:
    changed = replace(REFERENCE.exports[-1], signature_digest="0" * 64)
    self_consistent = replace(REFERENCE, exports=(*REFERENCE.exports[:-1], changed))
    with pytest.raises(ArchitectureViolation, match="R1_EXPORT_SIGNATURE"):
        verify_manifest(self_consistent, self_consistent)


def test_r1_export_set_cannot_self_certify_an_omission() -> None:
    omitted = tuple(
        export
        for export in REFERENCE.exports
        if export.reference != "chiplog.domain_primitives:PrincipalId"
    )
    self_consistent = replace(REFERENCE, exports=omitted)
    with pytest.raises(ArchitectureViolation, match="R1_EXPORT_SET"):
        verify_manifest(self_consistent, self_consistent)


def test_export_reference_and_declared_package_cannot_split() -> None:
    target = "chiplog.adapters.bridges:EffectsToPlanning"
    exports = list(REFERENCE.exports)
    position = next(index for index, export in enumerate(exports) if export.reference == target)
    exports[position] = replace(exports[position], package="chiplog.composition")
    self_consistent = replace(REFERENCE, exports=tuple(exports))
    with pytest.raises(ArchitectureViolation, match="EXPORT_PACKAGE_SPLIT"):
        verify_manifest(self_consistent, self_consistent)


def test_capability_package_cannot_self_certify_a_rival_owner() -> None:
    changed = replace(REFERENCE.packages[3], owner="effects")
    self_consistent = replace(
        REFERENCE,
        packages=(*REFERENCE.packages[:3], changed, *REFERENCE.packages[4:]),
    )
    with pytest.raises(ArchitectureViolation, match="PACKAGE_OWNER_SUBSTITUTION"):
        verify_manifest(self_consistent, self_consistent)


def test_export_cannot_self_certify_a_rival_owner() -> None:
    position = next(
        index
        for index, export in enumerate(REFERENCE.exports)
        if export.reference == "chiplog.capabilities.planning:_PlanningFactory"
    )
    changed = replace(REFERENCE.exports[position], capability="effects")
    exports = list(REFERENCE.exports)
    exports[position] = changed
    self_consistent = replace(REFERENCE, exports=tuple(exports))
    with pytest.raises(ArchitectureViolation, match="EXPORT_OWNER_SUBSTITUTION"):
        verify_manifest(self_consistent, self_consistent)


def test_evidence_classification_is_part_of_the_frozen_generation() -> None:
    with pytest.raises(ArchitectureViolation, match="CLOSED_SET"):
        verify_manifest(REFERENCE, replace(REFERENCE, evidentiary=False))
