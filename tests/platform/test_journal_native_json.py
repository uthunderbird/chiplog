"""Journal roundtrips preserve payloads and reject malformed authenticated rows."""

import json
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import _journal as journals
from chiplog.platform.authority_gate import AuthorityGate, checked_file_identity


def test_cold_journal_prefix_preserves_payloads(tmp_path: Path) -> None:
    path = tmp_path / "journal"
    journal = journals.IndependentTenantDecisionJournal(path)
    first = journal.append(b"first", None)
    second = journal.append(b"second", first)
    original_body = path.read_bytes()
    journal._decoded = None
    actual = journal.entries()
    assert actual == ((first, None, b"first"), (second, first, b"second"))
    assert path.read_bytes() == original_body


@pytest.fixture
def signed_row(tmp_path: Path) -> tuple[bytes, bytes, str]:
    path = tmp_path / "journal"
    journal = journals.IndependentTenantDecisionJournal(path)
    head = journal.append(b"canonical payload", None)
    return path.read_bytes(), path.with_suffix(".key").read_bytes(), head


@pytest.mark.parametrize("field", ["decision", "decision_id", "predecessor", "authentication"])
def test_native_journal_rejects_oversized_integer_fields(
    signed_row: tuple[bytes, bytes, str], field: str
) -> None:
    body, key, head = signed_row
    envelope = json.loads(body)
    envelope[field] = 10**30
    raw = json.dumps(envelope).encode()
    with pytest.raises(RuntimeError):
        journals._decode_authenticated_entries(raw, key, head)


@pytest.mark.parametrize("raw", [b"[]", b"null", b"1", b'"text"', b"{}", b"{"])
def test_native_journal_keeps_invalid_root_error_mapping(
    signed_row: tuple[bytes, bytes, str], raw: bytes
) -> None:
    _, key, head = signed_row
    with pytest.raises(RuntimeError):
        journals._decode_authenticated_entries(raw, key, head)


@pytest.mark.parametrize("existing_only", [False, True])
def test_native_journal_writer_reopens_opaque_payloads(tmp_path: Path, existing_only: bool) -> None:
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
    payload = b"opaque\x00\xff\xcf\x80"
    first = journal.append(payload, None)
    prefix = path.read_bytes()
    second = journal.append(b"second", first)
    body = path.read_bytes()
    assert body.startswith(prefix)
    assert journal.entries() == ((first, None, payload), (second, first, b"second"))
