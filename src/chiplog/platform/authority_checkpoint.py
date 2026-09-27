"""Immutable, content-addressed storage for verified full-authority preimages."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chiplog.architecture.r7_storage_surface import (
    AUTHORITY_STORAGE_MEMBERS,
    AUTHORITY_STORAGE_SURFACE_DIGEST,
)

MAX_AUTHORITY_PREIMAGE_BYTES = 16 * 1024 * 1024
_PRIMARY_KEY_COLUMNS = {
    "deletion_fences": ("tenant_id",),
    "evidence_inbox": ("tenant_id", "source_id", "evidence_id"),
    "publications": ("tenant_id", "operation_kind", "idempotency_key"),
    "records": ("tenant_id", "record_id"),
    "store_metadata": ("singleton",),
    "tenant_heads": ("tenant_id",),
}


class AuthorityCheckpointVerificationError(RuntimeError):
    """A checkpoint reference or its immutable blob cannot be authenticated."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AuthorityCheckpointRefV1(_StrictModel):
    format: Literal["authority-preimage-v1"]
    commitment_algorithm: Literal["authority-json-v1"]
    authority_surface_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    blob_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(ge=0, le=MAX_AUTHORITY_PREIMAGE_BYTES)


class AuthoritySnapshotTableV1(_StrictModel):
    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]


