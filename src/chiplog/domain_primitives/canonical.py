from __future__ import annotations

from dataclasses import dataclass

from .versions import ProducingVersions


@dataclass(frozen=True)
class CanonicalBytes:
    payload: bytes
    producing_versions: ProducingVersions


@dataclass(frozen=True)
class PreservedBytes:
    original: bytes
    producing_versions: ProducingVersions
