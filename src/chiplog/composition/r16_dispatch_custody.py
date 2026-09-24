"""Explicit independent offline custody; never derive provider keys from tenant data."""

import json
import os
import secrets
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

_CUSTODY_SCHEMA = "chiplog.hermetic-dispatch-custody.v1"
_CUSTODY_KEYS = frozenset(
    {
        "schema",
        "issuer_key",
        "receipt_key",
        "grant_id",
        "credential_id",
        "scenarios",
        "cap",
    }
)
_SCENARIOS = frozenset({"CONFIRM", "PERMANENT_NO_EFFECT", "LOST_RESPONSE_AFTER_EFFECT", "MIXED"})
_MAX_CUSTODY_BYTES = 4096


@dataclass(frozen=True, slots=True)
class HistoricalDispatchCustody:
    """Authenticated, read-only original R16 custody facts for historical checks."""

    issuer_key: bytes = field(repr=False)
    credential_id: str
    grant_id: str
    cap: int


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        type(value) is str
        and len(value) == length
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _has_prefixed_hex(value: object, prefix: str, hex_length: int) -> bool:
    return (
        type(value) is str
        and value.startswith(prefix)
        and _is_lower_hex(value[len(prefix) :], hex_length)
    )


def _read_existing_custody(path: Path) -> dict[str, object]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o077
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError("offline custody path is not private regular storage")
        chunks: list[bytes] = []
        remaining = _MAX_CUSTODY_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_CUSTODY_BYTES:
        raise ValueError("offline custody storage exceeds the bounded schema")
    try:
        retained = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ValueError("offline custody storage is not canonical JSON") from None
    if (
        not isinstance(retained, dict)
        or raw != json.dumps(retained, sort_keys=True, separators=(",", ":")).encode()
    ):
        raise ValueError("offline custody storage is not canonical JSON")
    return retained


def load_existing_historical_custody(path: Path) -> HistoricalDispatchCustody:
    """Open one durable custody record without provisioning any live resource."""
    retained = _read_existing_custody(path)
    scenarios = retained.get("scenarios")
    cap = retained.get("cap")
    if (
        set(retained) != _CUSTODY_KEYS
        or retained.get("schema") != _CUSTODY_SCHEMA
        or not _is_lower_hex(retained.get("issuer_key"), 64)
        or not _is_lower_hex(retained.get("receipt_key"), 64)
        or not _has_prefixed_hex(retained.get("grant_id"), "hermetic-send-grant/", 48)
        or not _has_prefixed_hex(retained.get("credential_id"), "hermetic-send-credential/", 48)
        or type(scenarios) is not list
        or not scenarios
        or any(type(scenario) is not str or scenario not in _SCENARIOS for scenario in scenarios)
        or len(set(scenarios)) != len(scenarios)
        or type(cap) is not int
        or not 1 <= cap <= len(scenarios)
    ):
        raise ValueError("offline custody storage has an invalid historical schema")
    issuer_key = retained["issuer_key"]
    credential_id = retained["credential_id"]
    grant_id = retained["grant_id"]
    assert type(issuer_key) is str
    assert type(credential_id) is str
    assert type(grant_id) is str
    return HistoricalDispatchCustody(
        issuer_key=bytes.fromhex(issuer_key),
        credential_id=credential_id,
        grant_id=grant_id,
        cap=cap,
    )


def load_or_create(path: Path, scenarios: tuple[str, ...], cap: int) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": _CUSTODY_SCHEMA,
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
