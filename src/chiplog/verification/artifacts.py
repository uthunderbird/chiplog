from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .identity import canonical_json_bytes, sha256_bytes


def write_artifact(root: Path, result: dict[str, object]) -> Path:
    directory = root / ".artifacts/verification"
    directory.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(result) + b"\n"
    artifact_id = sha256_bytes(payload)
    target = directory / f"{artifact_id}.json"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{artifact_id}.", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != payload:
                raise RuntimeError("artifact identity collision") from None
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return target
