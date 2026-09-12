from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.architecture import (
    ArchitectureManifest,
    ArchitectureViolation,
    CapabilityDecl,
    ExportDecl,
    ImportEdge,
    PackageDecl,
    RouteEdge,
    verify_manifest,
)
from tests.support.manifest import REFERENCE


def assert_rejected(code: str, candidate: ArchitectureManifest) -> None:
    with pytest.raises(ArchitectureViolation, match=code):
        verify_manifest(REFERENCE, candidate)


def ordered[T](values: tuple[T, ...], key: str) -> tuple[T, ...]:
    return tuple(sorted(values, key=lambda value: str(getattr(value, key))))


def test_cross_owner_record_conflict() -> None:
    ownership = replace(REFERENCE.record_ownership[0], owner="planning")
    assert_rejected(
        "CROSS_OWNER_COMMIT",
        replace(REFERENCE, record_ownership=(ownership, *REFERENCE.record_ownership[1:])),
    )


def test_private_import_and_dynamic_resolution_escapes() -> None:
    private = ImportEdge(
        "planning_private",
        "chiplog.capabilities.effects",
        "chiplog.capabilities.planning",
        "chiplog.capabilities.planning:_PlanningFactory",
    )
    assert_rejected(
        "PRIVATE_IMPORT",
        replace(REFERENCE, imports=ordered((*REFERENCE.imports, private), "name")),
    )
    dynamic = replace(REFERENCE.imports[0], mode="service_locator")
    assert_rejected(
        "DYNAMIC_RESOLUTION",
        replace(REFERENCE, imports=(dynamic, *REFERENCE.imports[1:])),
    )


def test_adapter_to_adapter_and_capability_platform_edges() -> None:
    adapter = ImportEdge(
        "adapter_peer",
        "chiplog.adapters.bridges",
        "chiplog.adapters.bridges",
        "chiplog.capabilities.effects:EvidenceQuery",
    )
    assert_rejected(
        "FORBIDDEN_ROLE_IMPORT",
        replace(REFERENCE, imports=ordered((*REFERENCE.imports, adapter), "name")),
    )
    forbidden = replace(
        REFERENCE.imports[-1],
        imported="chiplog.composition",
        symbol="chiplog.capabilities.planning:PlanningCommands",
    )
    assert_rejected(
        "FORBIDDEN_ROLE_IMPORT",
        replace(REFERENCE, imports=(*REFERENCE.imports[:-1], forbidden)),
    )
    direct = replace(
        REFERENCE.imports[-1],
        imported="chiplog.capabilities.effects",
        symbol="chiplog.capabilities.effects:EvidenceQuery",
    )
    assert_rejected(
        "DIRECT_BOUNDARY_IMPORT",
        replace(REFERENCE, imports=(*REFERENCE.imports[:-1], direct)),
    )


def test_route_substitution_callback_and_scc() -> None:
    substituted = replace(REFERENCE.routes[0], provider="journal")
    assert_rejected(
        "ROUTE_SUBSTITUTION",
        replace(REFERENCE, routes=(substituted, *REFERENCE.routes[1:])),
    )
    callback = replace(REFERENCE.routes[0], kind="callback")
    assert_rejected(
        "CALLBACK_ROUTE",
        replace(REFERENCE, routes=(callback, *REFERENCE.routes[1:])),
    )
    cycle_bridge = replace(
        REFERENCE.bridges[0],
        name="journal_to_effects",
        consumer="journal",
        provider="effects",
        consumer_port="chiplog.capabilities.journal:CandidateCommands",
        provider_port="chiplog.capabilities.effects:EvidenceQuery",
    )
    cycle_route = RouteEdge(
        "journal_to_effects", "journal", "effects", "journal_to_effects", "reverse"
    )
    assert_rejected(
        "SYNC_SCC",
        replace(
            REFERENCE,
            bridges=ordered((*REFERENCE.bridges, cycle_bridge), "name"),
            routes=ordered((*REFERENCE.routes, cycle_route), "name"),
        ),
    )


def test_generic_append_fallback_and_bootstrap_bypass() -> None:
    surface = replace(REFERENCE.surfaces[1], generic_authoritative_write=True)
    assert_rejected(
        "GENERIC_APPEND",
        replace(
            REFERENCE,
            surfaces=(REFERENCE.surfaces[0], surface, *REFERENCE.surfaces[2:]),
        ),
    )
    provider = replace(REFERENCE.providers[0], fallback="ambient-service-locator")
    assert_rejected(
        "FALLBACK_ROUTING",
        replace(REFERENCE, providers=(provider, *REFERENCE.providers[1:])),
    )
    root = replace(REFERENCE.executable_roots[0], admission_bypass=True)
    assert_rejected("BOOTSTRAP_BYPASS", replace(REFERENCE, executable_roots=(root,)))
    foreign_root = replace(root, admission_bypass=False, assembly="chiplog.adapters.bridges:build")
    assert_rejected("ROOT_OWNERSHIP", replace(REFERENCE, executable_roots=(foreign_root,)))


