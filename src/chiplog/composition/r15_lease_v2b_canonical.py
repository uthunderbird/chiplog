"""Frozen historical V2b canonicalization helpers; no live trust imports."""

import hashlib
import json


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
