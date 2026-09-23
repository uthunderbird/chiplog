"""Real retained-source files; no source-authentication or durable-token claim."""

import hashlib
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.adapters.driven.ingress_retained_source import (
    RetainedSourceAdapter,
    RetainedSourceError,
    RetainedSourceObservation,
    RetainedSourceTransfer,
    decode_retained_observation,
)

_SECRET = b"test-independent-retained-source-key"


def adapter(
    path: Path,
    *,
    create: bool = True,
    items: int = 2,
    total: int = 8,
    permitted: set[str] | None = None,
) -> RetainedSourceAdapter:
    def validate(token: str, observation: RetainedSourceObservation) -> bool:
        return permitted is not None and token in permitted and observation.slot_id == "one"

    return RetainedSourceAdapter(
        path,
        "tenant",
        "database",
        secret=_SECRET,
        allow_create=create,
        maximum_items=items,
        maximum_total_bytes=total,
        maximum_item_bytes=8,
        token_validator=validate,
    )


def raw_path(path: Path, slot: str = "one") -> Path:
    return path / (hashlib.sha256(slot.encode()).hexdigest() + ".raw")


def test_source_observation_does_not_read_body_and_transfer_requires_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed: set[str] = set()
    source = adapter(tmp_path / "source", permitted=allowed)
    source.provision("one", b"body")
    original_read = os.read
    body_reads: list[int] = []
    body_inode = raw_path(source.directory).stat().st_ino

    def watched(fd: int, size: int) -> bytes:
        if os.fstat(fd).st_ino == body_inode:
            body_reads.append(size)
        return original_read(fd, size)

    monkeypatch.setattr(os, "read", watched)
    observed = source.observe("one")
    assert source.verify(observed)
    assert body_reads == []
    with pytest.raises(RetainedSourceError, match="materialized token"):
        source.read(observed, "token")
    assert body_reads == [] and source.transfers == ()
    allowed.add("token")
    assert source.read(observed, "token") == b"body"
    assert body_reads
    transfers: list[RetainedSourceTransfer] = list(source.transfers)
    assert transfers[0].source_proof == observed.proof
    assert transfers[0].token_id == "token"
    assert raw_path(source.directory).read_bytes() == b"body"


def test_restart_preserves_observation_after_sibling_provision_and_retention(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source"
    source = adapter(path, permitted={"token"})
    source.provision("one", b"body")
    original = source.observe("one")
    source.provision("two", b"more")
    assert source.observe("one").canonical_bytes() == original.canonical_bytes()
    assert source.read(original, "token") == b"body"
    reopened = adapter(path, create=False, permitted={"token"})
    current = reopened.observe("one")
    assert current.canonical_bytes() == original.canonical_bytes()
    assert reopened.transfers == source.transfers
    with pytest.raises(RetainedSourceError, match="not issued"):
        reopened.verify(original)
    # Retained reread can recover a crash after transfer but before canonical stage.
    assert reopened.read(current, "token") == b"body"
    assert len(reopened.transfers) == 2
    assert reopened.verify(current)


def test_serialized_and_cloned_observations_never_issue_access(tmp_path: Path) -> None:
    source = adapter(tmp_path / "source", permitted={"token"})
    source.provision("one", b"body")
    observed = source.observe("one")
    decoded = decode_retained_observation(observed.canonical_bytes())
    assert decoded == observed
    for forged in (replace(observed), decoded):
        with pytest.raises(RetainedSourceError, match="not issued"):
            source.read(forged, "token")
    value = json.loads(observed.canonical_bytes())
    value["root_identity"][1] += 1
    with pytest.raises(RetainedSourceError, match="proof differs"):
        decode_retained_observation(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        )


@pytest.mark.parametrize("mutation", ["replace", "mutate", "hardlink", "symlink", "metadata"])
def test_raw_or_metadata_identity_tampering_holds(tmp_path: Path, mutation: str) -> None:
    source = adapter(tmp_path / "source", permitted={"token"})
    source.provision("one", b"body")
    observed = source.observe("one")
    path = raw_path(source.directory)
    if mutation == "replace":
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"body")
        replacement.replace(path)
    elif mutation == "mutate":
        path.write_bytes(b"evil")
    elif mutation == "hardlink":
        os.link(path, tmp_path / "alias")
    elif mutation == "symlink":
        external = tmp_path / "external"
        path.rename(external)
        path.symlink_to(external)
    else:
        metadata = path.with_suffix(".json")
        content = json.loads(metadata.read_bytes())
        content["value"]["raw_digest"] = "f" * 64
        metadata.write_text(json.dumps(content, sort_keys=True, separators=(",", ":")))
    with pytest.raises((RetainedSourceError, OSError)):
        source.read(observed, "token")
    assert source.transfers == ()


