from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PrincipalId:
    value: str


@dataclass(frozen=True)
class PermissionScope:
    value: str
