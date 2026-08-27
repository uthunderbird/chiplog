"""Fail-closed verifier for closed architectural manifests."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import fields

from .manifests import R1_SIGNATURES, ArchitectureManifest


class ArchitectureViolation(ValueError):
    """A stable violation code plus an actionable owner-labelled message."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"сделай: [{code}] {detail}")


def _keys[T](values: tuple[T, ...], key: str) -> tuple[str, ...]:
    return tuple(str(getattr(value, key)) for value in values)


def _require_unique_ordered[T](values: tuple[T, ...], key: str, label: str) -> None:
    keys = _keys(values, key)
    duplicates = tuple(name for name, count in Counter(keys).items() if count > 1)
    if duplicates:
        raise ArchitectureViolation("DUPLICATE", f"{label}: duplicate {duplicates}")
    if keys != tuple(sorted(keys)):
        raise ArchitectureViolation("REORDER", f"{label}: use canonical key order")


def _closed_equal(reference: ArchitectureManifest, candidate: ArchitectureManifest) -> None:
    for field in fields(reference):
        name = field.name
        if name in {"generation", "schema_version"}:
            continue
        expected = getattr(reference, name)
        actual = getattr(candidate, name)
        if actual != expected:
            raise ArchitectureViolation("CLOSED_SET", f"{name} differs from frozen generation")


def _assert_known(name: str, universe: set[str], label: str) -> None:
    if name not in universe:
        raise ArchitectureViolation("UNKNOWN_REFERENCE", f"{label} references {name!r}")


def _check_graph(manifest: ArchitectureManifest) -> None:
    graph: dict[str, set[str]] = {item.name: set() for item in manifest.capabilities}
    for route in manifest.routes:
        if not route.synchronous:
            continue
        graph.setdefault(route.consumer, set()).add(route.provider)
        graph.setdefault(route.provider, set())
    package_owners = {
        package.name: package.owner
        for package in manifest.packages
        if package.role in {"capability", "workflow"}
    }
    for edge in manifest.imports:
        consumer = package_owners.get(edge.importer)
        provider = package_owners.get(edge.imported)
        if consumer is not None and provider is not None and consumer != provider:
            graph.setdefault(consumer, set()).add(provider)
            graph.setdefault(provider, set())

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ArchitectureViolation("SYNC_SCC", f"synchronous routing cycle reaches {node}")
        if node in visited:
            return
        visiting.add(node)
        for neighbour in graph[node]:
            visit(neighbour)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)


