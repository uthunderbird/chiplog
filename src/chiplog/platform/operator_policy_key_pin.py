"""Authority-gated immutable operator-policy key pin snapshots.

This module reads the provisioned pin only; it deliberately does not install it
into a runtime or provide a hot-reload path.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.cli_custody_contracts import CliCustodyDTO, Identity
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    OperatorPolicyKeyBindingV1,
    OperatorPolicyOperation,
)
from chiplog.platform.authority_gate import AuthorityGate

_DOMAIN = b"chiplog.operator-policy-key-pin.v1\x00"
_MAX_PIN_BYTES = 65_536
_PIN_SUFFIX = ".operator-policy-key.json"


class OperatorPolicyPinError(RuntimeError):
    """The provisioned operator-policy key pin cannot be trusted."""


class OperatorPolicyKeyPinFileV1(CliCustodyDTO):
    """The exact, canonical on-disk operator-policy key pin."""

    schema_id: Literal["chiplog.operator-policy-key-pin.v1"] = "chiplog.operator-policy-key-pin.v1"
    tenant_id: Identity
    database_id: Identity
    operator_key_id: Identity
    policy_id: Identity
    allowed_operations: tuple[OperatorPolicyOperation, ...]
    status: Literal["ACTIVE", "REVOKED"]
    public_key: bytes = Field(min_length=32, max_length=32)


_FileMetadata = tuple[int, int, int, int, int, int, int, int]
_ParentMetadata = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class _ReadPinSource:
    raw: bytes
    file_metadata: _FileMetadata
    parent_metadata: _ParentMetadata


@dataclass(frozen=True)
class PinnedOperatorPolicyKey:
    """An immutable pin snapshot whose currentness is checked under its gate."""

    binding: OperatorPolicyKeyBindingV1
    _gate: AuthorityGate
    _path: Path
    _raw: bytes
    _file_metadata: _FileMetadata
    _parent_metadata: _ParentMetadata

    def assert_current(self) -> None:
        """Raise unless this exact source snapshot remains current under the gate."""
        self._gate.require_held()
        try:
            current = _read_pin_source(self._path)
        except OperatorPolicyPinError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise OperatorPolicyPinError(
                "operator policy key pin currentness check failed"
            ) from error
        if (
            current.raw != self._raw
            or current.file_metadata != self._file_metadata
            or current.parent_metadata != self._parent_metadata
        ):
            raise OperatorPolicyPinError("operator policy key pin source changed")


def load_operator_policy_key_pin(
    gate: AuthorityGate, *, tenant_id: str, database_id: str
) -> PinnedOperatorPolicyKey:
    """Load one exact provisioned key pin while ``gate`` is held by this thread."""
    gate.require_held()
    path = gate.database.with_suffix(gate.database.suffix + _PIN_SUFFIX)
    try:
        source = _read_pin_source(path)
        pin = OperatorPolicyKeyPinFileV1.model_validate_json(source.raw)
        if pin.canonical_bytes() != source.raw:
            raise OperatorPolicyPinError("operator policy key pin bytes are not canonical")
        if pin.tenant_id != tenant_id or pin.database_id != database_id:
            raise OperatorPolicyPinError(
                "operator policy key pin scope differs from requested scope"
            )
        digest = hashlib.sha256(source.raw).hexdigest()
        binding = OperatorPolicyKeyBindingV1(
            tenant_id=pin.tenant_id,
            database_id=pin.database_id,
            operator_key_id=pin.operator_key_id,
            policy_id=pin.policy_id,
            allowed_operations=pin.allowed_operations,
            status=pin.status,
            public_key=pin.public_key,
            ref=ExactHead(
                identity=pin.operator_key_id,
                head=hashlib.sha256(_DOMAIN + source.raw).hexdigest(),
                fingerprint=digest,
            ),
        )
        return PinnedOperatorPolicyKey(
            binding=binding,
            _gate=gate,
            _path=path,
            _raw=source.raw,
            _file_metadata=source.file_metadata,
            _parent_metadata=source.parent_metadata,
        )
    except OperatorPolicyPinError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise OperatorPolicyPinError("operator policy key pin is unavailable or invalid") from error


@contextmanager
def _opened_descriptor(descriptor: int) -> Iterator[int]:
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _read_pin_source(path: Path) -> _ReadPinSource:
    parent_before = _checked_parent(path.parent)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    with _opened_descriptor(descriptor):
        before = _checked_file_descriptor(path, descriptor)
        if before[5] > _MAX_PIN_BYTES:
            raise OperatorPolicyPinError("operator policy key pin exceeds the read bound")
        raw = os.read(descriptor, _MAX_PIN_BYTES + 1)
        if len(raw) > _MAX_PIN_BYTES:
            raise OperatorPolicyPinError("operator policy key pin exceeds the read bound")
        after = _checked_file_descriptor(path, descriptor)
        parent_after = _checked_parent(path.parent)
    if before != after or parent_before != parent_after:
        raise OperatorPolicyPinError("operator policy key pin changed during read")
    return _ReadPinSource(raw=raw, file_metadata=after, parent_metadata=parent_after)


def _checked_parent(parent: Path) -> _ParentMetadata:
    metadata = parent.lstat()
    if (
        parent.resolve(strict=True) != parent
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise OperatorPolicyPinError("operator policy key pin parent is unsafe")
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_ctime_ns,
    )


def _checked_file_descriptor(path: Path, descriptor: int) -> _FileMetadata:
    actual = os.fstat(descriptor)
    named = path.lstat()
    if (
        path.resolve(strict=True) != path
        or not stat.S_ISREG(actual.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or actual.st_nlink != 1
        or named.st_nlink != 1
        or stat.S_IMODE(actual.st_mode) != 0o600
        or stat.S_IMODE(named.st_mode) != 0o600
        or actual.st_uid != os.geteuid()
        or named.st_uid != os.geteuid()
        or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise OperatorPolicyPinError("operator policy key pin identity or permissions are unsafe")
    return (
        actual.st_dev,
        actual.st_ino,
        actual.st_mode,
        actual.st_uid,
        actual.st_nlink,
        actual.st_size,
        actual.st_mtime_ns,
        actual.st_ctime_ns,
    )


__all__ = [
    "OperatorPolicyKeyPinFileV1",
    "OperatorPolicyPinError",
    "PinnedOperatorPolicyKey",
    "load_operator_policy_key_pin",
]
