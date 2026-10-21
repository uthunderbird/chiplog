"""Physical journal snapshots authenticate existing bytes without provisioning."""

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import _journal as journals
from chiplog.platform.authority_gate import AuthorityGateError
from tests.support.platform.existing_only_decision_journal import _provision


def _files(directory):
    return {
        path.name: (
            os.readlink(path) if path.is_symlink() else path.read_bytes(),
            path.lstat().st_mode,
        )
        for path in directory.iterdir()
    }


def test_readonly_snapshot_authenticates_real_journal_without_writable_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, gate, first, _ = _provision(tmp_path)
    before = _files(tmp_path)
    opened = []
    original_open = os.open

    def observe(name, flags, *args, **kwargs):
        opened.append((str(name), flags))
        return original_open(name, flags, *args, **kwargs)

    with gate.hold_existing():
        monkeypatch.setattr(os, "open", observe)
        snapshot = journals.read_authenticated_journal_snapshot(path, authority_gate=gate)
    assert snapshot.entries == ((first, None, b"first"),)
    with pytest.raises(FrozenInstanceError):
        snapshot.entries = ()
    assert all(
        flags & os.O_ACCMODE == os.O_RDONLY and not flags & os.O_CREAT for _, flags in opened
    )
    assert {Path(name).name for name, _ in opened} >= {
        path.name,
        path.name + ".key",
        path.name + ".head",
        path.name + ".lock",
    }
    assert _files(tmp_path) == before


def test_readonly_snapshot_requires_preheld_gate_before_any_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, gate, _, _ = _provision(tmp_path)
    opened = []

    def forbidden(*args, **kwargs) -> None:
        opened.append(args)
        pytest.fail("unheld snapshot opened a journal component")

    monkeypatch.setattr(os, "open", forbidden)
    with pytest.raises(AuthorityGateError):
        journals.read_authenticated_journal_snapshot(path, authority_gate=gate)
    assert opened == []


@pytest.mark.parametrize(
    "damage",
    [
        "missing-body",
        "missing-key",
        "missing-head",
        "missing-lock",
        "body",
        "key",
        "short-key",
        "head",
        "non-ascii-head",
        "head-new",
        "mode",
        "symlink",
        "hardlink",
    ],
)
def test_readonly_snapshot_refuses_bad_sources_without_repair(tmp_path: Path, damage: str) -> None:
    path, gate, _, _ = _provision(tmp_path)
    if damage.startswith("missing-"):
        suffix = {"body": "", "key": ".key", "head": ".head", "lock": ".lock"}[
            damage.removeprefix("missing-")
        ]
        path.with_name(path.name + suffix).unlink()
    elif damage == "body":
        raw = path.read_bytes()
        changed = raw.replace(b"6669727374", b"77726f6e67")
        assert changed != raw
        path.write_bytes(changed)
    elif damage in {"key", "short-key"}:
        path.with_name(path.name + ".key").write_bytes(b"x" * (32 if damage == "key" else 1))
    elif damage in {"head", "non-ascii-head"}:
        path.with_name(path.name + ".head").write_bytes(b"0" * 64 if damage == "head" else b"\xff")
    elif damage == "head-new":
        path.with_name(path.name + ".head.new").write_bytes(b"pending")
    elif damage == "mode":
        path.chmod(0o644)
    else:
        original = path.with_name("original")
        path.rename(original)
        if damage == "symlink":
            path.symlink_to(original)
        else:
            path.hardlink_to(original)
    before = _files(tmp_path)
    with gate.hold_existing(), pytest.raises(RuntimeError):
        journals.read_authenticated_journal_snapshot(path, authority_gate=gate)
    assert _files(tmp_path) == before


@pytest.mark.parametrize("mutation", ["body", "key", "head-replacement", "head-new"])
def test_readonly_snapshot_rechecks_sources_after_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    path, gate, _, _ = _provision(tmp_path)
    decode = journals._decode_authenticated_entries
    mutated = []

    def change(body, key, head):
        result = decode(body, key, head)
        if mutation == "body":
            path.write_bytes(path.read_bytes() + b" ")
        elif mutation == "key":
            path.with_name(path.name + ".key").write_bytes(b"x" * 32)
        elif mutation == "head-replacement":
            target = path.with_name(path.name + ".head")
            replacement = path.with_name("replacement")
            replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(target)
        else:
            path.with_name(path.name + ".head.new").write_bytes(b"pending")
        mutated.append(_files(tmp_path))
        return result

    monkeypatch.setattr(journals, "_decode_authenticated_entries", change)
    with gate.hold_existing(), pytest.raises(RuntimeError):
        journals.read_authenticated_journal_snapshot(path, authority_gate=gate)
    assert len(mutated) == 1
    assert _files(tmp_path) == mutated[0]


def test_readonly_snapshot_closes_partial_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, gate, _, _ = _provision(tmp_path)
    original = os.open
    opened = []

    def fail_at_key(name, flags, *args, **kwargs):
        if str(name) == path.name + ".key":
            raise PermissionError("key read denied")
        descriptor = original(name, flags, *args, **kwargs)
        opened.append(descriptor)
        return descriptor

    before = _files(tmp_path)
    with gate.hold_existing():
        monkeypatch.setattr(os, "open", fail_at_key)
        with pytest.raises(RuntimeError, match="unavailable"):
            journals.read_authenticated_journal_snapshot(path, authority_gate=gate)
        assert opened
        for descriptor in opened:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    assert _files(tmp_path) == before
