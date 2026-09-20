"""Bounded, JSON-shaped YAML for authored transcript contracts."""

from __future__ import annotations

import math
import re
from typing import Any

import yaml


class ContractYamlError(ValueError):
    pass


class _Loader(yaml.SafeLoader):
    pass


# Dates and YAML 1.1 yes/no must not silently change authored string types.
_Loader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag not in {"tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:bool"}
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile("^(true|false)$"), list("tf"))


def _mapping(loader: _Loader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key == "<<" or key in result:
            raise ContractYamlError(f"invalid or duplicate contract key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_Loader.add_constructor("tag:yaml.org,2002:map", _mapping)


def parse_contract_yaml(source: str) -> dict[str, Any]:
    if len(source.encode()) > 1_048_576:
        raise ContractYamlError("contract block exceeds byte limit")
    try:
        depth = 0
        for event in yaml.parse(source, Loader=_Loader):
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise ContractYamlError("YAML anchors and aliases are not supported")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > 64:
                    raise ContractYamlError("contract nesting exceeds limit")
            if isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        parsed: Any = yaml.load(source, Loader=_Loader)
    except yaml.YAMLError as error:
        raise ContractYamlError(str(error)) from error
    _json_value(parsed)
    if not isinstance(parsed, dict) or len(parsed) != 1:
        raise ContractYamlError("each block must have exactly one discriminator")
    return parsed


def _json_value(value: Any) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, dict):
        for item in value.values():
            _json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item)
        return
    raise ContractYamlError("contract values must be finite JSON values")
