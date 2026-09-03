from __future__ import annotations

import ast
import inspect
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints

import chiplog.capabilities.deployment_trust as deployment_trust
import chiplog.capabilities.planning as planning
import chiplog.capabilities.projections as projections
from chiplog.adapters.driven import deployment_trust as trust_adapters
from chiplog.architecture import verify_manifest
from chiplog.architecture.manifests import signature_digest
from chiplog.architecture.r4_r5_freeze import (
    R4_R5_ARCHITECTURE,
    R4_R5_DERIVATIVE_SINKS,
    R4_R5_EXPORTS,
    R4_R5_PACKAGES,
    R4_R5_PROVIDERS,
    R4_R5_RECORDS,
    R4_R5_SURFACES,
    verify_r4_r5_freeze,
)
from chiplog.capabilities.deployment_trust._records import (
    RECORD_COMMIT_BOUNDARY,
    RECORD_OWNER,
    RECORD_TYPE_IDS,
    SCHEMA_ID,
)
from chiplog.capabilities.planning._planning import (
    PLANNING_COMMIT_BOUNDARY,
    PLANNING_RECORD_OWNER,
    PLANNING_RECORD_TYPES,
    PLANNING_SCHEMA_ID,
)
from chiplog.capabilities.projections._planning import (
    PROJECTION_REBUILD_SURFACE,
    PROJECTION_SINK,
)

ROOT = Path(__file__).parents[2]


def _public_runtime_exports() -> dict[str, object]:
    modules = (deployment_trust, planning, projections, trust_adapters)
    return {
        f"{module.__name__}:{name}": getattr(module, name)
        for module in modules
        for name in module.__all__
    }


def _type_name(annotation: object) -> str:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return " | ".join(str(item) for item in args)
    if origin is types.UnionType:
        return " | ".join(_type_name(item) for item in args)
    if origin is tuple:
        return (
            f"tuple[{', '.join('...' if item is Ellipsis else _type_name(item) for item in args)}]"
        )
    if isinstance(annotation, type):
        if annotation is type(None):
            return "None"
        return annotation.__name__
    return str(annotation)


def _method_shape(value: object, method_name: str) -> tuple[tuple[str, str], ...]:
    method = getattr(value, method_name)
    hints = get_type_hints(method)
    names = tuple(name for name in inspect.signature(method).parameters if name != "self")
    return tuple((name, _type_name(hints[name])) for name in names)


def _observed_export_shape(reference: str, value: object) -> tuple[tuple[str, str], ...]:
    method_by_reference = {
        "chiplog.capabilities.deployment_trust:TenantDecisionJournalPort": "append",
        "chiplog.capabilities.deployment_trust:TrustMaterializationPort": "materialize",
        "chiplog.capabilities.deployment_trust:TrustRevalidator": "revalidate",
        "chiplog.capabilities.deployment_trust:TrustAuthenticator": "authenticate",
        "chiplog.capabilities.planning:PlanningCommands": "execute",
        "chiplog.capabilities.planning:TrustRevalidationPort": "revalidate",
        "chiplog.capabilities.projections:PlanningProjectionQueries": "get",
    }
    if reference in method_by_reference:
        shape = _method_shape(value, method_by_reference[reference])
        frozen_names = tuple(
            name
            for name, _ in next(
                item.fields for item in R4_R5_EXPORTS if item.reference == reference
            )
        )
        if "result" in frozen_names:
            shape = (
                *shape,
                (
                    "result",
                    _type_name(
                        get_type_hints(getattr(value, method_by_reference[reference]))["return"]
                    ),
                ),
            )
        return tuple(item for item in shape if item[0] in frozen_names)
    if reference.startswith("chiplog.adapters."):
        return _method_shape(value, "__init__")
    hints = get_type_hints(value)
    return tuple((field.name, _type_name(hints[field.name])) for field in fields(cast(Any, value)))


def test_runtime_exports_equal_the_frozen_r4_r5_set() -> None:
    verify_r4_r5_freeze()
    assert verify_manifest(R4_R5_ARCHITECTURE, R4_R5_ARCHITECTURE)
    assert set(_public_runtime_exports()) == {item.reference for item in R4_R5_EXPORTS}
    assert (
        tuple(
            (module, owner)
            for module, owner in R4_R5_PACKAGES
            if (ROOT / "src" / Path(*module.split(".")) / "__init__.py").is_file()
        )
        == R4_R5_PACKAGES
    )
    assert {
        (owner, module)
        for module, owner in R4_R5_PACKAGES
        if module.startswith("chiplog.capabilities.")
    } == {(item.name, item.package) for item in R4_R5_ARCHITECTURE.capabilities}


