"""Native journal decoding preserves authenticated bytes and legacy inputs."""

import json
from pathlib import Path

import orjson
import pytest

from chiplog.adapters.driven.deployment_trust import _journal as journals
from chiplog.platform.authority_gate import AuthorityGate, checked_file_identity


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


@pytest.mark.parametrize("existing_only", [False, True])
def test_native_journal_writer_retains_exact_legacy_row_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_only: bool
) -> None:
    database = tmp_path / "database.sqlite"
    database.touch()
    gate = AuthorityGate.for_database(database)
    path = tmp_path / "journal"
    journal = journals.IndependentTenantDecisionJournal.for_authority_bundle(
        path, authority_gate=gate
    )
    components = (
        path,
        path.with_suffix(".key"),
        path.with_suffix(".head"),
        path.with_suffix(".lock"),
    )
    for component in components:
        component.chmod(0o600)
    if existing_only:
        journal = journals.IndependentTenantDecisionJournal.for_existing_authority_bundle(
            path,
            authority_gate=gate,
            expected_body_identity=checked_file_identity(path),
            expected_key_identity=checked_file_identity(path.with_suffix(".key")),
            expected_lock_identity=checked_file_identity(path.with_suffix(".lock")),
        )
    calls = {"native": 0, "stdlib": 0}
    original_native, original_stdlib = orjson.dumps, json.dumps

    def native(value: object, *, option: int) -> bytes:
        calls["native"] += 1
        return original_native(value, option=option)

    def stdlib(value: object, *, sort_keys: bool, separators: tuple[str, str]) -> str:
        calls["stdlib"] += 1
        return original_stdlib(value, sort_keys=sort_keys, separators=separators)

    payload = b"opaque\x00\xff\xcf\x80"
    with monkeypatch.context() as patch:
        patch.setattr(orjson, "dumps", native)
        patch.setattr(json, "dumps", stdlib)
        first = journal.append(payload, None)
        prefix = path.read_bytes()
        second = journal.append(b"second", first)
    body = path.read_bytes()
    assert body.startswith(prefix)
    assert journal.entries() == ((first, None, payload), (second, first, b"second"))
    for raw in body.splitlines():
        envelope = json.loads(raw)
        assert set(envelope) == {"authentication", "decision", "decision_id", "predecessor"}
        assert raw == original_stdlib(envelope, sort_keys=True, separators=(",", ":")).encode()
    assert calls == {"native": 2, "stdlib": 0}
