"""Explicit independent offline custody; never derive provider keys from tenant data."""

import json
import os
import secrets
import stat
from contextlib import ExitStack
from pathlib import Path


def load_or_create(path: Path, scenarios: tuple[str, ...], cap: int) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": "chiplog.hermetic-dispatch-custody.v1",
        "issuer_key": secrets.token_hex(32),
        "receipt_key": secrets.token_hex(32),
        "grant_id": "hermetic-send-grant/" + secrets.token_hex(24),
        "credential_id": "hermetic-send-credential/" + secrets.token_hex(24),
        "scenarios": list(scenarios),
        "cap": cap,
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid != os.getuid()
            ):
                raise ValueError("offline custody path is not private regular storage") from None
            retained = json.loads(stream.read())
        if (
            not isinstance(retained, dict)
            or set(retained) != set(value)
            or retained["schema"] != value["schema"]
            or retained["scenarios"] != list(scenarios)
            or retained["cap"] != cap
        ):
            raise ValueError("offline custody configuration differs") from None
        return retained
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        with ExitStack() as resources:
            directory = os.open(path.parent, os.O_RDONLY)
            resources.callback(os.close, directory)
            os.fsync(directory)
        return value