class VerifiedAuthoritySnapshot(_StrictModel):
    ref: AuthorityCheckpointRefV1
    preimage: bytes
    tables: tuple[AuthoritySnapshotTableV1, ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_scalar(value: object) -> None:
    if (
        value is None
        or isinstance(value, str)
        or (isinstance(value, int) and not isinstance(value, bool))
    ):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("non-finite scalar")
    if isinstance(value, dict):
        encoded = value.get("base64")
        if set(value) != {"base64"} or not isinstance(encoded, str):
            raise ValueError("invalid bytes scalar")
        decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
        if base64.b64encode(decoded).decode("ascii") != encoded:
            raise ValueError("noncanonical bytes scalar")
        return
    raise ValueError("unsupported scalar")


def _sqlite_sort_key(value: object) -> tuple[int, object]:
    if value is None:
        return (0, "")
    if isinstance(value, int) and not isinstance(value, bool):
        return (1, value)
    if isinstance(value, float):
        return (1, value)
    if isinstance(value, str):
        return (2, value.encode("utf-8"))
    if isinstance(value, dict):
        return (3, base64.b64decode(str(value["base64"]).encode("ascii"), validate=True))
    raise ValueError("unsupported scalar")


def _sqlite_row_sort_key(row: list[object]) -> tuple[tuple[int, object], ...]:
    return tuple(_sqlite_sort_key(value) for value in row)


def _decode_preimage(raw: bytes) -> tuple[AuthoritySnapshotTableV1, ...]:
    if not raw or len(raw) > MAX_AUTHORITY_PREIMAGE_BYTES:
        raise ValueError("authority preimage length is invalid")
    decoded = json.loads(raw, object_pairs_hook=_unique_object)
    expected = tuple(item for item in AUTHORITY_STORAGE_MEMBERS if item.authority_bearing)
    if not isinstance(decoded, list) or len(decoded) != len(expected):
        raise ValueError("authority preimage table count differs")
    tables: list[AuthoritySnapshotTableV1] = []
    for item, member in zip(decoded, expected, strict=True):
        if not isinstance(item, dict) or set(item) != {"columns", "rows", "table"}:
            raise ValueError("authority preimage table envelope differs")
        if item["table"] != member.table or item["columns"] != list(member.columns):
            raise ValueError("authority preimage manifest differs")
        rows = item["rows"]
        if not isinstance(rows, list):
            raise ValueError("authority preimage rows differ")
        parsed_rows: list[tuple[object, ...]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(member.columns):
                raise ValueError("authority preimage row differs")
            for scalar in row:
                _validate_scalar(scalar)
            parsed_rows.append(tuple(row))
        if rows != sorted(rows, key=_sqlite_row_sort_key):
            raise ValueError("authority preimage rows are reordered")
        key_indexes = tuple(
            member.columns.index(column) for column in _PRIMARY_KEY_COLUMNS[member.table]
        )
        if len({tuple(row[index] for index in key_indexes) for row in rows}) != len(rows):
            raise ValueError("authority preimage has duplicate primary row")
        tables.append(
            AuthoritySnapshotTableV1(
                table=member.table, columns=member.columns, rows=tuple(parsed_rows)
            )
        )
    canonical = json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
    if canonical != raw:
        raise ValueError("authority preimage is noncanonical")
    return tuple(tables)


class AuthorityCheckpointStore:
    """A broker-owned namespace; references never select a filesystem path."""

    def __init__(self, root: Path) -> None:
        self._root = Path(os.path.abspath(root))
        self._ensure_directory(self._root)
        self._ensure_directory(self._root / "objects")

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        AuthorityCheckpointStore._assert_safe_ancestry(path)
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        if current.is_symlink() or not stat.S_ISDIR(current.lstat().st_mode):
            raise AuthorityCheckpointVerificationError("checkpoint directory is unsafe")
        for directory in reversed(missing):
            with suppress(FileExistsError):
                directory.mkdir(mode=0o700)
            if directory.is_symlink() or not stat.S_ISDIR(directory.lstat().st_mode):
                raise AuthorityCheckpointVerificationError("checkpoint directory is unsafe")
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            parent_descriptor = os.open(
                directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)

    @staticmethod
    def _assert_safe_ancestry(path: Path) -> None:
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current /= component
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise AuthorityCheckpointVerificationError("checkpoint directory is unsafe")

    def _blob_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise AuthorityCheckpointVerificationError("checkpoint digest is invalid")
        return self._root / "objects" / digest[:2] / digest

    @staticmethod
    def _read_regular(path: Path, limit: int) -> bytes:
        try:
            named = path.lstat()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as error:
            raise AuthorityCheckpointVerificationError("checkpoint blob is unavailable") from error
        try:
            actual = os.fstat(descriptor)
            if (
                not stat.S_ISREG(named.st_mode)
                or not stat.S_ISREG(actual.st_mode)
                or actual.st_nlink != 1
                or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
                or actual.st_size > limit
            ):
                raise AuthorityCheckpointVerificationError("checkpoint blob is unsafe")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(limit + 1)
            after = os.fstat(descriptor)
            if (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise AuthorityCheckpointVerificationError("checkpoint blob changed during read")
            if len(raw) > limit:
                raise AuthorityCheckpointVerificationError("checkpoint blob exceeds limit")
            return raw
        finally:
            os.close(descriptor)

    def stage_verified(self, preimage: bytes) -> AuthorityCheckpointRefV1:
        try:
            _decode_preimage(preimage)
        except (TypeError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise AuthorityCheckpointVerificationError("checkpoint preimage is invalid") from error
        ref = AuthorityCheckpointRefV1(
            format="authority-preimage-v1",
            commitment_algorithm="authority-json-v1",
            authority_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
            blob_sha256=hashlib.sha256(preimage).hexdigest(),
            byte_length=len(preimage),
        )
        destination = self._blob_path(ref.blob_sha256)
        self._ensure_directory(destination.parent)
        try:
            existing = self._read_regular(destination, ref.byte_length)
        except AuthorityCheckpointVerificationError:
            existing = None
        if existing is not None:
            if existing != preimage:
                raise AuthorityCheckpointVerificationError(
                    "checkpoint digest collision or corruption"
                )
            return ref
        descriptor, temporary_name = tempfile.mkstemp(prefix=".stage-", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(preimage)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_stat = temporary.lstat()
            if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
                raise AuthorityCheckpointVerificationError("checkpoint temporary is unsafe")
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                existing = self._read_regular(destination, ref.byte_length)
                if existing != preimage:
                    raise AuthorityCheckpointVerificationError(
                        "checkpoint digest collision or corruption"
                    ) from None
            else:
                directory = os.open(
                    destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        verified = self.resolve_verified(
            ref,
            expected_resulting=ref.blob_sha256,
            expected_surface_digest=AUTHORITY_STORAGE_SURFACE_DIGEST,
        )
        if verified.preimage != preimage:
            raise AuthorityCheckpointVerificationError("checkpoint installation differs")
        return ref

    def resolve_verified(
        self,
        ref: AuthorityCheckpointRefV1,
        expected_resulting: str,
        expected_surface_digest: str,
    ) -> VerifiedAuthoritySnapshot:
        try:
            if (
                ref.byte_length > MAX_AUTHORITY_PREIMAGE_BYTES
                or ref.byte_length < 1
                or ref.authority_surface_digest != AUTHORITY_STORAGE_SURFACE_DIGEST
                or expected_surface_digest != AUTHORITY_STORAGE_SURFACE_DIGEST
                or ref.authority_surface_digest != expected_surface_digest
                or ref.blob_sha256 != expected_resulting
            ):
                raise ValueError("checkpoint reference binding differs")
            raw = self._read_regular(self._blob_path(ref.blob_sha256), ref.byte_length)
            if len(raw) != ref.byte_length or hashlib.sha256(raw).hexdigest() != ref.blob_sha256:
                raise ValueError("checkpoint blob digest differs")
            tables = _decode_preimage(raw)
            return VerifiedAuthoritySnapshot(ref=ref, preimage=raw, tables=tables)
        except (OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, AuthorityCheckpointVerificationError):
                raise
            raise AuthorityCheckpointVerificationError("checkpoint verification failed") from error
