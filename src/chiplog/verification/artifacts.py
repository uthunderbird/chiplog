from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .identity import canonical_json_bytes, sha256_bytes


@contextmanager
def _directory_descriptor(path: Path) -> Iterator[int]:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _temporary_artifact(prefix: str, directory: Path) -> Iterator[tuple[Path, BinaryIO]]:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            yield temporary, stream
    finally:
        temporary.unlink(missing_ok=True)


def write_artifact(root: Path, result: dict[str, object]) -> Path:
    directory = root / ".artifacts/verification"
    directory.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(result) + b"\n"
    artifact_id = sha256_bytes(payload)
    target = directory / f"{artifact_id}.json"
    with _temporary_artifact(f".{artifact_id}.", directory) as (temporary, stream):
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise RuntimeError("artifact identity collision") from None
        with _directory_descriptor(directory) as directory_descriptor:
            os.fsync(directory_descriptor)
    return target
