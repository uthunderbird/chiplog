from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, fields
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, get_type_hints

import pytest

import chiplog.domain_primitives as primitives
from chiplog.architecture.manifests import R1_SIGNATURES


def _annotation_name(annotation: Any) -> str:
    name = getattr(annotation, "__name__", None)
    if not isinstance(name, str):
        raise TypeError(f"non-canonical annotation: {annotation!r}")
    return name


def _derive_signatures(module: Any) -> tuple[tuple[str, str], ...]:
    exports = tuple(module.__all__)
    if len(exports) != len(set(exports)):
        raise ValueError("duplicate R1 export")
    derived: list[tuple[str, str]] = []
    for export in exports:
        value_type = getattr(module, export)
        hints = get_type_hints(value_type)
        declared_fields = fields(value_type)
        parameters = tuple(inspect.signature(value_type).parameters.values())
        if tuple(parameter.name for parameter in parameters) != tuple(
            field.name for field in declared_fields
        ):
            raise ValueError(f"constructor/field mismatch: {export}")
        if any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        ):
            raise ValueError(f"non-frozen constructor shape: {export}")
        shape = tuple(
            (field.name, _annotation_name(hints[field.name])) for field in declared_fields
        )
        payload = json.dumps(
            {"export": export, "fields": shape},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        derived.append((f"chiplog.domain_primitives:{export}", sha256(payload).hexdigest()))
    return tuple(sorted(derived))


def _assert_contract(
    module: Any,
    expected: tuple[tuple[str, str], ...] = R1_SIGNATURES,
) -> None:
    observed = _derive_signatures(module)
    if observed != expected:
        raise ValueError("runtime R1 exports differ from the independently frozen R2 contract")


def _module_with(**changes: Any) -> SimpleNamespace:
    values = {name: getattr(primitives, name) for name in primitives.__all__}
    values.update(changes)
    return SimpleNamespace(__all__=tuple(values), **values)


def test_runtime_r1_exports_equal_independently_frozen_r2_contract() -> None:
    _assert_contract(primitives)


def test_r1_field_shape_mutation_rejects() -> None:
    @dataclass(frozen=True)
    class TenantId:
        value: str
        alias: str

    with pytest.raises(ValueError, match="runtime R1 exports differ"):
        _assert_contract(_module_with(TenantId=TenantId))


@pytest.mark.parametrize("mutation", ["omit", "add"])
def test_r1_export_set_mutation_rejects(mutation: str) -> None:
    module = _module_with()
    if mutation == "omit":
        module.__all__ = module.__all__[:-1]
    else:
        module.Extra = primitives.TenantId
        module.__all__ = (*module.__all__, "Extra")
    with pytest.raises(ValueError, match="runtime R1 exports differ"):
        _assert_contract(module)


def test_r2_signature_substitution_rejects() -> None:
    changed = ((R1_SIGNATURES[0][0], "0" * 64), *R1_SIGNATURES[1:])
    with pytest.raises(ValueError, match="runtime R1 exports differ"):
        _assert_contract(primitives, changed)