def test_provider_and_bridge_owner_substitution() -> None:
    provider = replace(REFERENCE.providers[0], owner="journal")
    assert_rejected(
        "PROVIDER_OWNER_SUBSTITUTION",
        replace(REFERENCE, providers=(provider, *REFERENCE.providers[1:])),
    )
    bridge = replace(
        REFERENCE.bridges[0],
        consumer_port="chiplog.capabilities.journal:CandidateCommands",
    )
    assert_rejected(
        "BRIDGE_CONSUMER_SUBSTITUTION",
        replace(REFERENCE, bridges=(bridge, *REFERENCE.bridges[1:])),
    )


def test_bridge_and_provider_reject_private_ports() -> None:
    target = "chiplog.capabilities.journal:CandidateCommands"
    exports = list(REFERENCE.exports)
    position = next(index for index, export in enumerate(exports) if export.reference == target)
    exports[position] = replace(exports[position], visibility="private")
    private_manifest = replace(REFERENCE, exports=tuple(exports))
    assert_rejected("BRIDGE_PRIVATE_PORT", private_manifest)

    private_port = ExportDecl(
        "chiplog.capabilities.effects:PrivateProviderPort",
        "chiplog.capabilities.effects",
        "private",
        "0" * 64,
        "effects",
    )
    provider_exports = ordered((*REFERENCE.exports, private_port), "reference")
    provider = replace(REFERENCE.providers[0], port=private_port.reference)
    private_provider_manifest = replace(
        REFERENCE,
        exports=provider_exports,
        providers=(provider, *REFERENCE.providers[1:]),
    )
    assert_rejected("PROVIDER_PRIVATE_PORT", private_provider_manifest)


def test_role_allow_matrix_rejects_domain_and_workflow_escapes() -> None:
    adapter_export = replace(REFERENCE.exports[0], visibility="public")
    exports = (adapter_export, *REFERENCE.exports[1:])
    domain_escape = ImportEdge(
        "domain_to_adapter",
        "chiplog.domain_primitives",
        "chiplog.adapters.bridges",
        adapter_export.reference,
    )
    assert_rejected(
        "FORBIDDEN_ROLE_IMPORT",
        replace(
            REFERENCE,
            exports=exports,
            imports=ordered((*REFERENCE.imports, domain_escape), "name"),
        ),
    )

    workflow_package = PackageDecl("chiplog.workflows.audit", "workflow", "audit")
    workflow_capability = CapabilityDecl("audit", workflow_package.name)
    composition_export = next(
        export
        for export in REFERENCE.exports
        if export.reference == "chiplog.composition:build_graph"
    )
    public_composition = replace(composition_export, visibility="public")
    exports = tuple(
        public_composition if export.reference == public_composition.reference else export
        for export in REFERENCE.exports
    )
    workflow_escape = ImportEdge(
        "workflow_to_composition",
        workflow_package.name,
        "chiplog.composition",
        public_composition.reference,
    )
    assert_rejected(
        "FORBIDDEN_ROLE_IMPORT",
        replace(
            REFERENCE,
            packages=ordered((*REFERENCE.packages, workflow_package), "name"),
            capabilities=ordered((*REFERENCE.capabilities, workflow_capability), "name"),
            exports=exports,
            imports=ordered((*REFERENCE.imports, workflow_escape), "name"),
        ),
    )


def test_unregistered_implementation_and_bootstrap_symbols() -> None:
    bridge = replace(REFERENCE.bridges[0], implementation="chiplog.adapters.bridges:Missing")
    assert_rejected(
        "BRIDGE_IMPLEMENTATION_UNKNOWN",
        replace(REFERENCE, bridges=(bridge, *REFERENCE.bridges[1:])),
    )
    provider = replace(REFERENCE.providers[0], implementation="chiplog.adapters.bridges:Missing")
    assert_rejected(
        "PROVIDER_IMPLEMENTATION_UNKNOWN",
        replace(REFERENCE, providers=(provider, *REFERENCE.providers[1:])),
    )
    root = replace(REFERENCE.executable_roots[0], bootstrap="chiplog.composition:missing")
    assert_rejected("ROOT_OWNERSHIP", replace(REFERENCE, executable_roots=(root,)))


def test_stale_generation_schema_and_lifecycle_order() -> None:
    assert_rejected("STALE_GENERATION", replace(REFERENCE, generation="r2-synthetic-v0"))
    assert_rejected("STALE_SCHEMA", replace(REFERENCE, schema_version=0))
    root = replace(REFERENCE.executable_roots[0], teardown_order=("database", "graph", "workers"))
    assert_rejected("LIFECYCLE_ORDER", replace(REFERENCE, executable_roots=(root,)))