@pytest.mark.parametrize("items,total,first,second", [(1, 8, b"a", b"b"), (2, 4, b"1234", b"x")])
def test_all_provisioned_items_consume_reserve_before_any_token(
    tmp_path: Path,
    items: int,
    total: int,
    first: bytes,
    second: bytes,
) -> None:
    path = tmp_path / "source"
    source = adapter(path, items=items, total=total)
    source.provision("one", first)
    reopened = adapter(path, create=False, items=items, total=total)
    with pytest.raises(RetainedSourceError, match="reserve exhausted"):
        reopened.provision("two", second)
    assert not raw_path(path, "two").exists()
    assert reopened.transfers == ()


def test_missing_root_unknown_inventory_changed_profile_and_secret_hold(tmp_path: Path) -> None:
    path = tmp_path / "source"
    with pytest.raises(FileNotFoundError):
        adapter(path, create=False)
    source = adapter(path)
    source.provision("one", b"body")
    with pytest.raises(RetainedSourceError, match="already provisioned"):
        source.provision("one", b"body")
    with pytest.raises(RetainedSourceError, match="registry"):
        adapter(path, create=False, items=3)
    with pytest.raises(RetainedSourceError, match="signature"):
        RetainedSourceAdapter(path, "tenant", "database", secret=b"x" * 32, allow_create=False)
    (path / "orphan.raw").write_bytes(b"x")
    with pytest.raises(RetainedSourceError, match="inventory"):
        source.observe("one")
    with pytest.raises(RetainedSourceError, match="inventory"):
        adapter(path, create=False)


def test_provision_flushes_files_and_directory_before_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = adapter(tmp_path / "source")
    flushed: list[tuple[int, int]] = []
    original = os.fsync

    def track(fd: int) -> None:
        value = os.fstat(fd)
        flushed.append((value.st_ino, value.st_mode))
        original(fd)

    monkeypatch.setattr(os, "fsync", track)
    source.provision("one", b"body")
    assert len(flushed) == 3
    assert [stat.S_ISREG(mode) for _, mode in flushed] == [True, True, False]
    assert stat.S_ISDIR(flushed[-1][1])
    observed = source.observe("one")
    assert observed.raw_identity[1] == flushed[0][0]
    assert observed.metadata_identity[1] == flushed[1][0]


def test_crash_between_source_files_keeps_dangling_inventory_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source"
    source = adapter(path)
    original = source._write_new

    def fail(fd: int, name: str, raw: bytes) -> None:
        if name.endswith(".json"):
            raise OSError("simulated source metadata failure")
        original(fd, name, raw)

    monkeypatch.setattr(source, "_write_new", fail)
    with pytest.raises(OSError, match="metadata failure"):
        source.provision("one", b"body")
    assert raw_path(path).exists()
    with pytest.raises(RetainedSourceError, match="inventory"):
        adapter(path, create=False)


@pytest.mark.parametrize("control", ["lock", "transfers"])
def test_control_file_replacement_rejects_new_lock_or_observer(
    tmp_path: Path, control: str
) -> None:
    path = tmp_path / "source"
    source = adapter(path)
    source.provision("one", b"body")
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"")
    replacement.replace(path / control)
    with pytest.raises(RetainedSourceError, match="registry"):
        source.observe("one")


def test_token_lookup_can_verify_source_and_mutation_then_prevents_transfer(tmp_path: Path) -> None:
    path = tmp_path / "source"

    def token_lookup(token: str, observation: RetainedSourceObservation) -> bool:
        assert source.verify(observation)
        raw_path(path).write_bytes(b"evil")
        return True

    source = RetainedSourceAdapter(
        path,
        "tenant",
        "database",
        secret=_SECRET,
        allow_create=True,
        token_validator=token_lookup,
    )
    source.provision("one", b"body")
    with pytest.raises(RetainedSourceError, match="identity changed"):
        source.read(source.observe("one"), "token")
    assert source.transfers == ()


def test_two_adapter_provisioners_cannot_overbook_one_source_slot_reserve(tmp_path: Path) -> None:
    path = tmp_path / "source"
    first = adapter(path, items=1)
    second = adapter(path, create=False, items=1)

    def provision(source: RetainedSourceAdapter, slot: str) -> bool:
        try:
            source.provision(slot, b"body")
            return True
        except RetainedSourceError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(provision, first, "one"), pool.submit(provision, second, "two")]
        assert sorted(future.result() for future in futures) == [False, True]
    assert len(list(path.glob("*.raw"))) == 1
