"""Authority-gated immutable operator-grant key pin snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.cli_custody_contracts import CliCustodyDTO, Identity
from chiplog.capabilities.deployment_trust.operator_grant_command_verifier import (
    OperatorGrantKeyBindingV2,
    OperatorGrantOperation,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.operator_policy_key_pin import (
    OperatorPolicyPinError,
    _read_pin_source,
    _ReadPinSource,
)

_DOMAIN = b"chiplog.operator-grant-key-pin.v2\x00"
_PIN_SUFFIX = ".operator-grant-key.json"


class OperatorGrantPinError(RuntimeError):
    """The provisioned operator-grant key pin cannot be trusted."""


class OperatorGrantKeyPinFileV2(CliCustodyDTO):
    """The exact, canonical on-disk operator-grant key pin."""

    schema_id: Literal["chiplog.operator-grant-key-pin.v2"] = "chiplog.operator-grant-key-pin.v2"
    tenant_id: Identity
    database_id: Identity
    operator_key_id: Identity
    policy_id: Identity
    allowed_operations: tuple[OperatorGrantOperation, ...]
    status: Literal["ACTIVE", "REVOKED"]
    public_key: bytes = Field(min_length=32, max_length=32)


@dataclass(frozen=True)
class PinnedOperatorGrantKey:
    """An immutable pin snapshot whose currentness is checked under its gate."""

    binding: OperatorGrantKeyBindingV2
    _gate: AuthorityGate
    _path: Path
    _raw: bytes
    _file_metadata: tuple[int, int, int, int, int, int, int, int]
    _parent_metadata: tuple[int, int, int, int]

    def assert_current(self) -> None:
        """Raise unless this exact source snapshot remains current under the gate."""
        self._gate.require_held()
        current = _read_grant_pin_source(self._path)
        if (
            current.raw != self._raw
            or current.file_metadata != self._file_metadata
            or current.parent_metadata != self._parent_metadata
        ):
            raise OperatorGrantPinError("operator grant key pin source changed")


def load_operator_grant_key_pin(
    gate: AuthorityGate, *, tenant_id: str, database_id: str
) -> PinnedOperatorGrantKey:
    """Load one exact provisioned grant-key pin while ``gate`` is held by this thread."""
    gate.require_held()
    path = gate.database.with_suffix(gate.database.suffix + _PIN_SUFFIX)
    try:
        source = _read_grant_pin_source(path)
        pin = OperatorGrantKeyPinFileV2.model_validate_json(source.raw)
        if pin.canonical_bytes() != source.raw:
            raise OperatorGrantPinError("operator grant key pin bytes are not canonical")
        if pin.tenant_id != tenant_id or pin.database_id != database_id:
            raise OperatorGrantPinError("operator grant key pin scope differs from requested scope")
        digest = hashlib.sha256(source.raw).hexdigest()
        binding = OperatorGrantKeyBindingV2(
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
        return PinnedOperatorGrantKey(
            binding=binding,
            _gate=gate,
            _path=path,
            _raw=source.raw,
            _file_metadata=source.file_metadata,
            _parent_metadata=source.parent_metadata,
        )
    except OperatorGrantPinError:
        raise
    except (OperatorPolicyPinError, OSError, TypeError, ValueError) as error:
        raise OperatorGrantPinError("operator grant key pin is unavailable or invalid") from error


def _read_grant_pin_source(path: Path) -> _ReadPinSource:
    try:
        return _read_pin_source(path)
    except OperatorPolicyPinError as error:
        raise OperatorGrantPinError("operator grant key pin is unavailable or unsafe") from error


__all__ = [
    "OperatorGrantKeyPinFileV2",
    "OperatorGrantPinError",
    "PinnedOperatorGrantKey",
    "load_operator_grant_key_pin",
]
