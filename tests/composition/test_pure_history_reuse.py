"""Pure byte reuse preserves failures, bounded storage and fresh DTO outputs."""

import pytest

from chiplog.composition._pure_bytes import reuse_exact_bytes
from chiplog.composition.r14_acceptance_v2_records import build_acceptance_envelope
from tests.support.acceptance_v2 import prepared_acceptance


def test_reuse_only_exact_successful_bounded_bytes() -> None:
    calls: list[bytes] = []

    @reuse_exact_bytes
    def compute(raw: bytes) -> bytes:
        calls.append(raw)
        if raw == b"bad":
            raise ValueError("invalid")
        return raw + b"!"

    assert compute(b"one") == compute(b"one") == b"one!"
    assert calls == [b"one"]
    for _ in range(2):
        with pytest.raises(ValueError, match="invalid"):
            compute(b"bad")
    assert compute(b"one") == b"one!"
    assert calls == [b"one", b"bad", b"bad"]
    assert compute(b"two") == b"two!"
    assert compute(b"one") == b"one!"
    assert calls[-2:] == [b"two", b"one"]
    large = b"x" * (4 * 1024 * 1024)
    assert compute(large) == compute(large) == large + b"!"
    assert calls[-2:] == [large, large]


def test_warm_acceptance_rechecks_changed_input_and_returns_fresh_objects() -> None:
    retained = prepared_acceptance()
    first = build_acceptance_envelope(retained)
    original = first.canonical_bytes()
    # Even arbitrary mutation of the returned object cannot poison stored bytes.
    object.__setattr__(first, "tenant_id", "poisoned")
    second = build_acceptance_envelope(retained)
    assert second is not first
    assert second.canonical_bytes() == original
    changed = retained.model_copy(
        update={
            "effects_request": retained.effects_request.model_copy(
                update={
                    "current": retained.effects_request.current.model_copy(
                        update={"command_fingerprint": "f" * 64}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError):
        build_acceptance_envelope(changed)
    assert build_acceptance_envelope(retained).canonical_bytes() == original
