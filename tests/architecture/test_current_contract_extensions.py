"""Current additions are exact and cannot redefine the historical R4/R5 lane."""

import ast
import inspect
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import get_args, get_type_hints

import pytest
from pydantic import TypeAdapter

from chiplog.architecture.current_contract_extensions import (
    CURRENT_EXPORT_EXTENSIONS,
    CURRENT_RECORD_EXTENSION_EVIDENCE,
    CURRENT_RECORD_EXTENSIONS,
    current_export_references,
    current_record_ids,
    verify_current_contract_extensions,
)
from chiplog.architecture.r4_r5_freeze import R4_R5_EXPORTS, R4_R5_RECORDS
from chiplog.capabilities import deployment_trust


def _reference(annotation):
    for declaration in CURRENT_EXPORT_EXTENSIONS:
        value = getattr(deployment_trust, declaration.reference.split(":")[1])
        if value is annotation:
            return declaration.reference
    raise AssertionError(f"unregistered protocol annotation: {annotation}")


def _shape(declaration):
    value = getattr(deployment_trust, declaration.reference.split(":")[1])
    if declaration.kind in {"model", "union"}:
        if declaration.kind == "model":
            assert tuple(value.model_fields) == declaration.fields
            expected = {"strict": True, "frozen": True, "extra": "forbid"}
            if declaration.reference.split(":")[1] not in {
                "DatabaseGenesisV2", "DatabaseVersionTuple",
            }:
                expected |= {"ser_json_bytes": "base64", "val_json_bytes": "base64"}
            assert value.model_config == expected
        return TypeAdapter(value).json_schema()
    if declaration.kind == "protocol":
        assert value.__dict__["_is_protocol"]
        methods = []
        for name, method in sorted(vars(value).items()):
            if name.startswith("_") or not inspect.isfunction(method):
                continue
            hints = get_type_hints(method, include_extras=True)
            assert "self" not in hints
            methods.append({
                "name": name,
                "parameters": [{
                    "name": parameter.name,
                    "kind": parameter.kind.name,
                    "required": parameter.default is inspect.Parameter.empty,
                    "annotation": None if parameter.name == "self" else _reference(
                        hints[parameter.name]
                    ),
                } for parameter in inspect.signature(method).parameters.values()],
                "returns": _reference(hints["return"]),
            })
        return {"kind": "protocol", "methods": methods}
    assert declaration.kind == "exception"
    assert value.__bases__ == (RuntimeError,)
    properties = {}
    for name in ("code", "adoption"):
        member = vars(value)[name]
        assert isinstance(member, property) and member.fset is None
        alternatives = get_args(get_type_hints(member.fget)["return"])
        assert getattr(value("diagnostic"), name) in alternatives
        properties[name] = {"literal": list(alternatives)}
    return {"base": "builtins:RuntimeError", "properties": properties}


def test_current_extensions_pin_full_export_shapes_and_historical_union():
    golden = json.loads((Path(__file__).parent / "fixtures" /
                        "current_contract_extension_shapes_v1.json").read_bytes())
    assert set(golden) == {item.reference.split(":")[1] for item in CURRENT_EXPORT_EXTENSIONS}
    for declaration in CURRENT_EXPORT_EXTENSIONS:
        observed = _shape(declaration)
        assert observed == golden[declaration.reference.split(":")[1]]
        body = json.dumps(observed, sort_keys=True, separators=(",", ":")).encode()
        assert sha256(body).hexdigest() == declaration.shape_sha256
    assert current_export_references() == frozenset(
        [item.reference for item in R4_R5_EXPORTS] +
        [item.reference for item in CURRENT_EXPORT_EXTENSIONS]
    )
    assert current_record_ids("deployment_trust") == tuple(sorted(
        item.record_type_id for item in (*R4_R5_RECORDS, *CURRENT_RECORD_EXTENSIONS)
        if item.owner == "deployment_trust"
    ))


@pytest.mark.parametrize("mutation", (
    "missing-export", "extra-export", "duplicate-export", "historical-export",
    "wrong-kind", "wrong-shape", "wrong-fields", "missing-evidence",
    "missing-record", "extra-record", "duplicate-record", "historical-record",
    "wrong-schema", "wrong-owner", "wrong-boundary",
))
def test_current_extension_registry_rejects_unregistered_declarations(mutation):
    exports, records = CURRENT_EXPORT_EXTENSIONS, CURRENT_RECORD_EXTENSIONS
    first = exports[0]
    record = records[0]
    if mutation == "missing-export":
        exports = exports[1:]
    elif mutation == "extra-export":
        exports = (*exports, replace(first, reference=first.reference + "Unknown"))
    elif mutation == "duplicate-export":
        exports = (*exports, first)
    elif mutation == "historical-export":
        exports = (replace(first, reference=R4_R5_EXPORTS[0].reference), *exports[1:])
    elif mutation == "wrong-kind":
        exports = (replace(first, kind="model"), *exports[1:])
    elif mutation == "wrong-shape":
        exports = (replace(first, shape_sha256="0" * 64), *exports[1:])
    elif mutation == "wrong-fields":
        exports = (replace(first, fields=("invented",)), *exports[1:])
    elif mutation == "missing-evidence":
        exports = (replace(first, evidence=()), *exports[1:])
    elif mutation == "missing-record":
        records = records[1:]
    elif mutation == "extra-record":
        records = (*records, replace(record, record_type_id=record.record_type_id + "Unknown"))
    elif mutation == "duplicate-record":
        records = (*records, record)
    elif mutation == "historical-record":
        records = (R4_R5_RECORDS[0], *records[1:])
    else:
        field = {"wrong-schema": "schema_id", "wrong-owner": "owner",
                 "wrong-boundary": "commit_boundary"}[mutation]
        records = (replace(record, **{field: "unregistered"}), *records[1:])
    with pytest.raises(ValueError, match="current contract extensions"):
        verify_current_contract_extensions(exports, records)


def test_equal_fresh_declarations_are_accepted_without_object_identity_permission():
    assert verify_current_contract_extensions(
        tuple(replace(item) for item in CURRENT_EXPORT_EXTENSIONS),
        tuple(replace(item) for item in CURRENT_RECORD_EXTENSIONS),
    ) == verify_current_contract_extensions()


def test_each_current_extension_has_existing_owned_evidence_selector():
    root = Path(__file__).resolve().parents[2]
    assert tuple(reference for reference, _ in CURRENT_RECORD_EXTENSION_EVIDENCE) == tuple(
        item.record_type_id for item in CURRENT_RECORD_EXTENSIONS
    )
    selectors = [selector for item in CURRENT_EXPORT_EXTENSIONS for selector in item.evidence]
    selectors.extend(selector for _, selector in CURRENT_RECORD_EXTENSION_EVIDENCE)
    for selector in selectors:
        path, function = selector.split("::")
        tree = ast.parse((root / path).read_text())
        assert function in {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }, selector
