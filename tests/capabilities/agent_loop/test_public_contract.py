"""Consumer-side validation through the exported capability boundary."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, Complete, Continue, ToolCall


def test_public_response_contract_separates_tools_and_completion() -> None:
    value = Continue(
        kind="Continue",
        tool_calls=(ToolCall(call_id="c", tool="propose_planning", text="Purpose"),),
    )
    assert Continue.model_validate_json(value.canonical_bytes()) == value
    with pytest.raises(ValidationError):
        Complete.model_validate_json(value.canonical_bytes())
    with pytest.raises(ValidationError):
        Continue.model_validate_json(b'{"kind":"Continue","tool_calls":[],"deliveries":[]}')


def test_public_policy_rejects_invalid_limits() -> None:
    assert BudgetPolicy(kind="NO_POLICY").kind == "NO_POLICY"
    with pytest.raises(ValidationError):
        BudgetPolicy(max_turns=0)
    with pytest.raises(ValidationError):
        BudgetPolicy.model_validate({"max_turns": True})
