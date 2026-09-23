"""Independent offline custody and provider crash durability boundaries."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.composition.r16_dispatch_custody import load_or_create
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.authority_gate import AuthorityGate
from tests.support.dispatch import _ticket


@pytest.mark.parametrize("separate_process", [False, True])
def test_live_custody_holder_observes_external_revocation(
    tmp_path: Path, separate_process: bool
) -> None:
    path = tmp_path / "keys.json"
    database = tmp_path / "authority.db"
    gate = AuthorityGate(database)
    holder = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    holder.bind(gate)
    original = holder.observe()
    assert holder.verify_current(original)
    if separate_process:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; "
                "from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources; "
                "from chiplog.platform.authority_gate import AuthorityGate; "
                "r=HermeticDispatchResources(scenarios=('CONFIRM',),cap=1,"
                "custody_path=Path(sys.argv[1])); "
                "r.bind(AuthorityGate(Path(sys.argv[2]))); r.revoke()",
                str(path),
                str(database),
            ],
            check=True,
            timeout=20,
        )
    else:
        revoker = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
        revoker.bind(gate)
        assert revoker.grant_identity == holder.grant_identity
        revoker.revoke()
    assert not holder.verify_current(original)
    with pytest.raises(ValueError, match="no longer current"):
        holder.recipient(original)
    assert holder.verify_historical(original)
    assert not holder.verify_current(holder.observe())
    path.with_suffix(".revoked").unlink()
    assert not holder.verify_current(holder.observe())


def test_revocation_read_failure_denies_current_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "keys.json"
    holder = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    holder.bind(AuthorityGate(tmp_path / "authority.db"))
    original = holder.observe()
    lstat = Path.lstat

    def denied(target: Path) -> os.stat_result:
        if target == path.with_suffix(".revoked"):
            raise PermissionError("revocation storage unreadable")
        return lstat(target)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(PermissionError, match="revocation storage"):
        holder.recipient(original)
    assert holder.verify_historical(original)


def test_private_custody_reopens_and_rejects_alias_unknown_or_nonregular(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    first = load_or_create(path, ("CONFIRM",), 1)
    assert load_or_create(path, ("CONFIRM",), 1) == first
    with pytest.raises(ValueError):
        load_or_create(path, ("MIXED",), 1)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        load_or_create(alias, ("CONFIRM",), 1)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError, match="regular"):
        load_or_create(fifo, ("CONFIRM",), 1)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_or_create(path, ("CONFIRM",), 1)


async def test_provider_directory_sync_failure_remains_reconcilable_not_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "provider.jsonl"
    provider = HermeticEffectsProvider(
        receipt_key=b"key", scenarios=("CONFIRM",), journal_path=path
    )
    fsync = os.fsync
    observed: list[str] = []

    def fail_directory(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        observed.append(kind)
        if kind == "directory":
            raise OSError("injected after receipt fsync before directory sync")
        fsync(descriptor)

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="directory sync"):
            await provider.emit_issued(_ticket())
    assert observed == ["file", "directory"]
    reopened = HermeticEffectsProvider(
        receipt_key=b"key", scenarios=("CONFIRM",), journal_path=path
    )
    assert len(reopened.transfers) == 1
    assert reopened.reconcile("child0") is not None
    with pytest.raises(ValueError, match="reenqueue"):
        await reopened.emit_issued(_ticket())


def test_revocation_directory_sync_failure_is_not_reported_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "keys.json"
    database = tmp_path / "authority.db"
    database.touch()
    gate = AuthorityGate(database)
    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    resources.bind(gate)
    original = resources.observe()
    assert resources.verify_current(original)
    fsync = os.fsync
    observed: list[str] = []

    def sync(descriptor: int) -> None:
        kind = "directory" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file"
        observed.append(kind)
        fsync(descriptor)

    def fail_directory(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            observed.append("directory")
            raise OSError("injected revocation directory sync failure")
        sync(descriptor)

    with monkeypatch.context() as fault:
        fault.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="revocation directory"):
            resources.revoke()
    assert observed == ["file", "directory"]
    assert not resources.verify_current(original)
    reopened = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    reopened.bind(gate)
    assert not reopened.verify_current(reopened.observe())
    observed.clear()
    with monkeypatch.context() as checked:
        checked.setattr(os, "fsync", sync)
        reopened.revoke()
    assert observed == ["file", "directory"]
    final = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1, custody_path=path)
    final.bind(gate)
    assert not final.verify_current(final.observe())
