"""Composition facade for the loop-owned direct R14 terminal mapping."""

from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    CLASSIFIER_ID,
    CLASSIFIER_VERSION,
    MAPPING_ID,
    MAPPING_VERSION,
    DirectEffectTerminalClassificationV1,
    classify_direct_effect_terminal_v1,
)

__all__ = [
    "CLASSIFIER_ID",
    "CLASSIFIER_VERSION",
    "MAPPING_ID",
    "MAPPING_VERSION",
    "DirectEffectTerminalClassificationV1",
    "classify_direct_effect_terminal_v1",
]
