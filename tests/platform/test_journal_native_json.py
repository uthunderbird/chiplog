"""Native journal decoding preserves authenticated bytes and legacy inputs."""

import json
from pathlib import Path

import orjson
import pytest

from chiplog.adapters.driven.deployment_trust import _journal as journals


def test_cold_journal_prefix_uses_one_native_decode_per_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "journal"
    journal = journals.IndependentTenantDecisionJournal(path)
    first = journal.append(b"first", None)
    second = journal.append(b"second", first)
    original_body = path.read_bytes()
    journal._decoded = None
    calls = {"native": 0, "stdlib": 0}
    original_native, original_stdlib = orjson.loads, json.loads

    def native(raw: bytes) -> object:
        calls["native"] += 1
        result: object = original_native(raw)
        return result

    def stdlib(raw: bytes) -> object:
        calls["stdlib"] += 1
        result: object = original_stdlib(raw)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(orjson, "loads", native)
        patch.setattr(json, "loads", stdlib)
        actual = journal.entries()
    assert actual == ((first, None, b"first"), (second, first, b"second"))
    assert calls == {"native": 2, "stdlib": 0}
    assert path.read_bytes() == original_body


@pytest.fixture
def signed_row(tmp_path: Path) -> tuple[bytes, bytes, str]:
    path = tmp_path / "journal"
    journal = journals.IndependentTenantDecisionJournal(path)
    head = journal.append(b"canonical payload", None)
    return path.read_bytes(), path.with_suffix(".key").read_bytes(), head


@pytest.mark.parametrize("mode", ["utf16", "nan", "infinity", "duplicate"])
def test_native_journal_preserves_legacy_envelope_forms(
    signed_row: tuple[bytes, bytes, str], mode: str
) -> None:
    body, key, head = signed_row
    envelope = json.loads(body)
    if mode == "utf16":
        raw = json.dumps(envelope).encode("utf-16")
    elif mode == "duplicate":
        raw = b'{"ignored":0,"ignored":1,' + body.lstrip()[1:]
    else:
        envelope["ignored"] = float("nan" if mode == "nan" else "inf")
        raw = json.dumps(envelope).encode()
    assert journals._decode_authenticated_entries(raw, key, head) == (
        (head, None, b"canonical payload"),
    )


@pytest.mark.parametrize("field", ["decision", "decision_id", "predecessor", "authentication"])
def test_native_journal_preserves_oversized_integer_rejection_and_fallback(
    signed_row: tuple[bytes, bytes, str], monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    body, key, head = signed_row
    envelope = json.loads(body)
    envelope[field] = 10**30
    raw = json.dumps(envelope).encode()
    calls: list[bytes] = []
    original_stdlib = json.loads

    def stdlib(value: bytes) -> object:
        calls.append(value)
        result: object = original_stdlib(value)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(json, "loads", stdlib)
        with pytest.raises(RuntimeError):
            journals._decode_authenticated_entries(raw, key, head)
    assert calls == [raw]


@pytest.mark.parametrize("raw", [b"[]", b"null", b"1", b'"text"', b"{}", b"{"])
def test_native_journal_keeps_invalid_root_error_mapping(
    signed_row: tuple[bytes, bytes, str], raw: bytes
) -> None:
    _, key, head = signed_row
    with pytest.raises(RuntimeError):
        journals._decode_authenticated_entries(raw, key, head)
