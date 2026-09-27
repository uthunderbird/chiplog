"""The recovery-only journal opener never provisions authority sidecars."""

import os
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate, FileIdentity, checked_file_identity


def _provision(tmp_path: Path) -> tuple[Path, AuthorityGate, str, tuple[FileIdentity, ...]]:
    database = tmp_path / "authority.sqlite"
    database.touch()
    gate = AuthorityGate.for_database(database)
    path = tmp_path / "journal"
    journal = IndependentTenantDecisionJournal.for_authority_bundle(path, authority_gate=gate)
    decision_id = journal.append(b"first", None)
    for sidecar in (
        path,
        path.with_suffix(".head"),
        path.with_suffix(".key"),
        path.with_suffix(".lock"),
    ):
        sidecar.chmod(0o600)
    return path, gate, decision_id, tuple(
        checked_file_identity(sidecar)
        for sidecar in (path, path.with_suffix(".key"), path.with_suffix(".lock"))
    )


def _open(
    path: Path, gate: AuthorityGate, expected: tuple[FileIdentity, ...]
) -> IndependentTenantDecisionJournal:
    return IndependentTenantDecisionJournal.for_existing_authority_bundle(
        path,
        authority_gate=gate,
        expected_body_identity=expected[0],
        expected_key_identity=expected[1],
        expected_lock_identity=expected[2],
    )


def _journal_names(path: Path) -> set[str]:
    return {entry.name for entry in path.parent.iterdir() if entry.name.startswith("journal")}


@pytest.mark.parametrize("suffix", ("", ".key", ".head", ".lock"))
def test_existing_only_open_rejects_missing_component_without_repair(
    tmp_path: Path, suffix: str
) -> None:
    path, gate, _, expected = _provision(tmp_path)
    missing = path.with_suffix(path.suffix + suffix)
    missing.unlink()
    before = _journal_names(path)

    with pytest.raises(RuntimeError):
        _open(path, gate, expected)

    assert not missing.exists()
    assert _journal_names(path) == before


@pytest.mark.parametrize("kind", ("symlink", "hardlink", "replacement"))
@pytest.mark.parametrize("suffix", ("", ".key", ".head", ".lock"))
def test_existing_only_open_rejects_unprotected_component(
    tmp_path: Path, suffix: str, kind: str
) -> None:
    path, gate, _, expected = _provision(tmp_path)
    component = path.with_suffix(path.suffix + suffix)
    original = component.with_name(component.name + ".original")
    component.rename(original)
    if kind == "symlink":
        component.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, component)
    else:
        component.write_bytes(b"stale-head" if suffix == ".head" else original.read_bytes())
        component.chmod(0o600)
    before = _journal_names(path)

    with pytest.raises(RuntimeError):
        _open(path, gate, expected)

    assert _journal_names(path) == before


def test_existing_only_open_authenticates_and_allows_append_and_head_replacement(
    tmp_path: Path,
) -> None:
    path, gate, first, expected = _provision(tmp_path)
    prior_head = path.with_suffix(".head").stat().st_ino

    recovered = _open(path, gate, expected)

    assert recovered.entries() == ((first, None, b"first"),)
    body, key, head = recovered.physical_sources()
    assert body[0] == str(path)
    assert key[0] == str(path.with_suffix(".key"))
    assert head[0] == str(path.with_suffix(".head"))
    second = recovered.append(b"second", first)

    assert path.with_suffix(".head").stat().st_ino != prior_head
    assert recovered.entries() == ((first, None, b"first"), (second, first, b"second"))
    assert _open(path, gate, expected).entries()[-1] == (second, first, b"second")


def test_existing_only_open_rejects_interrupted_head_publication_without_removing_it(
    tmp_path: Path,
) -> None:
    path, gate, _, expected = _provision(tmp_path)
    temporary = path.with_suffix(".head.new")
    temporary.write_text("interrupted", encoding="ascii")
    temporary.chmod(0o600)

    with pytest.raises(RuntimeError):
        _open(path, gate, expected)

    assert temporary.read_text(encoding="ascii") == "interrupted"


def test_existing_only_journal_rejects_pinned_source_replacement_after_open(tmp_path: Path) -> None:
    path, gate, _, expected = _provision(tmp_path)
    recovered = _open(path, gate, expected)
    key = path.with_suffix(".key")
    replacement = key.with_name("replacement.key")
    replacement.write_bytes(key.read_bytes())
    replacement.chmod(0o600)
    os.replace(replacement, key)

    with pytest.raises(RuntimeError):
        recovered.entries()


def test_existing_only_journal_rejects_changed_key_bytes_after_open(tmp_path: Path) -> None:
    path, gate, _, expected = _provision(tmp_path)
    recovered = _open(path, gate, expected)
    key = path.with_suffix(".key")
    key.write_bytes(os.urandom(32))

    with pytest.raises(RuntimeError, match="key changed"):
        recovered.entries()


def test_legacy_constructor_still_provisions_an_empty_journal(tmp_path: Path) -> None:
    path = tmp_path / "legacy"

    journal = IndependentTenantDecisionJournal(path)

    assert journal.entries() == ()
    assert path.exists()
    assert path.with_suffix(".key").exists()
    assert path.with_suffix(".head").exists()
    assert path.with_suffix(".lock").exists()
