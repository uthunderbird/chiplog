"""Shared builders extracted without changing their behavior."""

from pathlib import Path
from typing import Literal

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.composition.postgres_effects import (
    PostgresEffectsBinding,
)

ISSUER_KEY, RECEIPT_KEY = b"stable-channel-issuer", b"stable-provider-receipt"


def binding(directory: Path, *, with_effect: bool = False) -> PostgresEffectsBinding:
    scenario: Literal["LOST_RESPONSE_AFTER_EFFECT"] = "LOST_RESPONSE_AFTER_EFFECT"
    scenarios = (scenario,) * (2 if with_effect else 1)
    return PostgresEffectsBinding(
        HermeticEffectsProvider(
            receipt_key=RECEIPT_KEY,
            scenarios=scenarios,
            journal_path=directory / "transfers.jsonl",
            observation_journal_path=directory / "observations.jsonl",
        ),
        RECEIPT_KEY,
    )