def test_runtime_export_shapes_equal_frozen_signature_digests() -> None:
    runtime = _public_runtime_exports()
    for frozen in R4_R5_EXPORTS:
        observed = _observed_export_shape(frozen.reference, runtime[frozen.reference])
        assert observed == frozen.fields
        assert (
            signature_digest(frozen.reference.rpartition(":")[2], observed)
            == frozen.signature_digest
        )


def test_all_frozen_interfaces_are_typing_protocols() -> None:
    interfaces = (
        deployment_trust.TenantDecisionJournalPort,
        deployment_trust.TrustMaterializationPort,
        deployment_trust.TrustRevalidator,
        deployment_trust.TrustAuthenticator,
        planning.PlanningCommands,
        planning.TrustRevalidationPort,
        projections.PlanningProjectionQueries,
    )
    assert all(inspect.isclass(item) and item.__dict__.get("_is_protocol") for item in interfaces)


def test_frozen_value_exports_are_immutable_dataclasses_with_exact_fields() -> None:
    runtime = _public_runtime_exports()
    protocol_refs = {
        reference
        for reference, value in runtime.items()
        if inspect.isclass(value) and value.__dict__.get("_is_protocol")
    }
    adapter_refs = {reference for reference in runtime if reference.startswith("chiplog.adapters.")}
    for frozen in R4_R5_EXPORTS:
        if frozen.reference in protocol_refs | adapter_refs:
            continue
        value = runtime[frozen.reference]
        assert is_dataclass(value)
        assert cast(Any, value).__dataclass_params__.frozen
        assert tuple(field.name for field in fields(value)) == tuple(
            name for name, _ in frozen.fields
        )


def test_runtime_record_schema_and_projection_sets_equal_freeze() -> None:
    frozen_by_owner = {
        owner: tuple(item.record_type_id for item in R4_R5_RECORDS if item.owner == owner)
        for owner in ("deployment_trust", "planning")
    }
    assert tuple(RECORD_TYPE_IDS) == frozen_by_owner["deployment_trust"]
    assert tuple(PLANNING_RECORD_TYPES) == frozen_by_owner["planning"]
    assert {item.schema_id for item in R4_R5_RECORDS if item.owner == "deployment_trust"} == {
        SCHEMA_ID
    }
    assert {item.schema_id for item in R4_R5_RECORDS if item.owner == "planning"} == {
        PLANNING_SCHEMA_ID
    }
    assert (PROJECTION_SINK,) == R4_R5_DERIVATIVE_SINKS
    runtime_ownership = {
        (record_type, RECORD_OWNER, RECORD_COMMIT_BOUNDARY) for record_type in RECORD_TYPE_IDS
    } | {
        (record_type, PLANNING_RECORD_OWNER, PLANNING_COMMIT_BOUNDARY)
        for record_type in PLANNING_RECORD_TYPES
    }
    assert {
        (item.record_type_id, item.owner, item.commit_boundary) for item in R4_R5_RECORDS
    } == runtime_ownership
    assert (
        RECORD_COMMIT_BOUNDARY,
        PLANNING_COMMIT_BOUNDARY,
        PROJECTION_REBUILD_SURFACE,
    ) == R4_R5_SURFACES


def test_private_adapters_structurally_implement_frozen_ports() -> None:
    assert {"append", "entries", "observation"} <= set(
        dir(trust_adapters.IndependentTenantDecisionJournal)
    )
    assert {"materialize", "materialized", "records"} <= set(
        dir(trust_adapters.SQLiteTrustMaterializer)
    )
    assert tuple((item.port, item.owner, item.implementation) for item in R4_R5_PROVIDERS) == (
        (
            "chiplog.capabilities.deployment_trust:TenantDecisionJournalPort",
            "deployment_trust",
            "chiplog.adapters.driven.deployment_trust:IndependentTenantDecisionJournal",
        ),
        (
            "chiplog.capabilities.deployment_trust:TrustMaterializationPort",
            "deployment_trust",
            "chiplog.adapters.driven.deployment_trust:SQLiteTrustMaterializer",
        ),
    )


def test_parallel_capabilities_have_no_direct_cross_imports() -> None:
    packages = (
        "chiplog.capabilities.deployment_trust",
        "chiplog.capabilities.planning",
        "chiplog.capabilities.projections",
    )
    for package in packages:
        package_path = ROOT / "src" / Path(*package.split("."))
        for source in package_path.rglob("*.py"):
            tree = ast.parse(source.read_text())
            imported = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module is not None
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            denied = set(packages) - {package}
            assert not any(
                name == target or name.startswith(f"{target}.")
                for name in imported
                for target in denied
            )
