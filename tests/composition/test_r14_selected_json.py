"""Selected JSON keeps historical byte/error semantics on its native fast path."""

import json
import math
from collections.abc import Callable

import pytest

from chiplog.composition.r14_loop_history import _strict_json


@pytest.mark.parametrize("canonical", (True, False))
def test_ordinary_selected_json_avoids_duplicate_stdlib_decode_and_encode(
    monkeypatch: pytest.MonkeyPatch, canonical: bool
) -> None:
    loads, dumps = json.loads, json.dumps
    calls = {"loads": 0, "dumps": 0}

    def counted_loads(
        raw: str | bytes | bytearray,
        *,
        object_pairs_hook: Callable[[list[tuple[str, object]]], dict[str, object]],
    ) -> object:
        calls["loads"] += 1
        return loads(raw, object_pairs_hook=object_pairs_hook)

    def counted_dumps(value: object, *, sort_keys: bool, separators: tuple[str, str]) -> str:
        calls["dumps"] += 1
        return dumps(value, sort_keys=sort_keys, separators=separators)

    with monkeypatch.context() as patch:
        patch.setattr(json, "loads", counted_loads)
        patch.setattr(json, "dumps", counted_dumps)
        assert _strict_json(b'{"a":1,"b":[true,null,"ascii"]}', canonical=canonical) == {
            "a": 1,
            "b": [True, None, "ascii"],
        }
    expected = 0 if canonical else 1
    assert calls == {"loads": expected, "dumps": expected}


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        (b'{"v":"\\b\\f\\n\\r\\t\\u0000"}', {"v": "\b\f\n\r\t\x00"}),
        (b'{"v":-9223372036854775808}', {"v": -(2**63)}),
        (b'{"v":18446744073709551615}', {"v": 2**64 - 1}),
        (b'{"v":18446744073709551616}', {"v": 2**64}),
        (b'{"v":1.25}', {"v": 1.25}),
        (b'{"v":1e+20}', {"v": 1e20}),
        (b'{"v":Infinity}', {"v": float("inf")}),
        (b'{"v":-Infinity}', {"v": float("-inf")}),
        (b'{"v":"\\u007f"}', {"v": "\x7f"}),
        (b'{"v":"\\u0416"}', {"v": "\u0416"}),
        (b'{"v":"\\ud800"}', {"v": "\ud800"}),
    ),
)
def test_selected_json_preserves_historical_values(raw: bytes, expected: dict[str, object]) -> None:
    assert _strict_json(raw) == expected


def test_selected_json_preserves_historical_nan() -> None:
    value = _strict_json(b'{"v":NaN}')["v"]
    assert isinstance(value, float) and math.isnan(value)


@pytest.mark.parametrize(
    "raw",
    (
        b'{"v":"\x7f"}',
        '{"v":"Ж"}'.encode(),
        b'{"b":1,"a":2}',
        b'{ "v":1}',
        b'{"v":-0}',
        b'{"v":1e20}',
        b"[]",
        b"null",
        '{"v":1}'.encode("utf-16"),
        '{"v":1}'.encode("utf-32"),
    ),
)
def test_selected_json_rejects_noncanonical_bytes(raw: bytes) -> None:
    with pytest.raises(ValueError, match="selected decision is noncanonical"):
        _strict_json(raw)


@pytest.mark.parametrize(
    "raw",
    (b'{"v":1,"v":2}', b'{"v":{"x":1,"x":2}}', b'{"v":1.25,"v":2.5}'),
)
def test_selected_json_preserves_duplicate_specific_errors(raw: bytes) -> None:
    with pytest.raises(ValueError, match="selected decision has duplicate JSON key"):
        _strict_json(raw)


def test_noncanonical_reader_keeps_duplicate_check_and_whitespace_acceptance() -> None:
    assert _strict_json(b'{ "v": 1 }', canonical=False) == {"v": 1}
    with pytest.raises(ValueError, match="selected decision has duplicate JSON key"):
        _strict_json(b'{"v":1,"v":2}', canonical=False)
