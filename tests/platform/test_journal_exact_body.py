"""Exact-body decode reuse must still observe every durable corruption."""

import json
import os
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate


@pytest.mark.parametrize("bound", (False, True))
@pytest.mark.parametrize("mutation", ("body", "head", "missing_head", "key", "replacement"))
def test_warm_journal_rejects_changed_durable_sources(
    tmp_path: Path,
    bound: bool,
    mutation: str,
) -> None:
    path = tmp_path / "journal"
    journal = (
        IndependentTenantDecisionJournal.for_authority_bundle(
            path, authority_gate=AuthorityGate.for_database(tmp_path / "db.sqlite")
        )
        if bound
        else IndependentTenantDecisionJournal(path)
    )
    first = journal.append(b"original", None)
    expected = ((first, None, b"original"),)
    assert journal.entries() == expected
    body = path.read_bytes()
    metadata = path.stat()
    if mutation == "body":
        changed = body.replace(b"6f726967696e616c", b"616c746572656421")
        assert changed != body and len(changed) == len(body)
        path.write_bytes(changed)
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    elif mutation == "head":
        path.with_suffix(".head").write_text("0" * 64)
    elif mutation == "missing_head":
        path.with_suffix(".head").unlink()
    elif mutation == "key":
        path.with_suffix(".key").write_bytes(b"x" * 32)
    else:
        replacement = tmp_path / "replacement"
        replacement.write_bytes(body)
        replacement.replace(path)
    with pytest.raises((RuntimeError, OSError)):
        journal.entries()
    if mutation == "body":
        path.write_bytes(body)
        assert journal.entries() == expected


def test_exact_body_reuses_decode_but_append_reads_new_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "journal"
    journal = IndependentTenantDecisionJournal(path)
    first = journal.append(b"first", None)
    expected = journal.entries()
    original = json.loads
    calls = 0

    def counted(raw: bytes) -> object:
        nonlocal calls
        calls += 1
        return original(raw)

    monkeypatch.setattr(json, "loads", counted)
    assert journal.entries() == expected
    assert calls == 0
    second = journal.append(b"second", first)
    assert journal.entries() == (*expected, (second, first, b"second"))
    assert calls == 2
    assert journal.entries() == (*expected, (second, first, b"second"))
    assert calls == 2
