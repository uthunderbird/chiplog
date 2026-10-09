"""Composition facade for the loop-owned pure R16 outcome classifier."""

from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    CLASSIFIER_ID as POLICY_ID,
)
from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    CLASSIFIER_VERSION as POLICY_VERSION,
)
from chiplog.capabilities.agent_loop.direct_effect_terminal_contracts import (
    RESULT_SCHEMA,
    RecoveredEffectStatusV1,
    translate_selected_outcome_v1,
)

__all__ = [
    "POLICY_ID",
    "POLICY_VERSION",
    "RESULT_SCHEMA",
    "RecoveredEffectStatusV1",
    "translate_selected_outcome_v1",
]
