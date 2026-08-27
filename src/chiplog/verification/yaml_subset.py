from __future__ import annotations

import re
from dataclasses import dataclass

KEY = re.compile(r"^[a-z_][a-z0-9_]*$")
INTEGER = re.compile(r"^-?[0-9]+$")


class SubsetYamlError(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    indent: int
    content: str


def parse_yaml_subset(source: str) -> object:
    lines: list[Line] = []
    for raw in source.splitlines():
        if not raw.strip():
            continue
        if "\t" in raw:
            raise SubsetYamlError("tabs are not allowed")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise SubsetYamlError("indentation must use multiples of two spaces")
        lines.append(Line(indent, raw.strip()))
    if not lines:
        raise SubsetYamlError("empty block")
    value, end = _parse_block(lines, 0, lines[0].indent)
    if end != len(lines):
        raise SubsetYamlError("unconsumed input")
    return value


def _parse_block(lines: list[Line], index: int, indent: int) -> tuple[object, int]:
    if lines[index].indent != indent:
        raise SubsetYamlError("unexpected indentation")
    if lines[index].content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(lines: list[Line], index: int, indent: int) -> tuple[dict[str, object], int]:
    result: dict[str, object] = {}
    while index < len(lines) and lines[index].indent == indent:
        content = lines[index].content
        if content.startswith("- ") or ":" not in content:
            raise SubsetYamlError("expected key: value")
        key, raw_value = content.split(":", 1)
        if not KEY.fullmatch(key) or key in result:
            raise SubsetYamlError(f"invalid or duplicate key: {key}")
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            result[key] = _scalar(raw_value)
        else:
            if index >= len(lines) or lines[index].indent <= indent:
                result[key] = {}
            else:
                if lines[index].indent != indent + 2:
                    raise SubsetYamlError("nested indentation must advance by two")
                result[key], index = _parse_block(lines, index, indent + 2)
    return result, index


def _parse_list(lines: list[Line], index: int, indent: int) -> tuple[list[object], int]:
    result: list[object] = []
    while index < len(lines) and lines[index].indent == indent:
        content = lines[index].content
        if not content.startswith("- "):
            break
        item = content[2:].strip()
        index += 1
        if not item:
            if index >= len(lines) or lines[index].indent != indent + 2:
                raise SubsetYamlError("empty list item must contain a nested block")
            parsed, index = _parse_block(lines, index, indent + 2)
            result.append(parsed)
        elif ":" in item and KEY.fullmatch(item.split(":", 1)[0]):
            key, raw_value = item.split(":", 1)
            parsed_value = _scalar(raw_value.strip()) if raw_value.strip() else {}
            mapping: dict[str, object] = {key: parsed_value}
            if index < len(lines) and lines[index].indent == indent + 2:
                tail, index = _parse_map(lines, index, indent + 2)
                if set(mapping) & set(tail):
                    raise SubsetYamlError("duplicate list-item key")
                mapping.update(tail)
            result.append(mapping)
        else:
            result.append(_scalar(item))
    return result, index


def _scalar(value: str) -> object:
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value in {"true", "false"}:
        return value == "true"
    if INTEGER.fullmatch(value):
        return int(value)
    if value.startswith('"') or value.endswith('"'):
        if not (len(value) >= 2 and value.startswith('"') and value.endswith('"')):
            raise SubsetYamlError("unbalanced quoted scalar")
        return value[1:-1]
    return value
