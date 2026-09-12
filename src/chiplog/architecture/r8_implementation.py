"""Load the inert, audited R8 implementation identity catalog."""

from __future__ import annotations

import json
from pathlib import Path


def _load() -> dict[str, str]:
    path = Path(__file__).parents[1] / "inert_shared/r8-implementation-v1.json"
    value: object = json.loads(path.read_bytes())
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(digest, str) for key, digest in value.items()
    ):
        raise ValueError("R8 implementation catalog is not inert string data")
    return value


R8_IMPLEMENTATION_FILES = _load()
