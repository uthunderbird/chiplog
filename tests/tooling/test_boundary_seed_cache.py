from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.support import r7_boundary_seed as seed


def test_copies_are_private_and_corrupt_or_outdated_cache_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIPLOG_BOUNDARY_SEED_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CHIPLOG_FRESH_BOUNDARY_SEED", raising=False)
    calls: list[Path] = []
    version = ["first"]

    def build(directory: Path) -> None:
        calls.append(directory)
        (directory / "chiplog.sqlite3").write_bytes(b"original database")
        (directory / "chiplog.sqlite3.anchor").write_bytes(b"original anchor")

    monkeypatch.setattr(seed, "_build_boundary_seed", build)
    monkeypatch.setattr(seed, "_seed_key", lambda: version[0])

    def copy(number: int) -> Path:
        directory = tmp_path / str(number)
        directory.mkdir()
        database = directory / "chiplog.sqlite3"
        seed.prepare_boundary_database(database)
        return database

    first = copy(1)
    first.write_bytes(b"mutated by first test")
    assert copy(2).read_bytes() == b"original database"
    assert len(calls) == 1
    stored = next((tmp_path / "cache").glob("*/snapshot/data/chiplog.sqlite3"))
    stored.write_bytes(b"damaged cache")
    assert copy(3).read_bytes() == b"original database"
    assert len(calls) == 2
    version[0] = "second"
    assert copy(4).read_bytes() == b"original database"
    assert len(calls) == 3
    monkeypatch.setenv("CHIPLOG_FRESH_BOUNDARY_SEED", "1")
    assert copy(5).read_bytes() == b"original database"
    assert len(calls) == 4


def test_source_key_changes_with_source_names_bytes_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed, "_ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    source = tmp_path / "src" / "example.py"
    source.write_text("first")
    (tmp_path / "pyproject.toml").write_text("config")
    lock = tmp_path / "uv.lock"
    lock.write_text("lock")
    original = seed._seed_key()
    source.write_text("second")
    changed = seed._seed_key()
    assert changed != original
    source.rename(source.with_name("renamed.py"))
    renamed = seed._seed_key()
    assert renamed != changed
    lock.write_text("new lock")
    assert seed._seed_key() != renamed


def test_two_processes_build_once_and_receive_independent_copies(tmp_path: Path) -> None:
    script = """
from pathlib import Path
import sys, time
from tests.support import r7_boundary_seed as seed
root, target = Path(sys.argv[1]), Path(sys.argv[2])
def build(directory):
    with (root / 'builds').open('a') as log:
        log.write('built\\n')
    time.sleep(0.1)
    (directory / 'chiplog.sqlite3').write_bytes(b'fixture')
seed._build_boundary_seed = build
seed._seed_key = lambda: 'concurrent-fixture'
target.mkdir()
seed.prepare_boundary_database(target / 'chiplog.sqlite3')
"""
    env = {**os.environ, "CHIPLOG_BOUNDARY_SEED_CACHE": str(tmp_path / "cache")}
    env.pop("CHIPLOG_FRESH_BOUNDARY_SEED", None)

    def execute(name: str) -> None:
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), str(tmp_path / name)],
            env=env,
            check=True,
            capture_output=True,
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, ("first", "second")))
    assert (tmp_path / "builds").read_text().splitlines() == ["built"]
    (tmp_path / "first/chiplog.sqlite3").write_bytes(b"changed")
    assert (tmp_path / "second/chiplog.sqlite3").read_bytes() == b"fixture"
