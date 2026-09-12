from __future__ import annotations

from dataclasses import is_dataclass
from typing import is_protocol

from chiplog.composition import OwnerGeneration, R7GraphBuilder, RuntimeGraphGeneration


def test_consumer_can_describe_r7_generation_through_public_contracts() -> None:
    assert is_protocol(R7GraphBuilder)
    assert OwnerGeneration(
        "planning",
        "pid:1",
        "generation-1",
        "session-1",
        ("planning",),
        ("provider",),
        ("target",),
        ("factory",),
        ("APP",),
    )
    assert is_dataclass(RuntimeGraphGeneration)
