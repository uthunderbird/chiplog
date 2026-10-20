"""Historical native selection for an installed H1 V2 seal."""

from __future__ import annotations

import pytest

from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource


def test_selector_rejects_checkpoint_before_reading_runtime() -> None:
    with pytest.raises(ValueError, match="checkpoint"):
        H1V2RecoveryNativeSource._decode_v2_seal(
            b'{"h1_historical_checkpoint":{},"kind":"DECIDED"}'
        )
