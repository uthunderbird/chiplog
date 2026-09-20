import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.live_contract import LiveModelBinding


def test_live_model_contract_defaults_and_unknown_provider() -> None:
    binding = LiveModelBinding(credential_identity="account-hash")
    assert (binding.model, binding.effort) == ("gpt-5.6-terra", "low")
    with pytest.raises(ValidationError):
        LiveModelBinding.model_validate(
            {"credential_identity": "account-hash", "provider": "hermetic-model.v1"}
        )
