"""Bounded reuse of pure byte computations; never caches a live read or authority."""

from collections.abc import Callable
from functools import wraps

_PAIR_LIMIT = 4 * 1024 * 1024


def reuse_exact_bytes(compute: Callable[[bytes], bytes]) -> Callable[[bytes], bytes]:
    """Keep one successful immutable pair; callers decode fresh public objects."""
    cached: tuple[bytes, bytes] | None = None

    @wraps(compute)
    def evaluate(raw: bytes) -> bytes:
        nonlocal cached
        pair = cached
        if pair is not None and raw == pair[0]:
            return pair[1]
        result = compute(raw)
        if len(raw) + len(result) <= _PAIR_LIMIT:
            cached = (raw, result)
        return result

    return evaluate
