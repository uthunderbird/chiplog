from __future__ import annotations

import pytest
from pydantic import ValidationError

from chiplog.platform.deployment_gate import DeploymentGateGeneration, DeploymentGateResult


def test_gate_contract_is_strict_and_has_no_third_permit_mode() -> None:
    with pytest.raises(ValidationError):
        DeploymentGateGeneration.model_validate(
            {"tenant_id": "tenant", "epoch": "epoch", "sequence": "1"}
        )
    with pytest.raises(ValidationError):
        DeploymentGateResult.model_validate(
            {"disposition": "PERMIT_DEVELOPMENT", "request_digest": "digest", "reason": "test"}
        )
