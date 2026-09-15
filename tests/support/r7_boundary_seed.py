"""Reusable, private copies of the real 1001-command R6 boundary fixture."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from chiplog.capabilities.planning import CreateIntentionLine
from chiplog.composition.r6 import open_r6_runtime
from chiplog.domain_primitives import RecordId, TenantId
from chiplog.platform.authority_reads import (
    AuthorityCommitmentJournal,
    capture_authority_storage_state,
)

_ROOT = Path(__file__).resolve().parents[2]


def _seed_key() -> str:
    digest = hashlib.sha256()
    digest.update(repr((sys.version, sqlite3.sqlite_version, platform.platform())).encode())
    paths = [
        path
        for directory in (_ROOT / "src", _ROOT / "tests")
        for path in directory.rglob("*")
        if path.suffix in {".py", ".json"} and path.is_file()
    ]
    paths.extend((_ROOT / "pyproject.toml", _ROOT / "uv.lock"))
    for path in sorted(paths):
        digest.update(path.relative_to(_ROOT).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _files(directory: Path) -> dict[str, str]:
    result = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("boundary snapshot must contain only ordinary files")
        result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "chiplog.sqlite3" not in result:
        raise ValueError("boundary snapshot database is missing")
    return result


def prepare_boundary_database(database: Path) -> None:
    """Regenerate on changed inputs/corruption; never cache a test result.

    The cache has the same trust boundary as local test sources. Checksums detect
    damaged data, not an actor who can rewrite both fixture files and manifest.
    Set CHIPLOG_FRESH_BOUNDARY_SEED=1 to exercise the original complete seed path.
    """
    if database.name != "chiplog.sqlite3" or any(database.parent.iterdir()):
        raise ValueError("boundary fixture requires an empty private directory")
    if os.environ.get("CHIPLOG_FRESH_BOUNDARY_SEED") == "1":
        _build_boundary_seed(database.parent)
        return
    cache_parent = Path(
        os.environ.get(
            "CHIPLOG_BOUNDARY_SEED_CACHE",
            str(Path(tempfile.gettempdir()) / f"chiplog-test-seeds-{os.getuid()}"),
        )
    )
    cache = cache_parent / hashlib.sha256(str(_ROOT).encode()).hexdigest()[:16]
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = _seed_key()
    with (cache / "lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        snapshot = cache / "snapshot"
        try:
            manifest = json.loads((snapshot / "manifest.json").read_text())
            valid = manifest["key"] == key and manifest["files"] == _files(snapshot / "data")
        except OSError, ValueError, KeyError, TypeError:
            valid = False
        if not valid:
            with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as temporary:
                staging = Path(temporary)
                data = staging / "data"
                data.mkdir()
                _build_boundary_seed(data)
                if _seed_key() != key:
                    raise RuntimeError("seed inputs changed during generation; rerun the test")
                manifest = {"key": key, "files": _files(data)}
                (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
                if snapshot.exists():
                    shutil.rmtree(snapshot)
                staging.rename(snapshot)
        expected = manifest["files"]
        for path in (snapshot / "data").iterdir():
            shutil.copyfile(path, database.parent / path.name)
        if _files(database.parent) != expected:
            raise RuntimeError("boundary fixture copy differs from its recorded snapshot")


def _build_boundary_seed(directory: Path) -> None:
    database = directory / "chiplog.sqlite3"
    tenant = TenantId("tenant-1")
    secret = b"boundary-secret"

    async def seed_r6() -> None:
        async with open_r6_runtime(database, operator_secret=secret) as runtime:
            await runtime.bootstrap(
                tenant_id=tenant.value,
                database_instance_id="database-1",
                principal_id="principal-1",
                credential_id="credential-1",
                session_id="session-1",
                token="bootstrap-token-1",
            )
            for index in range(1, 1002):
                outcome = await runtime.create(
                    tenant_id=tenant.value,
                    principal_id="principal-1",
                    credential_id="credential-1",
                    session_id="session-1",
                    command=CreateIntentionLine(
                        RecordId(tenant, f"command-{index:04d}"),
                        RecordId(tenant, f"intention-{index:04d}"),
                        RecordId(tenant, f"revision-{index:04d}"),
                        f"purpose-{index:04d}",
                        f"act-{index:04d}",
                    ),
                )
                assert outcome.disposition == "COMMITTED"

    asyncio.run(seed_r6())
    for suffix in (
        ".trust-journal",
        ".trust-journal.head",
        ".trust-journal.key",
        ".trust-journal.lock",
    ):
        database.with_suffix(database.suffix + suffix).unlink(missing_ok=True)
    database.with_suffix(database.suffix + ".trust.sqlite3").unlink(missing_ok=True)
    commitment, _ = capture_authority_storage_state(database)
    AuthorityCommitmentJournal(database, secret).commit(tenant.value, commitment)