def _validate_structure(manifest: ArchitectureManifest) -> None:
    if manifest.schema_version != 1:
        raise ArchitectureViolation("STALE_SCHEMA", "manifest schema_version must be exactly 1")
    if manifest.r1_signatures != R1_SIGNATURES:
        raise ArchitectureViolation("R1_SIGNATURE_SET", "bind the exact frozen R1 signature set")
    if manifest.r1_signatures != tuple(sorted(manifest.r1_signatures)):
        raise ArchitectureViolation("R1_SIGNATURE_ORDER", "R1 refs must use canonical order")
    if len(dict(manifest.r1_signatures)) != len(manifest.r1_signatures):
        raise ArchitectureViolation("R1_SIGNATURE_DUPLICATE", "R1 refs must be unique")

    keyed = (
        (manifest.packages, "name", "packages"),
        (manifest.exports, "reference", "exports"),
        (manifest.capabilities, "name", "capabilities"),
        (manifest.record_ownership, "record_type", "record ownership"),
        (manifest.bridges, "name", "bridges"),
        (manifest.providers, "port", "providers"),
        (manifest.surfaces, "name", "surfaces"),
        (manifest.executable_roots, "executable", "executable roots"),
        (manifest.imports, "name", "imports"),
        (manifest.routes, "name", "routes"),
    )
    for values, key, label in keyed:
        _require_unique_ordered(values, key, label)

    packages = {item.name: item for item in manifest.packages}
    capabilities = {item.name: item for item in manifest.capabilities}
    exports = {item.reference: item for item in manifest.exports}
    bridges = {item.name: item for item in manifest.bridges}
    surfaces = {item.name: item for item in manifest.surfaces}

    for capability in manifest.capabilities:
        _assert_known(capability.package, set(packages), "capability package")
        if packages[capability.package].role not in {"capability", "workflow"}:
            raise ArchitectureViolation("CAPABILITY_ROLE", capability.name)
        if packages[capability.package].owner != capability.name:
            raise ArchitectureViolation("PACKAGE_OWNER_SUBSTITUTION", capability.name)
    for export in manifest.exports:
        _assert_known(export.package, set(packages), "export package")
        if export.reference.rpartition(":")[0] != export.package:
            raise ArchitectureViolation("EXPORT_PACKAGE_SPLIT", export.reference)
        if re.fullmatch(r"[0-9a-f]{64}", export.signature_digest) is None:
            raise ArchitectureViolation("SIGNATURE_DIGEST_FORMAT", export.reference)
        if packages[export.package].role == "domain_primitives":
            if export.capability:
                raise ArchitectureViolation("SHARED_OWNER", export.reference)
            expected_digest = dict(R1_SIGNATURES).get(export.reference)
            if export.signature_digest != expected_digest:
                raise ArchitectureViolation("R1_EXPORT_SIGNATURE", export.reference)
        elif packages[export.package].role in {"capability", "workflow"}:
            _assert_known(export.capability, set(capabilities), "export capability")
            if export.capability != packages[export.package].owner:
                raise ArchitectureViolation("EXPORT_OWNER_SUBSTITUTION", export.reference)
        else:
            if export.capability:
                raise ArchitectureViolation("IMPLEMENTATION_CLAIMS_CAPABILITY", export.reference)
    for ownership in manifest.record_ownership:
        _assert_known(ownership.owner, set(capabilities), "record owner")
        _assert_known(ownership.commit_boundary, set(surfaces), "commit boundary")
        if surfaces[ownership.commit_boundary].owner != ownership.owner:
            raise ArchitectureViolation("CROSS_OWNER_COMMIT", ownership.record_type)
    for bridge in manifest.bridges:
        _assert_known(bridge.consumer, set(capabilities), "bridge consumer")
        _assert_known(bridge.provider, set(capabilities), "bridge provider")
        _assert_known(bridge.consumer_port, set(exports), "consumer port")
        _assert_known(bridge.provider_port, set(exports), "provider port")
        if exports[bridge.consumer_port].capability != bridge.consumer:
            raise ArchitectureViolation("BRIDGE_CONSUMER_SUBSTITUTION", bridge.name)
        if exports[bridge.provider_port].capability != bridge.provider:
            raise ArchitectureViolation("BRIDGE_PROVIDER_SUBSTITUTION", bridge.name)
        if (
            exports[bridge.consumer_port].visibility != "public"
            or exports[bridge.provider_port].visibility != "public"
        ):
            raise ArchitectureViolation("BRIDGE_PRIVATE_PORT", bridge.name)
        package = bridge.implementation.rpartition(":")[0]
        if (
            package not in packages
            or packages[package].role != "adapter"
            or ".bridges" not in package
        ):
            raise ArchitectureViolation("BRIDGE_LOCATION", bridge.name)
        if bridge.implementation not in exports:
            raise ArchitectureViolation("BRIDGE_IMPLEMENTATION_UNKNOWN", bridge.name)
    for provider in manifest.providers:
        _assert_known(provider.owner, set(capabilities), "provider owner")
        _assert_known(provider.port, set(exports), "provider port")
        if exports[provider.port].capability != provider.owner:
            raise ArchitectureViolation("PROVIDER_OWNER_SUBSTITUTION", provider.port)
        if exports[provider.port].visibility != "public":
            raise ArchitectureViolation("PROVIDER_PRIVATE_PORT", provider.port)
        implementation_package = provider.implementation.rpartition(":")[0]
        if (
            provider.implementation not in exports
            or implementation_package not in packages
            or packages[implementation_package].role != "adapter"
        ):
            raise ArchitectureViolation("PROVIDER_IMPLEMENTATION_UNKNOWN", provider.port)
        if provider.fallback is not None:
            raise ArchitectureViolation("FALLBACK_ROUTING", provider.port)
    for surface in manifest.surfaces:
        _assert_known(surface.owner, set(capabilities), "surface owner")
        if surface.generic_authoritative_write:
            raise ArchitectureViolation("GENERIC_APPEND", surface.name)

    executable_surfaces = {item.name for item in manifest.surfaces if item.executable}
    root_surfaces = {item.executable for item in manifest.executable_roots}
    if executable_surfaces != root_surfaces:
        raise ArchitectureViolation("ROOT_COVERAGE", "every executable needs exactly one root")
    for root in manifest.executable_roots:
        if not root.assembly or not root.bootstrap:
            raise ArchitectureViolation("BOOTSTRAP_MISSING", root.executable)
        if root.admission_bypass or not root.readiness_after_start:
            raise ArchitectureViolation("BOOTSTRAP_BYPASS", root.executable)
        for reference in (root.assembly, root.bootstrap):
            package = reference.rpartition(":")[0]
            if (
                reference not in exports
                or package not in packages
                or packages[package].role != "composition"
            ):
                raise ArchitectureViolation("ROOT_OWNERSHIP", root.executable)
        if root.teardown_order != tuple(reversed(root.resource_order)):
            raise ArchitectureViolation("LIFECYCLE_ORDER", root.executable)

    for edge in manifest.imports:
        _assert_known(edge.importer, set(packages), "importer")
        _assert_known(edge.imported, set(packages), "imported package")
        if edge.mode != "static":
            raise ArchitectureViolation("DYNAMIC_RESOLUTION", edge.importer)
        imported_export = exports.get(edge.symbol)
        if imported_export is None:
            raise ArchitectureViolation("UNKNOWN_EXPORT", edge.symbol)
        if imported_export.visibility != "public" and edge.importer != edge.imported:
            raise ArchitectureViolation("PRIVATE_IMPORT", edge.symbol)
        importer_role = packages[edge.importer].role
        imported_role = packages[edge.imported].role
        allowed_roles = {
            "domain_primitives": set(),
            "capability": {"capability", "domain_primitives"},
            "workflow": {"workflow", "domain_primitives"},
            "adapter": {"capability", "workflow", "platform", "domain_primitives"},
            "platform": {"platform"},
            "composition": {
                "capability",
                "workflow",
                "adapter",
                "platform",
                "domain_primitives",
                "composition",
            },
        }
        if imported_role not in allowed_roles[importer_role]:
            raise ArchitectureViolation("FORBIDDEN_ROLE_IMPORT", edge.name)
        if (
            importer_role in {"capability", "workflow"}
            and imported_role in {"capability", "workflow"}
            and edge.importer != edge.imported
        ):
            raise ArchitectureViolation("DIRECT_BOUNDARY_IMPORT", edge.importer)
        if imported_export.package != edge.imported:
            raise ArchitectureViolation("IMPORT_PACKAGE_SUBSTITUTION", edge.name)

    for route in manifest.routes:
        _assert_known(route.consumer, set(capabilities), "route consumer")
        _assert_known(route.provider, set(capabilities), "route provider")
        _assert_known(route.bridge, set(bridges), "route bridge")
        bridge = bridges[route.bridge]
        if (bridge.consumer, bridge.provider) != (route.consumer, route.provider):
            raise ArchitectureViolation("ROUTE_SUBSTITUTION", route.name)
        if route.kind == "callback":
            raise ArchitectureViolation("CALLBACK_ROUTE", route.name)
    if {route.bridge for route in manifest.routes} != set(bridges):
        raise ArchitectureViolation("BRIDGE_ROUTE_COVERAGE", "bridge/routes differ")
    _check_graph(manifest)
    if manifest.evidentiary:
        owners = {item.owner for item in manifest.record_ownership}
        visibilities = {item.visibility for item in manifest.exports}
        has_reverse = any(item.kind == "reverse" for item in manifest.routes)
        if (
            len(owners) < 2
            or visibilities != {"private", "public"}
            or not manifest.record_ownership
            or not manifest.executable_roots
            or not manifest.bridges
            or not has_reverse
        ):
            raise ArchitectureViolation(
                "VACUOUS_EVIDENCE",
                "evidence needs two owners, public/private exports, ownership, "
                "root, bridge, and reverse route",
            )
        r1_export_references = {
            export.reference
            for export in manifest.exports
            if export.package == "chiplog.domain_primitives"
        }
        if r1_export_references != set(dict(R1_SIGNATURES)):
            raise ArchitectureViolation(
                "R1_EXPORT_SET", "export every frozen R1 symbol exactly once"
            )


def verify_manifest(reference: ArchitectureManifest, candidate: ArchitectureManifest) -> str:
    """Validate structure and exact equality; return the bound fingerprint."""

    if candidate.generation != reference.generation:
        raise ArchitectureViolation("STALE_GENERATION", "candidate generation differs")
    _validate_structure(candidate)
    _closed_equal(reference, candidate)
    return candidate.fingerprint()
