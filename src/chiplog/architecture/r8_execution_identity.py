"""Explicit observed-source identity for current R8 descendant execution."""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_Mode = Literal["development", "release"]
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_mode: contextvars.ContextVar[_Mode] = contextvars.ContextVar(
    "chiplog_execution_identity_mode", default="development"
)


@dataclass(frozen=True)
class RuntimeSourceIdentity:
    mode: _Mode
    source_snapshot_digest: str

    def __post_init__(self) -> None:
        if self.mode not in ("development", "release") or not _DIGEST.fullmatch(
            self.source_snapshot_digest
        ):
            raise ValueError("runtime source identity is not canonical")


@dataclass(frozen=True)
class ResolvedRuntimeIdentity:
    source_identity: RuntimeSourceIdentity
    broker_version: str

    def __post_init__(self) -> None:
        prefix = (
            "r8-development-v1:" if self.source_identity.mode == "development" else "r8-broker-v1:"
        )
        if not self.broker_version.startswith(prefix) or not _DIGEST.fullmatch(
            self.broker_version.removeprefix(prefix)
        ):
            raise ValueError("runtime broker version does not bind the selected source mode")
        if self.source_identity.mode == "development" and self.broker_version != (
            prefix + self.source_identity.source_snapshot_digest
        ):
            raise ValueError("development broker version differs from selected source snapshot")


def snapshot_python_sources(root: Path) -> dict[str, str]:
    source = root / "src/chiplog"
    if not source.is_dir():
        raise ValueError("runtime source root is unavailable")
    return {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.rglob("*.py"))
    }


def source_snapshot_digest(root: Path) -> str:
    return snapshot_digest(snapshot_python_sources(root))


def snapshot_digest(snapshot: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@contextlib.contextmanager
def execution_identity(mode: _Mode = "development") -> Iterator[None]:
    if mode not in ("development", "release"):
        raise ValueError("unknown execution identity mode")
    token = _mode.set(mode)
    try:
        yield
    finally:
        _mode.reset(token)


def selected_execution_mode() -> _Mode:
    return _mode.get()


def resolve_runtime_identity(root: Path, *, mode: _Mode | None = None) -> ResolvedRuntimeIdentity:
    selected = selected_execution_mode() if mode is None else mode
    if selected not in ("development", "release"):
        raise ValueError("unknown execution identity mode")
    mapping = snapshot_python_sources(root)
    snapshot = snapshot_digest(mapping)
    source = RuntimeSourceIdentity(selected, snapshot)
    if selected == "development":
        return ResolvedRuntimeIdentity(source, f"r8-development-v1:{snapshot}")
    # Import locally: strict catalog validation is root-scoped and never trusts
    # the process-global catalog loaded for a different tree.
    from chiplog.verification.r8_surface import validated_catalog_broker_version

    return ResolvedRuntimeIdentity(source, validated_catalog_broker_version(root, mapping))


__all__ = [
    "ResolvedRuntimeIdentity",
    "RuntimeSourceIdentity",
    "execution_identity",
    "resolve_runtime_identity",
    "selected_execution_mode",
    "snapshot_digest",
    "snapshot_python_sources",
    "source_snapshot_digest",
]
